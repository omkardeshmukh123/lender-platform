# backend/api/core/gemini.py
"""
Gemini chat client for the lender chatbot.
Two-pass RAG pipeline:
  1. parse_intent()  — classify + extract entities (no answer)
  2. generate_grounded_answer() — answer strictly from DB records
"""
from __future__ import annotations

import json
import logging
import os
from collections import Counter
from typing import Iterator, Optional

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pass 1 — intent classification only
# ---------------------------------------------------------------------------

_INTENT_SYSTEM_PROMPT = """\
You are an intent classifier for MITRAM360, an Indian lender discovery platform.
Your ONLY job is to classify the user message and extract named entities.
Do NOT generate answers. Do NOT use your knowledge about lenders.

Intents:
- "filter"        — user wants to search/list lenders by criteria (loan type, state, AUM, company type, sector, etc.)
- "compare"       — user wants to compare 2–3 specific named lenders side-by-side
- "lender_detail" — user asks about a single specific named lender (HQ, location, contact, products, AUM, website, etc.)
- "concept"       — definitional/educational question about lending terms, types, or regulations
- "qa"            — factual question about lenders in India with no specific lender named and no clear filter criteria
- "greeting"      — greetings, thanks, small talk, or questions about the assistant's capabilities
- "out_of_scope"  — completely unrelated to lending/finance in India

VALID filter values (use exact strings only):
loan_type: MSME Loan, Personal Loan, Home Loan, Business Loan, Vehicle Loan, Gold Loan,
           Education Loan, Micro Loan, Loan Against Property, Working Capital,
           Agriculture Loan, EV Loan, Two Wheeler Loan, Rural Loan, Microfinance,
           Supply Chain Finance, Consumer Durable Loan, Credit Card
company_type: NBFC, Private Bank, PSU Bank, Foreign Bank, Cooperative Bank, NBFC-MFI, Small Finance Bank
aum_category: Micro, Small, Mid, Large
operating_intensity: Pan India, Regional, Single State
business_sector: MSME, Housing, Gold, Vehicle, Microfinance, Agriculture, Retail
sort_by: aum_crores, established_year, employee_count, quality_score, company_name
sort_dir: asc, desc

RULES:
- "filter"        → populate filters; omit fields not mentioned.
- "compare"       → put lender names in compare_names (max 3).
- "lender_detail" → put the single lender name in detail_names.
- "concept"       → leave all fields empty.
- "qa"            → leave filters, compare_names, and detail_names empty.
- "greeting"      → leave all fields empty.
- "out_of_scope"  → leave all fields empty.

PRONOUN RESOLUTION: If the message contains vague references ("that one", "the first one",
"it", "those", "them", "the second"), look at recent conversation history to resolve which
lender is meant, then classify accordingly (e.g. resolve to lender_detail or compare).

EXAMPLES:
"What NBFCs are in Gujarat?"                    → filter, {company_type:["NBFC"], state:"Gujarat"}
"Who offers agriculture loans?"                 → filter, {loan_type:["Agriculture Loan"]}
"Show me large AUM lenders in Mumbai"           → filter, {aum_category:["Large"], state:"Maharashtra"}
"Which banks operate pan India?"                → filter, {pan_india:true}
"NBFCs focused on agriculture sector"           → filter, {company_type:["NBFC"], business_sector:["Agriculture"]}
"Regional lenders in Rajasthan"                 → filter, {state:"Rajasthan", operating_intensity:["Regional"]}
"Compare Bajaj and Muthoot"                     → compare, compare_names:["Bajaj Finance","Muthoot Finance"]
"Tell me about HDFC Bank"                       → lender_detail, detail_names:["HDFC Bank"]
"What is an NBFC?" / "Difference between NBFC-MFI and bank?" → concept
"Which lenders have highest AUM?"               → qa
"Hello" / "Thanks" / "What can you do?"        → greeting
"Who won the cricket match?"                    → out_of_scope
"""

_INTENT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "intent": {
            "type": "STRING",
            "enum": ["filter", "compare", "lender_detail", "concept", "qa", "greeting", "out_of_scope"],
        },
        "filters": {
            "type": "OBJECT",
            "properties": {
                "q":                   {"type": "STRING"},
                "loan_type":           {"type": "ARRAY", "items": {"type": "STRING"}},
                "state":               {"type": "STRING"},
                "company_type":        {"type": "ARRAY", "items": {"type": "STRING"}},
                "aum_category":        {"type": "ARRAY", "items": {"type": "STRING"}},
                "aum_min":             {"type": "NUMBER"},
                "aum_max":             {"type": "NUMBER"},
                "pan_india":           {"type": "BOOLEAN"},
                "is_listed":           {"type": "BOOLEAN"},
                "operating_intensity": {"type": "ARRAY", "items": {"type": "STRING"}},
                "business_sector":     {"type": "ARRAY", "items": {"type": "STRING"}},
                "sort_by":             {"type": "STRING"},
                "sort_dir":            {"type": "STRING", "enum": ["asc", "desc"]},
            },
        },
        "compare_names": {"type": "ARRAY", "items": {"type": "STRING"}},
        "detail_names":  {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["intent"],
}

