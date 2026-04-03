"""
scheduler.py  —  Production Scheduler v2
==========================================
Continuously keeps lender data fresh.

What it does:
  1. Reads lenders from Supabase where next_scrape_at < NOW()
  2. Re-scrapes website + re-runs Gemini for each stale lender
  3. Compares new data against stored data (change detection)
  4. Changes found  → updates DB, flags approval_status = 'needs_update'
  5. No changes     → only bumps next_scrape_at (no writes to data)

Production features:
  - Lock file         : prevents two instances running at once
  - Signal handling   : SIGTERM / SIGINT → clean shutdown, no data corruption
  - Retry + backoff   : DB errors retried 3× before skipping
  - Health check      : --health verifies DB + API connectivity before running
  - Dry run           : --dry-run shows what would happen, writes nothing
  - Batch limit       : --batch N caps how many are processed per run
  - Priority queue    : NBFCs first (change more often), banks later
  - Jitter            : random delay between requests (avoids bot detection)
  - Rotating logs     : max 5 log files × 10 MB each
  - Status report     : --status shows pending/approved/stale counts
  - Metrics           : prints run summary with timings at the end

CLI:
  python scheduler.py --health            # check connectivity
  python scheduler.py --status            # show DB counts
  python scheduler.py --once              # process all stale, then exit
  python scheduler.py --once --batch 50   # process up to 50 stale
  python scheduler.py --watch 3600        # loop every hour
  python scheduler.py --lender 42         # force re-scrape one lender
  python scheduler.py --dry-run           # show stale list, write nothing
  python scheduler.py --set-schedule      # initialise next_scrape_at (run once)
"""

import os, sys, json, time, signal, random, hashlib, logging, argparse
from logging.handlers import RotatingFileHandler
from pathlib          import Path
from datetime         import datetime, timedelta
from typing           import Dict, List, Optional
from collections      import Counter
import requests

# ── env loading ──────────────────────────────────────────────────────────────
_ENV_FILE = Path(__file__).resolve().parent.parent / '.env'
if _ENV_FILE.exists():
    with open(_ENV_FILE, encoding='utf-8') as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _, _v = _line.partition('=')
                _k = _k.strip(); _v = _v.strip().strip('"').strip("'")
                if _k and _k not in os.environ:
                    os.environ[_k] = _v
try:
    from dotenv import load_dotenv
    load_dotenv(_ENV_FILE)
except ImportError:
    pass

SUPABASE_URL = os.getenv('NEXT_PUBLIC_SUPABASE_URL', '').rstrip('/')
SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY', '')
GEMINI_KEY   = os.getenv('GEMINI_API_KEY', '')

ROOT     = Path(__file__).parent.parent
LOG_DIR  = ROOT / 'logs'
LOCK_FILE = ROOT / 'logs' / 'scheduler.lock'

# ── Scrape intervals (days) per company type ─────────────────────────────────
SCRAPE_INTERVALS: Dict[str, int] = {
    'PSU Bank':          60,   # stable institutions — 2 months
    'Foreign Bank':      60,
    'Cooperative Bank':  60,
    'Private Bank':      45,
    'Small Finance Bank': 30,
    'NBFC':              30,   # change often (rates, products, status)
    'NBFC-MFI':          30,
    'default':           30,
}

# Priority order for processing (lower number = higher priority)
PRIORITY: Dict[str, int] = {
    'NBFC': 1, 'NBFC-MFI': 1, 'Small Finance Bank': 2,
    'Private Bank': 3, 'PSU Bank': 4,
    'Foreign Bank': 5, 'Cooperative Bank': 5,
}

# Fields watched for change detection
CHANGE_FIELDS = [
    'aum_crores', 'primary_loan_segments', 'hq_state',
    'operating_states', 'employee_count', 'phone', 'email',
    'ticket_size_min', 'ticket_size_max', 'website',
]

# DB retry config
DB_MAX_RETRIES = 3
DB_RETRY_DELAY = 5   # seconds, doubles each retry

# Jitter between scrape requests (seconds)
JITTER_MIN = 1.0
JITTER_MAX = 3.0

# ─────────────────────────────────────────────────────────────────────────────
# GRACEFUL SHUTDOWN
# ─────────────────────────────────────────────────────────────────────────────

