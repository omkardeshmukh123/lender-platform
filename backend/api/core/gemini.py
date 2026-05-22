# backend/api/core/gemini.py
"""
Gemini chat client for the lender chatbot.
Two-pass RAG pipeline:
  1. parse_intent()  — classify + extract entities (no answer, thinking disabled)
  2. generate_grounded_answer() — answer strictly from DB records
"""
from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from collections import Counter
from typing import Iterator, Optional

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Thinking config helper — gracefully absent on older google-genai builds
# ---------------------------------------------------------------------------

def _thinking(budget: int) -> dict:
    """Return a thinking_config kwarg dict if ThinkingConfig is available."""
    try:
        return {"thinking_config": types.ThinkingConfig(thinking_budget=budget)}
    except AttributeError:
        return {}


# ---------------------------------------------------------------------------
# Transient-error retry wrapper (sync — used inside run_in_executor threads)
# ---------------------------------------------------------------------------

def _call_with_retry(fn, *, attempts: int = 2, delay: float = 2.0, label: str = ""):
    """
    Call fn() and retry up to `attempts` times on transient Gemini errors.
    Retries on: 429 rate-limit, 5xx server errors, connection/timeout failures.
    Raises the last exception if all attempts fail.
    """
    last_exc: Exception | None = None
    current_delay = delay
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            err_str = str(exc).lower()
            is_transient = (
                "429" in err_str or "rate limit" in err_str or
                "500" in err_str or "503" in err_str or "504" in err_str or
                "overloaded" in err_str or
                "connection" in err_str or "timeout" in err_str or
                isinstance(exc, (ConnectionError, TimeoutError))
            )
            if not is_transient or attempt == attempts:
                raise
            jitter = current_delay * 0.25 * (2 * random.random() - 1)
            wait = max(0.5, current_delay + jitter)
            logger.warning(
                "Gemini %s attempt %d/%d failed (%s: %s). Retrying in %.1fs",
                label, attempt, attempts, type(exc).__name__, exc, wait,
            )
            time.sleep(wait)
            current_delay *= 2
    raise last_exc  # type: ignore[misc]


def _retried_stream(fn, *, attempts: int = 2, delay: float = 2.0, label: str = ""):
    """
    Streaming retry: restart the full stream if it errors before any tokens are yielded.
    Once tokens are flowing, errors propagate immediately (can't restart mid-output).
    """
    last_exc: Exception | None = None
    current_delay = delay
    for attempt in range(1, attempts + 1):
        yielded = False
        try:
            for chunk in fn():
                yielded = True
                yield chunk
            return
        except Exception as exc:
            if yielded:
                raise
            last_exc = exc
            err_str = str(exc).lower()
            is_transient = (
                "429" in err_str or "rate limit" in err_str or
                "500" in err_str or "503" in err_str or "504" in err_str or
                "overloaded" in err_str or
                "connection" in err_str or "timeout" in err_str or
                isinstance(exc, (ConnectionError, TimeoutError))
            )
            if not is_transient or attempt == attempts:
                raise
            jitter = current_delay * 0.25 * (2 * random.random() - 1)
            wait = max(0.5, current_delay + jitter)
            logger.warning(
                "Gemini %s stream attempt %d/%d failed (%s: %s). Retrying in %.1fs",
                label, attempt, attempts, type(exc).__name__, exc, wait,
            )
            time.sleep(wait)
            current_delay *= 2
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Pass 1 — intent classification only
# ---------------------------------------------------------------------------