# ---------------------------------------------------------------------------
# Pass 2 — grounded answer generation
# ---------------------------------------------------------------------------

_OUT_OF_SCOPE_REPLY = (
    "I only answer questions about lenders, loan products, NBFCs, and interest rates. "
    "For other topics, please consult the relevant resource."
)

_ANSWER_SYSTEM_PROMPT = """\
You are the AI assistant for MITRAM360, an Indian lender discovery platform.
You ONLY answer questions about lenders, loan products, NBFCs, and interest rates. \
For any other topic, respond with: \
"I only answer questions about lenders, loan products, NBFCs, and interest rates. \
For other topics, please consult the relevant resource."
Write like a knowledgeable colleague — warm, direct, and genuinely useful. Never sound like a data dump.

TONE:
- Start answers immediately. No "Sure!", "Of course!", or restating the question.
- Write in natural flowing sentences. Never output raw key:value pairs.
- Be concise: 2-4 sentences for detail/qa, a short paragraph for filter summaries.

FORMATTING RULES:
1. Answer ONLY from the database records in the prompt — unless intent is "concept".
2. NEVER make factual claims about specific lenders from training knowledge.
3. If a field is null/missing: say "not listed" (brief, not "not available in our database").
4. Format AUM as ₹X,XXX Cr — Indian comma format, no decimals, ₹ prefix. Example: ₹92,164 Cr not 92164.0.
5. State filter = operating coverage (pan_india OR operating_states), NOT headquarters.
   Never say "no lenders headquartered in X" — the results may be pan-India lenders operating there.
6. For compare: one bullet per lender — "**[Name]** — [Type], ₹X,XXX Cr, HQ [City], [key segments]"
   Never use flowing prose for compare. Always use bullets.
7. For filter results: open with count + context, name top 3 by AUM inline with ₹ and city.
   Example: "Found 20 NBFCs operating in Maharashtra. Biggest: **IIFL Finance** (₹92,164 Cr, Mumbai),
   **Kotak Mahindra Prime** (₹30,000 Cr, Mumbai), and **SBFC Finance** (₹7,200 Cr)."
8. For empty/not-found: be specific — "I couldn't find [exact name] in our database yet —
   they may not be listed. Try [concrete alternative]."
9. End every filter or qa answer with a contextual suggestion using ACTUAL lender names from the results:
   _"Want details? Try: 'Tell me about [Name1]' or 'Compare [Name1] and [Name2]'"_
10. If the user writes in Hindi or Hinglish, reply in Hinglish.
"""

_CONCEPT_SYSTEM_PROMPT = """\
You are a knowledgeable assistant for MITRAM360, an Indian lender discovery platform.
You ONLY answer questions about lenders, loan products, NBFCs, and interest rates. \
For any other topic, respond with: \
"I only answer questions about lenders, loan products, NBFCs, and interest rates. \
For other topics, please consult the relevant resource."
Answer the user's conceptual question about Indian lending, financial regulations, or lender types
using accurate general knowledge. Be concise (3–5 sentences). Use simple, jargon-free language.
Do not make claims about specific lenders in the MITRAM360 database.
End with one practical tip relevant to lender discovery.
If the user writes in Hindi or Hinglish, reply in Hinglish.
"""