_shutdown_requested = False

def _handle_signal(sig, frame):
    global _shutdown_requested
    _shutdown_requested = True
    logging.warning(f"Signal {sig} received — finishing current lender then stopping")

signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING  (rotating file + console)
# ─────────────────────────────────────────────────────────────────────────────

def setup_logging(verbose: bool = False):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / 'scheduler.log'

    file_handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # Silence noisy third-party loggers
    for noisy in ('scrapling', 'curl_cffi', 'camoufox', 'urllib3', 'httpx'):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.info(f"Logging to {log_file}")


# ─────────────────────────────────────────────────────────────────────────────
# LOCK FILE  (prevent two scheduler instances running at once)
# ─────────────────────────────────────────────────────────────────────────────

class LockFile:
    def __init__(self, path: Path):
        self.path = path

    def acquire(self) -> bool:
        """Returns True if lock acquired, False if already locked."""
        if self.path.exists():
            try:
                pid = int(self.path.read_text().strip())
                # Check if that PID is still alive
                try:
                    os.kill(pid, 0)
                    logging.error(
                        f"Scheduler already running (PID {pid}). "
                        f"If stale, delete: {self.path}"
                    )
                    return False
                except (OSError, ProcessLookupError):
                    # PID dead — stale lock
                    logging.warning(f"Removing stale lock for PID {pid}")
                    self.path.unlink(missing_ok=True)
            except (ValueError, IOError):
                self.path.unlink(missing_ok=True)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(str(os.getpid()))
        return True

    def release(self):
        self.path.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# SUPABASE CLIENT
# ─────────────────────────────────────────────────────────────────────────────

class SupabaseClient:
    def __init__(self):
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError(
                "Missing NEXT_PUBLIC_SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in .env"
            )
        self._base_headers = {
            'apikey':        SUPABASE_KEY,
            'Authorization': f'Bearer {SUPABASE_KEY}',
            'Content-Type':  'application/json',
            'Prefer':        'return=representation',
        }
        self._session = requests.Session()
        self._session.headers.update(self._base_headers)

    def _url(self, table: str) -> str:
        return f"{SUPABASE_URL}/rest/v1/{table}"

    def _retry(self, fn, label: str):
        """Run fn() with exponential backoff on network/5xx errors."""
        delay = DB_RETRY_DELAY
        for attempt in range(1, DB_MAX_RETRIES + 1):
            try:
                return fn()
            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code < 500:
                    raise   # 4xx: don't retry (bad request / auth)
                logging.warning(f"{label} HTTP error attempt {attempt}: {e}")
            except requests.exceptions.ConnectionError as e:
                logging.warning(f"{label} connection error attempt {attempt}: {e}")
            except requests.exceptions.Timeout:
                logging.warning(f"{label} timeout attempt {attempt}")

            if attempt < DB_MAX_RETRIES:
                time.sleep(delay)
                delay *= 2
        raise RuntimeError(f"{label} failed after {DB_MAX_RETRIES} attempts")

    def select(self, table: str, query: str = '') -> List[Dict]:
        url = self._url(table) + (f'?{query}' if query else '')
        def _do():
            r = self._session.get(url, timeout=30)
            r.raise_for_status()
            return r.json()
        return self._retry(_do, f"SELECT {table}")

    def update(self, table: str, row_id: int, data: dict) -> dict:
        url = self._url(table) + f'?id=eq.{row_id}'
        def _do():
            r = self._session.patch(url, json=data, timeout=30)
            r.raise_for_status()
            return r.json()
        return self._retry(_do, f"UPDATE {table} id={row_id}")

    def upsert(self, table: str, rows: List[Dict]) -> List[Dict]:
        url = self._url(table)
        hdrs = {'Prefer': 'resolution=merge-duplicates,return=representation'}
        def _do():
            r = self._session.post(url, json=rows, headers=hdrs, timeout=60)
            r.raise_for_status()
            return r.json()
        return self._retry(_do, f"UPSERT {table}")

    def ping(self) -> bool:
        """Health check — verify DB is reachable."""
        try:
            r = self._session.get(
                self._url('lenders') + '?select=id&limit=1', timeout=10
            )
            return r.status_code == 200
        except Exception:
            return False