_INTENT_SYSTEM_PROMPT = """\
You are an intent classifier for MITRAM360, an Indian lender discovery platform.
Your ONLY job is to classify the user message and extract named entities.
Do NOT generate answers. Do NOT use your knowledge about lenders.

Intents:
- "filter"        — user wants to search/list/find lenders by any criteria (loan type, state/city, AUM, company type, sector, etc.)
- "compare"       — user wants to compare 2–3 specific named lenders side-by-side
- "lender_detail" — user asks about a single specific named lender (HQ, contact, products, AUM, website, etc.)
- "concept"       — definitional/educational question about ANY financial/lending term, regulation, or process
- "qa"            — factual question about lenders in India where no filter dimension is clearly stated
- "greeting"      — greetings, thanks, small talk, or questions about the assistant's capabilities
- "out_of_scope"  — completely unrelated to lending, finance, banking, or credit in India

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
- "filter"   → When user says which/who/list/show/find + any loan/lender criteria, ALWAYS use filter.
               Populate filters with what was mentioned; omit fields not mentioned.
               ALSO use "filter" when the user's message is ONLY a loan type name (e.g. "Gold loan",
               "Vehicle loan", "Home loan") — treat it as "find lenders for that product".
               ALSO use "filter" for superlative/ranking queries ("largest", "biggest", "top X by AUM")
               → set sort_by:"aum_crores", sort_dir:"desc" and any relevant company_type filter.
- "compare"  → put lender names in compare_names (max 3). Use for "vs", "versus", "compare",
               "difference between X and Y", "X compared to Y".
- "lender_detail" → put the single lender name in detail_names.
- "concept"  → ANY question about what a financial term means, how a regulation works, how a product
               works (EMI, NPA, CIBIL, KYC, PSL, SARFAESI, co-lending, MCLR, repo rate, etc.).
               Leave all filter/name fields empty.
- "qa"       → Only use when no filter dimension and no specific lender is mentioned.
- "greeting" / "out_of_scope" → leave all fields empty.
- NEVER classify a lending-related query as "out_of_scope". If it mentions any loan, lender, NBFC,
  bank, credit, or finance topic, use "filter", "qa", or "concept" instead.
- aum_min / aum_max are in CRORES — use the number exactly as stated. "5000 crores" → aum_min:5000. NEVER multiply or convert.

PRONOUN RESOLUTION: If the message contains vague references ("that one", "the first one",
"it", "those", "them", "the second"), look at recent conversation history to resolve which
lender is meant, then classify accordingly (e.g. resolve to lender_detail or compare).

LENDER ABBREVIATION EXPANSION: Always expand to full registered names in compare_names/detail_names:
- SBI → State Bank of India
- PNB → Punjab National Bank
- BOB → Bank of Baroda
- BOI → Bank of India
- HDFC → HDFC Bank
- ICICI → ICICI Bank
- IDBI → IDBI Bank
- UCO → UCO Bank
- OBC → Oriental Bank of Commerce
- LIC HFL / LIC Housing → LIC Housing Finance
- Canara → Canara Bank
- Union Bank / UBI → Union Bank of India
- Axis → Axis Bank
- Kotak → Kotak Mahindra Bank
- Yes → Yes Bank
- IndusInd → IndusInd Bank
- Bajaj → Bajaj Finance
- Muthoot → Muthoot Finance
- Manappuram → Manappuram Finance
- Shriram → Shriram Finance
- IDFC / IDFC First → IDFC First Bank
- AU / AU SFB / AU Bank → AU Small Finance Bank
- Bandhan → Bandhan Bank
- IIFL → IIFL Finance
- RBL → RBL Bank
- Jana → Jana Small Finance Bank
- Ujjivan → Ujjivan Small Finance Bank
- Equitas → Equitas Small Finance Bank
- Suryoday → Suryoday Small Finance Bank
- Mahindra Finance / MMFSL → Mahindra & Mahindra Financial Services

COMPANY TYPE SYNONYMS — map to exact VALID company_type values:
- SFB / small finance / small finance bank → Small Finance Bank
- MFI / microfinance institution / micro finance → NBFC-MFI
- UCB / urban cooperative / urban co-op → Cooperative Bank
- cooperative / co-op / coop / credit society → Cooperative Bank
- nationalized bank / govt bank / public sector bank / PSB → PSU Bank
- private sector bank / new gen bank → Private Bank
- foreign bank / international bank / overseas bank / MNC bank → Foreign Bank
- banks / bank (generic, not further qualified, e.g. "largest banks", "top banks", "banks in X") → ["Private Bank", "PSU Bank", "Foreign Bank"]

MULTI-FILTER RULE: When the user mentions MULTIPLE filter criteria, you MUST include ALL of them in filters.
NEVER drop a dimension. Examples of combined filters:
- "SFBs pan India" → BOTH company_type:["Small Finance Bank"] AND pan_india:true
- "NBFCs pan India" → BOTH company_type:["NBFC"] AND pan_india:true
- "Large NBFCs in Maharashtra" → company_type:["NBFC"] AND aum_category:["Large"] AND state:"Maharashtra"

LOAN TYPE SYNONYMS — map to nearest VALID loan_type value:
- pre-owned car / used car / second hand car / old car / pre-owned vehicle → Vehicle Loan
- car loan / auto loan / four-wheeler / 4-wheeler / automobile loan → Vehicle Loan
- commercial vehicle / truck / bus / fleet finance → Vehicle Loan
- bike loan / scooter loan / motorbike / two-wheeler → Two Wheeler Loan
- electric vehicle / EV loan / electric car → EV Loan
- housing loan / house loan / home purchase / home finance / residential loan → Home Loan
- affordable housing / LIG / EWS housing / construction finance → Home Loan
- LAP / loan against property / mortgage / property loan → Loan Against Property
- SME loan / small business / startup loan → Business Loan
- MSME finance / Mudra / PM Mudra / PMMY / micro enterprise → MSME Loan
- JLG / joint liability / SHG loan / MFI loan / PM SVANidhi / street vendor → Microfinance
- tractor loan / kisan loan / crop loan / farm loan / agri finance / Kisan Credit Card / KCC → Agriculture Loan
- supply chain / channel finance / dealer finance / vendor finance / invoice discounting → Supply Chain Finance
- consumer loan / consumer goods / white goods / electronics loan / durables → Consumer Durable Loan
- working capital / overdraft / OD / cash credit / CC limit / revolving credit → Working Capital
- jewel loan / gold ornament / ornament loan / gold jewellery loan → Gold Loan
- education finance / study loan / student loan / higher education → Education Loan
- rural finance / village loan / grameen loan → Rural Loan
- micro credit / micro enterprise loan / nano credit → Micro Loan
- credit card / charge card / prepaid card → Credit Card
- salary loan / payroll loan / payday loan / salary advance / instant salary → Personal Loan
- instant loan / cash loan / emergency loan / quick loan / short term personal → Personal Loan
- flexi loan / flex loan / revolving credit line / dropline OD / flexi credit → Working Capital
- HCV loan / LCV loan / commercial vehicle loan / transport vehicle / fleet vehicle → Vehicle Loan

CITY → STATE MAPPING — when user mentions a city, always convert to the correct state in filters.state:
- Mumbai / Navi Mumbai / Thane / Nagpur / Pune / Nashik → Maharashtra
- Delhi / New Delhi / Gurugram / Gurgaon / Noida / Faridabad / NCR → Delhi
- Bengaluru / Bangalore → Karnataka
- Chennai / Madras / Coimbatore / Madurai / Trichy → Tamil Nadu
- Hyderabad / Secunderabad → Telangana
- Kolkata / Calcutta → West Bengal
- Ahmedabad / Surat / Vadodara / Baroda / Rajkot → Gujarat
- Jaipur / Jodhpur / Udaipur → Rajasthan
- Lucknow / Kanpur / Agra / Varanasi / Allahabad / Prayagraj → Uttar Pradesh
- Bhopal / Indore → Madhya Pradesh
- Kochi / Thiruvananthapuram / Kozhikode / Calicut → Kerala
- Patna → Bihar
- Bhubaneswar / Cuttack → Odisha
- Visakhapatnam / Vizag / Vijayawada → Andhra Pradesh
- Raipur → Chhattisgarh
- Ranchi → Jharkhand
- Guwahati → Assam
- Dehradun → Uttarakhand
- Srinagar / Jammu → Jammu & Kashmir
- Chandigarh → Chandigarh
- Pondicherry / Puducherry → Puducherry
- Mysore / Mysuru / Mangalore / Udupi / Hubli / Belagavi / Belgaum / Dharwad → Karnataka
- Amritsar / Ludhiana / Jalandhar / Patiala → Punjab
- Shimla → Himachal Pradesh

EXAMPLES:
"What NBFCs are in Gujarat?"                        → filter, {company_type:["NBFC"], state:"Gujarat"}
"Show lenders in Mumbai"                            → filter, {state:"Maharashtra"}
"SFBs operating in UP"                              → filter, {company_type:["Small Finance Bank"], state:"Uttar Pradesh"}
"Nationalized banks offering MSME loans"            → filter, {company_type:["PSU Bank"], loan_type:["MSME Loan"]}
"MFI lenders in Bihar"                              → filter, {company_type:["NBFC-MFI"], state:"Bihar"}
"Cooperative banks in Pune"                         → filter, {company_type:["Cooperative Bank"], state:"Maharashtra"}
"Who offers agriculture loans?"                     → filter, {loan_type:["Agriculture Loan"]}
"Pre owned car loan lenders"                        → filter, {loan_type:["Vehicle Loan"]}
"Pre owned car loan"                                → filter, {loan_type:["Vehicle Loan"]}
"Used car loan"                                     → filter, {loan_type:["Vehicle Loan"]}
"Jewel loan NBFCs"                                  → filter, {loan_type:["Gold Loan"]}
"Mudra loan lenders"                                → filter, {loan_type:["MSME Loan"]}
"Show me large AUM lenders in Hyderabad"            → filter, {aum_category:["Large"], state:"Telangana"}
"Which banks operate pan India?"                    → filter, {company_type:["Private Bank","PSU Bank","Foreign Bank"], pan_india:true}
"SFBs operating pan India"                          → filter, {company_type:["Small Finance Bank"], pan_india:true}
"Small Finance Banks across India"                  → filter, {company_type:["Small Finance Bank"], pan_india:true}
"NBFCs with pan India presence"                     → filter, {company_type:["NBFC"], pan_india:true}
"NBFCs focused on agriculture sector"               → filter, {company_type:["NBFC"], business_sector:["Agriculture"]}
"Regional lenders in Rajasthan"                     → filter, {state:"Rajasthan", operating_intensity:["Regional"]}
"Top 10 banks by AUM"                               → filter, {sort_by:"aum_crores", sort_dir:"desc"}
"Compare Bajaj and Muthoot"                         → compare, compare_names:["Bajaj Finance","Muthoot Finance"]
"Compare SBI and HDFC"                              → compare, compare_names:["State Bank of India","HDFC Bank"]
"SBI vs HDFC Bank"                                  → compare, compare_names:["State Bank of India","HDFC Bank"]
"Difference between SBI and HDFC Bank"              → compare, compare_names:["State Bank of India","HDFC Bank"]
"SBI compared to HDFC"                              → compare, compare_names:["State Bank of India","HDFC Bank"]
"Tell me about SBI"                                 → lender_detail, detail_names:["State Bank of India"]
"Tell me about HDFC Bank"                           → lender_detail, detail_names:["HDFC Bank"]
"What is an NBFC?"                                  → concept
"What is CIBIL score?" / "How is credit score calculated?" → concept
"What is NPA?" / "What is a bad loan?"              → concept
"What is priority sector lending?"                  → concept
"How does EMI work?" / "What is floating rate?"     → concept
"What is SARFAESI?" / "What is DRT?"                → concept
"What is KYC in banking?"                           → concept
"What is co-lending?" / "What is MCLR?"             → concept
"Which lenders have highest AUM?"                   → filter, {sort_by:"aum_crores", sort_dir:"desc"}
"Top NBFCs by AUM"                                  → filter, {company_type:["NBFC"], sort_by:"aum_crores", sort_dir:"desc"}
"Biggest banks in India"                            → filter, {company_type:["Private Bank","PSU Bank","Foreign Bank"], sort_by:"aum_crores", sort_dir:"desc"}
"Largest banks in India"                            → filter, {company_type:["Private Bank","PSU Bank","Foreign Bank"], sort_by:"aum_crores", sort_dir:"desc"}
"Largest banks"                                     → filter, {company_type:["Private Bank","PSU Bank","Foreign Bank"], sort_by:"aum_crores", sort_dir:"desc"}
"Top 5 banks by AUM"                                → filter, {company_type:["Private Bank","PSU Bank","Foreign Bank"], sort_by:"aum_crores", sort_dir:"desc"}
"Which NBFC is largest?"                            → filter, {company_type:["NBFC"], sort_by:"aum_crores", sort_dir:"desc"}
"Vehicle loan"                                      → filter, {loan_type:["Vehicle Loan"]}
"Pre owned car loan" / "Used car loan" / "Car loan" → filter, {loan_type:["Vehicle Loan"]}
"Housing loan" / "House loan"                       → filter, {loan_type:["Home Loan"]}
"Bike loan" / "Two wheeler loan"                    → filter, {loan_type:["Two Wheeler Loan"]}
"Gold loan"                                         → filter, {loan_type:["Gold Loan"]}
"Home loan"                                         → filter, {loan_type:["Home Loan"]}
"Lenders with AUM above 5000 crores"                → filter, {aum_min:5000}
"NBFCs with AUM between 1000 and 10000 crores"      → filter, {company_type:["NBFC"], aum_min:1000, aum_max:10000}
"Hello" / "Thanks" / "What can you do?"             → greeting
"Who won the cricket match?" / "Weather today"      → out_of_scope
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
   CRITICAL — Stats shows "By HQ(headquartered)" = where lenders' head offices are located, NOT
   where they operate. It is irrelevant to the operating count. ALWAYS use "Total matching: N"
   as your headline count — NEVER derive a count from the "By HQ(headquartered)" breakdown.
   Each slim record has an "operating_states" field — use it to describe WHICH lenders operate in a
   state, but NEVER count from the slim records (they are only a sample of 20). The Stats total is
   the authoritative count of ALL matching lenders, including those not shown.
   Example: filter=Gujarat, Stats shows "Total: 189, showing top 20. By HQ(headquartered): Maharashtra(18), Karnataka(1), Gujarat(1)"
   → say "Found 189 lenders operating in Gujarat" — the 189 ALL operate there, most HQ elsewhere.
6. For compare: one bullet per lender — "**[Name]** — [Type], ₹X,XXX Cr, HQ [City], [key segments]"
   Never use flowing prose for compare. Always use bullets.
7. For filter results: ALWAYS open with "Found [N] lenders" using the exact total from Stats.
   NEVER start with "Here are", "Below are", "These are", "I found", "There are", or any other phrasing.
   The Stats note contains "Total matching: X" — use X as your headline count. NEVER invent a different number.
   If Stats says "Total matching: 162, showing top 20" → say "Found 162 lenders (showing top 20)".
   If Stats says "Total matching: 19" → say "Found 19 lenders".
   After the opening line, name top 3 by AUM inline with ₹ and city in one sentence.
   Example: "Found 47 NBFCs in Maharashtra (showing top 20). Biggest: **IIFL Finance** (₹92,164 Cr, Mumbai),
   **Kotak Mahindra Prime** (₹30,000 Cr, Mumbai), and **SBFC Finance** (₹7,200 Cr)."
   STOP after the top-3 sentence — do NOT add "Here are some of the lenders:" or list any further results.
   The UI already shows all results as cards. Your filter answer is exactly 2 sentences: opening + top 3.
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
    def __init__(self, api_key: str, model: str = "") -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required for the chat feature")
        self._client = genai.Client(api_key=api_key)
        self._model = model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        self._retries = int(os.environ.get("GEMINI_CHAT_RETRIES", "3"))
        logger.info("GeminiChatClient initialized (model=%s, retries=%d)", self._model, self._retries)

    # ------------------------------------------------------------------
    # Pass 1 — intent classification (thinking DISABLED for speed)
    # ------------------------------------------------------------------

    def parse_intent(self, message: str, history: list[dict]) -> dict:
        """Classify intent and extract entities. Returns no answer text."""
        # Use up to 4 history turns for pronoun resolution — enough context, low latency
        contents = self._build_contents(history[-8:], message)

        def _call():
            return self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=_INTENT_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=_INTENT_SCHEMA,
                    temperature=0.1,
                    max_output_tokens=256,
                    **_thinking(0),  # no thinking needed for structured classification
                ),
            )

        try:
            response = _call_with_retry(_call, attempts=self._retries, delay=1.5, label="parse_intent")
            raw = response.text
        except Exception as exc:
            logger.error("Gemini parse_intent failed after retries: %s", exc)
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
    # Pass 2 — grounded answer (non-streaming)
    # ------------------------------------------------------------------

    def generate_grounded_answer(
        self,
        question: str,
        intent: str,
        lenders: list[dict],
        history: list[dict],
        note: str = "",
        total_count: int = 0,
    ) -> str:
        """Generate a natural-language answer from the provided DB records."""

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

        if intent == "concept":
            contents = self._build_contents(history[-(4 * 2):], question)

            def _call():
                return self._client.models.generate_content(
                    model=self._model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=_CONCEPT_SYSTEM_PROMPT,
                        temperature=0.3,
                        max_output_tokens=500,
                        **_thinking(512),  # light thinking for educational answers
                    ),
                )

            try:
                response = _call_with_retry(_call, attempts=self._retries, delay=2.0, label="concept_answer")
                text = response.text
                return text if text else "I couldn't generate an answer. Please try again."
            except Exception as exc:
                logger.error("Gemini concept answer error: %s", exc)
                raise

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

        prompt = self._build_grounded_prompt(question, intent, lenders, note, total_count)
        history_contents = self._build_contents(history[-(6 * 2):], None)
        contents = history_contents + [
            types.Content(role="user", parts=[types.Part(text=prompt)])
        ]

        def _call():
            return self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=_ANSWER_SYSTEM_PROMPT,
                    temperature=0.3,
                    max_output_tokens=1200,
                    **_thinking(0),  # grounded answers are formatting tasks — no thinking needed
                ),
            )

        try:
            response = _call_with_retry(_call, attempts=self._retries, delay=2.0, label="grounded_answer")
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
        total_count: int = 0,
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

            def _mk_concept_stream():
                return self._client.models.generate_content_stream(
                    model=self._model,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=_CONCEPT_SYSTEM_PROMPT,
                        temperature=0.3,
                        max_output_tokens=500,
                        **_thinking(512),
                    ),
                )

            yielded = False
            for chunk in _retried_stream(_mk_concept_stream, attempts=self._retries, delay=1.0, label="concept_stream"):
                if chunk.text:
                    yielded = True
                    yield chunk.text
            if not yielded:
                yield "I couldn't generate an answer. Please try again."
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

        prompt = self._build_grounded_prompt(question, intent, lenders, note, total_count)
        history_contents = self._build_contents(history[-(6 * 2):], None)
        contents = history_contents + [
            types.Content(role="user", parts=[types.Part(text=prompt)])
        ]

        def _mk_answer_stream():
            return self._client.models.generate_content_stream(
                model=self._model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=_ANSWER_SYSTEM_PROMPT,
                    temperature=0.3,
                    max_output_tokens=1200,
                    **_thinking(0),
                ),
            )

        yielded = False
        for chunk in _retried_stream(_mk_answer_stream, attempts=self._retries, delay=1.0, label="answer_stream"):
            if chunk.text:
                yielded = True
                yield chunk.text
        if not yielded:
            yield "I couldn't generate an answer. Please try again."

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _build_grounded_prompt(
        self, question: str, intent: str, lenders: list[dict], note: str, total_count: int = 0,
    ) -> str:
        top_names = [
            l.get("company_name") or l.get("name", "")
            for l in lenders[:3]
            if l.get("company_name") or l.get("name")
        ]
        name_hint = f"\nTop lender names for your suggestions: {', '.join(top_names)}" if top_names else ""

        if intent == "filter":
            slim         = [_slim_record(l) for l in lenders]
            shown        = len(lenders)
            db_total     = total_count if total_count > 0 else shown
            type_counts  = Counter(l.get("company_type") for l in lenders if l.get("company_type"))
            state_counts = Counter(l.get("hq_state")     for l in lenders if l.get("hq_state"))
            top_types    = ", ".join(f"{t}({c})" for t, c in type_counts.most_common(4))
            top_states   = ", ".join(f"{s}({c})" for s, c in state_counts.most_common(4))
            total_note   = f"Total matching: {db_total}, showing top {shown}" if db_total > shown else f"Total matching: {db_total}"
            prefix = (
                f"{note + chr(10) if note else ''}"
                f"Stats — {total_note}. By type: {top_types}, By HQ(headquartered): {top_states}.{name_hint}\n\n"
            )
            context = slim
        else:
            prefix  = f"{note}\n\n{name_hint}\n\n" if (note or name_hint) else ""
            context = lenders

        records_json = json.dumps(context, indent=2, default=str)
        return (
            f"{prefix}"
            f"Database records:\n{records_json}\n\n"
            f"User question: {question}\n\n"
            "Answer in natural language using ONLY the records above. Follow all tone and formatting rules."
        )

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
        "aum_category":        l.get("aum_category"),      # restored — needed for AUM band answers
        "hq_state":            l.get("hq_state"),
        "hq_location":         l.get("hq_location"),
        "pan_india":           l.get("pan_india"),
        "operating_states":    l.get("operating_states"),  # needed so AI can verify state coverage
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
_client_lock = threading.Lock()


def get_gemini_client() -> GeminiChatClient:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                api_key = os.environ.get("GEMINI_API_KEY", "")
                model   = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
                _client = GeminiChatClient(api_key, model=model)
    return _client


def reset_gemini_client() -> None:
    """Force re-creation of the singleton on next get_gemini_client() call."""
    global _client
    with _client_lock:
        _client = None