class GeminiChatClient:
    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required for the chat feature")
        self._client = genai.Client(api_key=api_key)
        self._model = "gemini-2.5-flash"
        logger.info("GeminiChatClient initialized (model=%s)", self._model)

    # ------------------------------------------------------------------
    # Pass 1
    # ------------------------------------------------------------------

    def parse_intent(self, message: str, history: list[dict]) -> dict:
        """Classify intent and extract entities. Returns no answer text."""
        contents = self._build_contents(history[-(2 * 2):], message)
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=_INTENT_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=_INTENT_SCHEMA,
                    temperature=0.1,
                    max_output_tokens=256,
                ),
            )
            raw = response.text
        except Exception as exc:
            logger.error("Gemini intent parse error: %s", exc)
            raise

        if not raw:
            logger.warning("Gemini returned empty response during intent parsing")
            return {"intent": "qa", "filters": {}, "compare_names": [], "detail_names": []}

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.error("Gemini non-JSON intent response: %s | raw=%s", exc, str(raw)[:200])
            return {"intent": "qa", "filters": {}, "compare_names": [], "detail_names": []}

        data.setdefault("filters", {})
        data.setdefault("compare_names", [])
        data.setdefault("detail_names", [])
        return data

    # ------------------------------------------------------------------
    # Pass 2
    # ------------------------------------------------------------------

    def generate_grounded_answer(
        self,
        question: str,
        intent: str,
        lenders: list[dict],
        history: list[dict],
        note: str = "",
    ) -> str:
        """Generate a natural-language answer from the provided DB records."""

        # --- Static / no-DB intents ---
        if intent == "greeting":
            return (
                "Hi! I'm the MITRAM360 AI assistant. I can help you:\n"
                "- **Find lenders** by loan type, state, AUM, sector, or company type\n"
                "- **Compare** two or three lenders side-by-side\n"
                "- **Look up details** for a specific lender (HQ, contact, products)\n"
                "- **Explain** lending concepts and lender types\n\n"
                "Try: \"Show NBFCs in Maharashtra\" or \"What is a Small Finance Bank?\""
            )

        if intent == "out_of_scope":
            return _OUT_OF_SCOPE_REPLY

        # --- Concept: general knowledge, not DB ---
        if intent == "concept":
            contents = self._build_contents(history[-(4 * 2):], question)
            try:
                response = self._client.models.generate_content(
                    model=self._model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=_CONCEPT_SYSTEM_PROMPT,
                        temperature=0.3,
                        max_output_tokens=400,
                    ),
                )
                text = response.text
                return text if text else "I couldn't generate an answer. Please try again."
            except Exception as exc:
                logger.error("Gemini concept answer error: %s", exc)
                raise

        # --- Grounded intents ---
        if not lenders:
            if intent in ("compare", "lender_detail"):
                return (
                    "I couldn't find that lender in our database yet — they may not be listed. "
                    "Try browsing our full catalogue or compare other lenders."
                )
            if intent == "qa":
                return (
                    "I couldn't find relevant lenders for that question. "
                    "Try asking about a specific lender, loan type, or state — "
                    "e.g. \"Show NBFCs in Maharashtra\" or \"Tell me about Bajaj Finance\"."
                )
            return "No results found. Try broadening your search — remove a filter or try a different state."

        # Extract top names for contextual suggestions in the prompt
        top_names = [
            l.get("company_name") or l.get("name", "")
            for l in lenders[:3]
            if l.get("company_name") or l.get("name")
        ]
        name_hint = f"\nTop lender names for your suggestions: {', '.join(top_names)}" if top_names else ""

        if intent == "filter":
            slim   = [_slim_record(l) for l in lenders]
            total  = len(lenders)
            type_counts  = Counter(l.get("company_type") for l in lenders if l.get("company_type"))
            state_counts = Counter(l.get("hq_state")     for l in lenders if l.get("hq_state"))
            top_types    = ", ".join(f"{t}({c})" for t, c in type_counts.most_common(4))
            top_states   = ", ".join(f"{s}({c})" for s, c in state_counts.most_common(4))
            prefix = (
                f"{note + chr(10) if note else ''}"
                f"Stats — Total: {total}, By type: {top_types}, By HQ state: {top_states}.{name_hint}\n\n"
            )
            context = slim
        else:
            prefix  = f"{note}\n\n{name_hint}\n\n" if (note or name_hint) else ""
            context = lenders

        records_json = json.dumps(context, indent=2, default=str)
        prompt = (
            f"{prefix}"
            f"Database records:\n{records_json}\n\n"
            f"User question: {question}\n\n"
            "Answer in natural language using ONLY the records above. Follow all tone and formatting rules."
        )

        history_contents = self._build_contents(history[-(6 * 2):], None)
        contents = history_contents + [
            types.Content(role="user", parts=[types.Part(text=prompt)])
        ]

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=_ANSWER_SYSTEM_PROMPT,
                    temperature=0.3,
                    max_output_tokens=800,
                ),
            )
            text = response.text
            if not text:
                logger.warning("Gemini returned empty answer response")
                return "I couldn't generate an answer. Please try again."
            return text
        except Exception as exc:
            logger.error("Gemini answer generation error: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Pass 2 — streaming variant
    # ------------------------------------------------------------------

    def generate_grounded_answer_stream(
        self,
        question: str,
        intent: str,
        lenders: list[dict],
        history: list[dict],
        note: str = "",
    ) -> Iterator[str]:
        """Streaming version of generate_grounded_answer. Yields text tokens."""
        if intent == "greeting":
            yield (
                "Hi! I'm the MITRAM360 AI assistant. I can help you:\n"
                "- **Find lenders** by loan type, state, AUM, sector, or company type\n"
                "- **Compare** two or three lenders side-by-side\n"
                "- **Look up details** for a specific lender (HQ, contact, products)\n"
                "- **Explain** lending concepts and lender types\n\n"
                "Try: \"Show NBFCs in Maharashtra\" or \"What is a Small Finance Bank?\""
            )
            return

        if intent == "out_of_scope":
            yield _OUT_OF_SCOPE_REPLY
            return

        if intent == "concept":
            contents = self._build_contents(history[-(4 * 2):], question)
            stream = self._client.models.generate_content_stream(
                model=self._model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=_CONCEPT_SYSTEM_PROMPT,
                    temperature=0.3,
                    max_output_tokens=400,
                ),
            )
            for chunk in stream:
                if chunk.text:
                    yield chunk.text
            return

        if not lenders:
            if intent in ("compare", "lender_detail"):
                yield (
                    "I couldn't find that lender in our database yet — they may not be listed. "
                    "Try browsing our full catalogue or compare other lenders."
                )
            elif intent == "qa":
                yield (
                    "I couldn't find relevant lenders for that question. "
                    "Try asking about a specific lender, loan type, or state — "
                    "e.g. \"Show NBFCs in Maharashtra\" or \"Tell me about Bajaj Finance\"."
                )
            else:
                yield "No results found. Try broadening your search — remove a filter or try a different state."
            return

        top_names = [
            l.get("company_name") or l.get("name", "")
            for l in lenders[:3]
            if l.get("company_name") or l.get("name")
        ]
        name_hint = f"\nTop lender names for your suggestions: {', '.join(top_names)}" if top_names else ""

        if intent == "filter":
            slim         = [_slim_record(l) for l in lenders]
            total        = len(lenders)
            type_counts  = Counter(l.get("company_type") for l in lenders if l.get("company_type"))
            state_counts = Counter(l.get("hq_state")     for l in lenders if l.get("hq_state"))
            top_types    = ", ".join(f"{t}({c})" for t, c in type_counts.most_common(4))
            top_states   = ", ".join(f"{s}({c})" for s, c in state_counts.most_common(4))
            prefix = (
                f"{note + chr(10) if note else ''}"
                f"Stats — Total: {total}, By type: {top_types}, By HQ state: {top_states}.{name_hint}\n\n"
            )
            context = slim
        else:
            prefix  = f"{note}\n\n{name_hint}\n\n" if (note or name_hint) else ""
            context = lenders

        records_json = json.dumps(context, indent=2, default=str)
        prompt = (
            f"{prefix}"
            f"Database records:\n{records_json}\n\n"
            f"User question: {question}\n\n"
            "Answer in natural language using ONLY the records above. Follow all tone and formatting rules."
        )

        history_contents = self._build_contents(history[-(6 * 2):], None)
        contents = history_contents + [
            types.Content(role="user", parts=[types.Part(text=prompt)])
        ]

        stream = self._client.models.generate_content_stream(
            model=self._model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=_ANSWER_SYSTEM_PROMPT,
                temperature=0.3,
                max_output_tokens=800,
            ),
        )
        for chunk in stream:
            if chunk.text:
                yield chunk.text

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _build_contents(self, history: list[dict], message: str | None) -> list:
        contents = []
        for h in history:
            role  = h.get("role", "user")
            parts = h.get("parts", [])
            text  = parts[0] if parts else ""
            contents.append(
                types.Content(role=role, parts=[types.Part(text=str(text))])
            )
        if message is not None:
            contents.append(
                types.Content(role="user", parts=[types.Part(text=message)])
            )
        return contents


def _slim_record(l: dict) -> dict:
    return {
        "name":                l.get("company_name"),
        "type":                l.get("company_type"),
        "rbi_category":        l.get("rbi_category"),
        "aum_crores":          l.get("aum_crores"),
        "hq_state":            l.get("hq_state"),
        "hq_location":         l.get("hq_location"),
        "pan_india":           l.get("pan_india"),
        "loan_segments":       l.get("primary_loan_segments"),
        "operating_intensity": l.get("operating_intensity"),
        "business_sector":     l.get("business_sector"),
        "established":         l.get("established_year"),
        "employees":           l.get("employee_count"),
        "is_listed":           l.get("is_listed"),
        "quality_score":       l.get("quality_score"),
        "website":             l.get("website"),
        "phone":               l.get("phone"),
        "email":               l.get("email"),
    }


_client: Optional[GeminiChatClient] = None


def get_gemini_client() -> GeminiChatClient:
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY", "")
        _client = GeminiChatClient(api_key)
    return _client