# ─────────────────────────────────────────────────────────────────────────────
# CHANGE DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def _normalise(v):
    """Parse JSON strings → Python objects for reliable comparison."""
    if isinstance(v, str):
        try:    return json.loads(v)
        except: pass
    if isinstance(v, list):
        return sorted(str(x) for x in v)
    return v


def detect_changes(old: dict, new: dict) -> List[str]:
    """Return list of field names where the value changed."""
    changed = []
    for f in CHANGE_FIELDS:
        if _normalise(old.get(f)) != _normalise(new.get(f)):
            changed.append(f)
    return changed


def data_fingerprint(row: dict) -> str:
    """MD5 of all change-sensitive fields — fast equality check."""
    parts = [f"{f}={json.dumps(_normalise(row.get(f)), sort_keys=True)}"
             for f in CHANGE_FIELDS]
    return hashlib.md5('|'.join(parts).encode()).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# RE-EXTRACTION  (reuses the existing hybrid pipeline)
# ─────────────────────────────────────────────────────────────────────────────

# Import once at module level (not inside the loop — much faster)
_pipeline_loaded = False
_extract_with_gemini  = None
_merge_scraper_data   = None
_build_guardrails_input = None
_RateLimiter          = None
_Guardrails           = None
_SingleLenderScraper  = None

def _load_pipeline():
    global _pipeline_loaded, _extract_with_gemini, _merge_scraper_data
    global _build_guardrails_input, _RateLimiter, _Guardrails, _SingleLenderScraper

    if _pipeline_loaded:
        return True

    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from run_nbfc_extraction import (
            extract_with_gemini, merge_scraper_data,
            build_guardrails_input, RateLimiter,
        )
        from scraper.guardrails import Guardrails
        _extract_with_gemini    = extract_with_gemini
        _merge_scraper_data     = merge_scraper_data
        _build_guardrails_input = build_guardrails_input
        _RateLimiter            = RateLimiter
        _Guardrails             = Guardrails
    except Exception as e:
        logging.error(f"Pipeline import failed: {e}")
        return False

    try:
        from scraper.lender_scraper import SingleLenderScraper
        _SingleLenderScraper = SingleLenderScraper
    except Exception:
        logging.warning("Scraper not available — Gemini-only mode")

    _pipeline_loaded = True
    return True


# Shared rate limiter and scraper instance (reused across all lenders in a run)
_rl      = None
_scraper = None

def _get_shared_resources():
    global _rl, _scraper
    if _rl is None and _RateLimiter:
        _rl = _RateLimiter()
    if _scraper is None and _SingleLenderScraper:
        try:
            _scraper = _SingleLenderScraper(use_stealth=False)
        except Exception:
            pass
    return _rl, _scraper


def re_extract_lender(lender: dict) -> Optional[dict]:
    """
    Re-run the full hybrid pipeline for a single lender.
    Returns validated clean_data dict, or None on any failure.
    """
    if not _load_pipeline():
        return None

    rl, scraper = _get_shared_resources()
    if rl is None:
        logging.error("Rate limiter unavailable")
        return None

    name    = lender.get('company_name', '')
    website = lender.get('website', '')
    summary = lender.get('business_summary', '')
    focus   = lender.get('primary_focus', '')

    logging.info(f"  Re-extracting: {name}")

    # Phase 1: Scrape
    scraped = {}
    if scraper and website:
        try:
            # Jitter before each scrape (avoids bot detection patterns)
            time.sleep(random.uniform(JITTER_MIN, JITTER_MAX))
            scraped = scraper.scrape(name, website)
            if scraped.get('is_non_lender'):
                logging.info(f"  Non-lender signal detected — skipping Gemini")
                return None
        except Exception as e:
            logging.warning(f"  Scraper error: {e}")

    # Phase 2: Gemini — wrapped in retry + circuit breaker
    try:
        from pipeline.circuit_breaker import get_gemini_circuit, CircuitOpenError
        from pipeline.retry import retry_with_backoff
        circuit = get_gemini_circuit(
            failure_threshold=int(os.environ.get("GEMINI_CIRCUIT_THRESHOLD", "5")),
            reset_timeout=float(os.environ.get("GEMINI_CIRCUIT_RESET_S", "300")),
        )

        def _gemini_call():
            return circuit.call(
                _extract_with_gemini,
                name, website, summary, focus, rl,
                scraped_context=scraped or None,
            )

        extracted = retry_with_backoff(
            _gemini_call,
            attempts=int(os.environ.get("GEMINI_RETRY_ATTEMPTS", "3")),
            delay=float(os.environ.get("GEMINI_RETRY_DELAY_S", "10.0")),
            exceptions=(Exception,),
            label=f"Gemini:{name}",
        )
    except Exception as exc:
        logging.warning(f"  Gemini failed for {name} after retries: {exc}")
        return None

    if not extracted:
        logging.warning(f"  Gemini returned nothing for {name}")
        return None

    # Phase 3: Merge
    if scraped:
        extracted = _merge_scraper_data(scraped, extracted)

    # Phase 4: Guardrails
    gr = _Guardrails().run(
        _build_guardrails_input(lender, extracted, scraped, 'scheduler')
    )
    if not gr.is_valid:
        issues = [i['message'] for i in gr.critical_issues]
        logging.warning(f"  Guardrails rejected: {issues}")
        return None

    return gr.clean_data


