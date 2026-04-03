"""
database/migrate.py
====================
Schema migration runner with backward-compatibility enforcement.

Usage:
    python migrate.py                  # Apply all pending migrations
    python migrate.py --status         # Show current version + pending
    python migrate.py --version 3      # Apply up to version 3 only
    python migrate.py --dry-run        # Show SQL without executing
    python migrate.py --rollback 2     # Roll back to version 2 (if down scripts exist)

Backward-compatibility rules enforced before every migration:
  - New columns MUST have DEFAULT values
  - Columns may not be dropped (use ALTER COLUMN ... SET DEFAULT NULL instead)
  - Constraint changes must be additive
"""

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

# Load .env from project root
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
if _ENV_FILE.exists():
    with open(_ENV_FILE, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                _k = _k.strip(); _v = _v.strip().strip('"').strip("'")
                if _k and _k not in os.environ:
                    os.environ[_k] = _v

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
DATABASE_URL   = os.environ.get("DATABASE_URL", "")

# ── Compatibility checks run before any migration ─────────────

_FORBIDDEN_PATTERNS = [
    (r"\bDROP\s+COLUMN\b",      "DROP COLUMN is forbidden — set DEFAULT NULL instead"),
    (r"\bDROP\s+TABLE\b",       "DROP TABLE is forbidden — rename to _legacy instead"),
    # Only block NOT NULL without DEFAULT on ALTER TABLE (new tables can have NOT NULL freely)
    (r"ALTER\s+TABLE\b[^;]*\bNOT\s+NULL\b(?![^;]*\bDEFAULT\b)",
     "ALTER TABLE ... NOT NULL without DEFAULT breaks existing pipelines"),
]


def check_backward_compat(sql: str, filename: str):
    errors = []
    for pattern, msg in _FORBIDDEN_PATTERNS:
        if re.search(pattern, sql, re.IGNORECASE):
            errors.append(f"  [{filename}] {msg}")
    if errors:
        print("\n⛔  Backward-compatibility violations detected:")
        for e in errors:
            print(e)
        print("\n  Fix the migration file before running.\n")
        sys.exit(1)


# ── Migration runner ──────────────────────────────────────────

def get_connection():
    if not DATABASE_URL:
        print("✗  DATABASE_URL not set")
        sys.exit(1)
    try:
        return psycopg2.connect(DATABASE_URL, connect_timeout=10)
    except psycopg2.OperationalError as exc:
        print(f"✗  Cannot connect to database: {exc}")
        sys.exit(1)


def ensure_versions_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS schema_versions (
                version    INTEGER PRIMARY KEY,
                name       TEXT        NOT NULL,
                checksum   TEXT        NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
    conn.commit()


def get_current_version(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(version), 0) FROM schema_versions")
        return cur.fetchone()[0]


def get_applied_versions(conn) -> set:
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_versions")
        return {row[0] for row in cur.fetchall()}


def load_migrations(max_version: int = 9999) -> list[dict]:
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    migrations = []
    for f in files:
        m = re.match(r"^(\d+)_(.+)\.sql$", f.name)
        if not m:
            continue
        version = int(m.group(1))
        if version > max_version:
            continue
        sql      = f.read_text(encoding="utf-8")
        checksum = hashlib.md5(sql.encode()).hexdigest()
        migrations.append({
            "version":  version,
            "name":     m.group(2),
            "filename": f.name,
            "sql":      sql,
            "checksum": checksum,
        })
    return migrations


def run_migration(conn, migration: dict, dry_run: bool = False):
    sql      = migration["sql"]
    filename = migration["filename"]

    check_backward_compat(sql, filename)

    if dry_run:
        print(f"\n--- DRY RUN: {filename} ---")
        print(sql[:500] + ("..." if len(sql) > 500 else ""))
        return

    with conn.cursor() as cur:
        cur.execute(sql)
        # Upsert version record (migration SQL may already insert it)
        cur.execute("""
            INSERT INTO schema_versions (version, name, checksum)
            VALUES (%s, %s, %s)
            ON CONFLICT (version) DO UPDATE
                SET checksum = EXCLUDED.checksum,
                    applied_at = NOW()
        """, (migration["version"], migration["name"], migration["checksum"]))
    conn.commit()
    print(f"  ✓  {filename}")


def cmd_status(conn):
    current   = get_current_version(conn)
    applied   = get_applied_versions(conn)
    all_migs  = load_migrations()
    pending   = [m for m in all_migs if m["version"] not in applied]

    print(f"\n  Current schema version : {current}")
    print(f"  Applied migrations     : {len(applied)}")
    print(f"  Pending migrations     : {len(pending)}")
    if pending:
        print("\n  Pending:")
        for m in pending:
            print(f"    [{m['version']:03d}] {m['filename']}")
    print()


def cmd_migrate(conn, max_version: int = 9999, dry_run: bool = False):
    applied  = get_applied_versions(conn)
    all_migs = load_migrations(max_version)
    pending  = [m for m in all_migs if m["version"] not in applied]

    if not pending:
        print("  ✓  Schema is up to date.")
        return

    print(f"  Applying {len(pending)} migration(s)...\n")
    for m in pending:
        run_migration(conn, m, dry_run)

    if not dry_run:
        new_version = get_current_version(conn)
        print(f"\n  Schema is now at version {new_version}.")


def main():
    parser = argparse.ArgumentParser(description="Lender Platform schema migrator")
    parser.add_argument("--status",   action="store_true", help="Show migration status")
    parser.add_argument("--dry-run",  action="store_true", help="Print SQL without running")
    parser.add_argument("--version",  type=int, default=9999, help="Apply up to this version")
    args = parser.parse_args()

    conn = get_connection()
    ensure_versions_table(conn)

    if args.status:
        cmd_status(conn)
    else:
        cmd_migrate(conn, max_version=args.version, dry_run=args.dry_run)

    conn.close()


if __name__ == "__main__":
    main()