# ─────────────────────────────────────────────────────────────────────────────
# SCHEDULER CORE
# ─────────────────────────────────────────────────────────────────────────────

def process_stale_lenders(
    db:          SupabaseClient,
    force_id:    Optional[int] = None,
    batch_limit: Optional[int] = None,
    dry_run:     bool          = False,
) -> Counter:
    """
    Main processing loop.
    Returns Counter with run statistics.
    """
    stats = Counter()
    run_start = time.time()

    # ── Fetch stale lenders ───────────────────────────────────────────────────
    if force_id:
        query = f'id=eq.{force_id}&select=*'
        logging.info(f"Force mode: lender {force_id}")
    else:
        now_iso = datetime.utcnow().isoformat()
        query = (
            f'next_scrape_at=lt.{now_iso}'
            f'&approval_status=in.(approved,needs_update)'
            f'&select=id,company_name,company_type,website,hq_state,'
            f'aum_crores,primary_loan_segments,operating_states,'
            f'employee_count,phone,email,ticket_size_min,ticket_size_max,'
            f'scrape_interval_days,last_scraped_at,business_summary,primary_focus'
        )

    try:
        lenders = db.select('lenders', query)
    except Exception as e:
        logging.error(f"Failed to fetch stale lenders: {e}")
        return stats

    if not lenders:
        logging.info("No stale lenders — all up to date")
        return stats

    # ── Sort by priority (NBFCs first — change more often) ───────────────────
    lenders.sort(key=lambda r: PRIORITY.get(r.get('company_type', ''), 9))

    # ── Apply batch limit ─────────────────────────────────────────────────────
    if batch_limit:
        lenders = lenders[:batch_limit]

    total = len(lenders)
    logging.info(f"Processing {total} lenders (dry_run={dry_run})")

    if dry_run:
        print(f"\n  DRY RUN — would process {total} stale lenders:")
        for i, l in enumerate(lenders, 1):
            since = l.get('last_scraped_at', 'never')
            print(f"  {i:3}. [{l.get('company_type','?'):15}] {l.get('company_name','?')[:50]:50} | last: {since}")
        return stats

    # ── Processing loop ───────────────────────────────────────────────────────
    for idx, lender in enumerate(lenders, 1):
        if _shutdown_requested:
            logging.warning("Shutdown requested — stopping after current lender")
            break

        lid   = lender['id']
        name  = lender.get('company_name', str(lid))
        ctype = lender.get('company_type', 'NBFC')
        stats['checked'] += 1

        print(f"  [{idx}/{total}] {name[:60]}")

        # ── Re-extract ────────────────────────────────────────────────────────
        new_data = re_extract_lender(lender)

        interval = int(lender.get('scrape_interval_days') or SCRAPE_INTERVALS.get(ctype, 30))
        next_scrape = (datetime.utcnow() + timedelta(days=interval)).isoformat()
        now_iso     = datetime.utcnow().isoformat()

        if not new_data:
            stats['failed'] += 1
            # Always bump next_scrape_at to avoid hammering failed lenders
            try:
                db.update('lenders', lid, {
                    'last_scraped_at': now_iso,
                    'next_scrape_at':  next_scrape,
                })
            except Exception as e:
                logging.error(f"  Failed to update schedule for lender {lid}: {e}")
            print(f"    FAILED — rescheduled in {interval} days\n")
            continue

        # ── Change detection ──────────────────────────────────────────────────
        changed = detect_changes(lender, new_data)

        update_data: dict = {
            'last_scraped_at': now_iso,
            'next_scrape_at':  next_scrape,
        }

        if changed:
            stats['changed'] += 1
            logging.info(f"  Changes in {changed}")

            # Only update the fields that actually changed
            for f in changed:
                if new_data.get(f) is not None:
                    update_data[f] = new_data[f]

            # Admin-in-the-loop: do NOT auto-approve, just flag
            update_data['approval_status'] = 'needs_update'
            update_data['admin_notes'] = (
                f"Auto-detected changes {datetime.utcnow().date()}: "
                + ', '.join(changed)
            )
            print(f"    CHANGED: {changed}\n    -> flagged for admin review\n")
        else:
            stats['unchanged'] += 1
            print(f"    No changes — next scrape in {interval}d\n")

        try:
            db.update('lenders', lid, update_data)
        except Exception as e:
            logging.error(f"  DB update failed for lender {lid}: {e}")
            stats['db_errors'] += 1

    elapsed = round(time.time() - run_start, 1)
    stats['elapsed_seconds'] = elapsed

    # ── Run summary ───────────────────────────────────────────────────────────
    logging.info(f"Run complete in {elapsed}s: {dict(stats)}")
    print(f"\n{'─'*50}")
    print(f"  Run summary ({elapsed}s)")
    print(f"  Checked   : {stats['checked']}")
    print(f"  Changed   : {stats['changed']}  (flagged needs_update)")
    print(f"  Unchanged : {stats['unchanged']}")
    print(f"  Failed    : {stats['failed']}")
    if stats['db_errors']:
        print(f"  DB errors : {stats['db_errors']}")
    print(f"{'─'*50}\n")

    return stats


# ─────────────────────────────────────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────────────────────────────────────

def health_check(db: SupabaseClient) -> bool:
    """Verify DB and Gemini API are reachable before starting a run."""
    ok = True

    # DB ping
    if db.ping():
        print("  [OK] Supabase connected")
    else:
        print("  [FAIL] Supabase unreachable")
        ok = False

    # Gemini ping (minimal token call)
    if GEMINI_KEY:
        try:
            url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                   f"gemini-2.5-flash:generateContent?key={GEMINI_KEY}")
            r = requests.post(url, json={
                "contents": [{"parts": [{"text": "ping"}]}],
                "generationConfig": {"maxOutputTokens": 1},
            }, timeout=15)
            if r.status_code in (200, 400):   # 400 = reachable but bad request
                print("  [OK] Gemini API reachable")
            else:
                print(f"  [FAIL] Gemini API returned {r.status_code}")
                ok = False
        except Exception as e:
            print(f"  [FAIL] Gemini API: {e}")
            ok = False
    else:
        print("  [FAIL] GEMINI_API_KEY not set")
        ok = False

    return ok


# ─────────────────────────────────────────────────────────────────────────────
# STATUS REPORT
# ─────────────────────────────────────────────────────────────────────────────

def print_status(db: SupabaseClient):
    """Show DB counts by approval_status."""
    try:
        rows = db.select('lenders', 'select=approval_status')
        counts = Counter(r.get('approval_status', 'unknown') for r in rows)
        now_iso = datetime.utcnow().isoformat()
        stale   = db.select(
            'lenders',
            f'next_scrape_at=lt.{now_iso}&approval_status=in.(approved,needs_update)&select=id'
        )
        print(f"\n  Lender DB Status")
        print(f"  {'─'*30}")
        for status, count in sorted(counts.items()):
            print(f"  {status:15} : {count}")
        print(f"  {'─'*30}")
        print(f"  Stale (due now) : {len(stale)}")
        print()
    except Exception as e:
        logging.error(f"Status query failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# INITIAL SCRAPE SCHEDULE
# ─────────────────────────────────────────────────────────────────────────────

def set_initial_scrape_schedule(db: SupabaseClient):
    """
    Set next_scrape_at for lenders that don't have one yet.
    Staggers them to avoid all re-scraping on the same day.
    Run once after first data load.
    """
    lenders = db.select('lenders', 'next_scrape_at=is.null&select=id,company_type')
    if not lenders:
        print("All lenders already have a scrape schedule")
        return

    updates = []
    for i, l in enumerate(lenders):
        interval    = SCRAPE_INTERVALS.get(l.get('company_type', ''), 30)
        # Stagger evenly + small random offset to avoid bursts
        offset_days = (i % max(interval, 1)) + random.randint(0, 2)
        updates.append({
            'id':                   l['id'],
            'next_scrape_at':       (datetime.utcnow() + timedelta(days=offset_days)).isoformat(),
            'scrape_interval_days': interval,
        })

    try:
        db.upsert('lenders', updates)
        print(f"  Schedule set for {len(updates)} lenders")
    except Exception as e:
        logging.error(f"Schedule set failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='Lender platform scheduler — keeps data fresh automatically'
    )
    parser.add_argument('--health',       action='store_true',
                        help='Check DB + Gemini connectivity and exit')
    parser.add_argument('--status',       action='store_true',
                        help='Show DB counts and exit')
    parser.add_argument('--once',         action='store_true',
                        help='Process all stale lenders once, then exit')
    parser.add_argument('--watch',        type=int, metavar='SECONDS',
                        help='Run on a loop with this interval (seconds)')
    parser.add_argument('--lender',       type=int, metavar='ID',
                        help='Force re-scrape a specific lender by ID')
    parser.add_argument('--batch',        type=int, metavar='N',
                        help='Limit how many lenders to process per run')
    parser.add_argument('--dry-run',      action='store_true',
                        help='Show what would be processed — writes nothing')
    parser.add_argument('--set-schedule', action='store_true',
                        help='Set initial scrape schedule for all lenders (run once)')
    parser.add_argument('--verbose',      action='store_true',
                        help='Show DEBUG-level logs on console')
    args = parser.parse_args()

    setup_logging(verbose=args.verbose)

    logging.info(f"Scheduler v2 starting  pid={os.getpid()}")

    # ── Environment check ─────────────────────────────────────────────────────
    missing = [k for k in ('NEXT_PUBLIC_SUPABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY',
                            'GEMINI_API_KEY')
               if not os.getenv(k)]
    if missing:
        logging.error(f"Missing required env vars: {missing}")
        sys.exit(1)

    try:
        db = SupabaseClient()
    except RuntimeError as e:
        logging.error(str(e))
        sys.exit(1)

    # ── Special modes (no lock needed) ───────────────────────────────────────
    if args.health:
        print("\n  Health Check")
        sys.exit(0 if health_check(db) else 1)

    if args.status:
        print_status(db)
        return

    if args.set_schedule:
        set_initial_scrape_schedule(db)
        return

    # ── Acquire lock (prevents double-run) ───────────────────────────────────
    if not args.dry_run:
        lock = LockFile(LOCK_FILE)
        if not lock.acquire():
            sys.exit(1)
    else:
        lock = None

    try:
        if args.lender:
            process_stale_lenders(db, force_id=args.lender,
                                   dry_run=args.dry_run)
            return

        if args.watch:
            logging.info(f"Watch mode: every {args.watch}s")
            tick = 0
            while not _shutdown_requested:
                tick += 1
                print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                      f"Tick #{tick}")
                process_stale_lenders(db, batch_limit=args.batch,
                                       dry_run=args.dry_run)

                if _shutdown_requested:
                    break

                # Jitter on watch interval (±10%) to avoid thundering herd
                jitter   = args.watch * random.uniform(-0.1, 0.1)
                sleep_s  = max(args.watch + jitter, 60)
                wake_at  = datetime.now() + timedelta(seconds=sleep_s)
                logging.info(f"Next tick at {wake_at.strftime('%H:%M:%S')}")
                time.sleep(sleep_s)

            logging.info("Watch mode stopped cleanly")
            return

        # Default / --once
        process_stale_lenders(db, batch_limit=args.batch,
                               dry_run=args.dry_run)

    finally:
        if lock:
            lock.release()
        logging.info("Scheduler stopped")


if __name__ == '__main__':
    main()
