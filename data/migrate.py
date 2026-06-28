"""Forward-only SQL migration runner for the L0 Lead DB.

Boring by default: no ORM, no autogeneration, no magic. Migrations are plain
``.sql`` files in ``data/migrations/`` named ``NNNN_description.sql``. They are
applied in filename order, each inside its own transaction, and recorded in a
``schema_migrations`` table so re-runs are idempotent.

    python -m data.migrate            # apply all pending migrations
    python -m data.migrate --status   # show applied vs pending, then exit

Design notes:
  * A migration's checksum (sha256 of its bytes) is stored. If a previously
    applied file changes on disk, the runner refuses to proceed and tells you
    to add a new migration instead of editing a frozen one — this protects the
    "schema is the frozen contract" guarantee.
  * Each file runs in one transaction. A file that fails rolls back cleanly and
    nothing is recorded for it, so the next run retries from the same point.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import List, Tuple

from .db import ConfigError, connect

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_TRACKING_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT PRIMARY KEY,
    checksum   TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def discover() -> List[Path]:
    """Return migration files sorted by filename (i.e. by NNNN prefix)."""
    if not MIGRATIONS_DIR.is_dir():
        return []
    return sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.is_file())


def _applied(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute(_TRACKING_DDL)
        cur.execute("SELECT filename, checksum FROM schema_migrations")
        rows = cur.fetchall()
    conn.commit()
    return {fn: cs for fn, cs in rows}


def plan(conn) -> Tuple[List[Path], List[Tuple[Path, str, str]]]:
    """Return (pending, drifted). ``drifted`` = applied files whose on-disk
    checksum no longer matches what was recorded (a frozen migration was
    edited). Both lists are empty when the DB is up to date and untouched.
    """
    applied = _applied(conn)
    pending: List[Path] = []
    drifted: List[Tuple[Path, str, str]] = []
    for path in discover():
        cs = _checksum(path)
        if path.name not in applied:
            pending.append(path)
        elif applied[path.name] != cs:
            drifted.append((path, applied[path.name], cs))
    return pending, drifted


def apply_all(conn) -> List[str]:
    """Apply every pending migration. Returns the list of filenames applied.
    Raises RuntimeError if a frozen migration drifted (edit-in-place guard)."""
    pending, drifted = plan(conn)
    if drifted:
        names = ", ".join(p.name for p, _, _ in drifted)
        raise RuntimeError(
            f"Refusing to migrate: these applied migrations changed on disk: {names}.\n"
            "Migrations are frozen once applied. Add a new NNNN_*.sql instead of editing one."
        )
    done: List[str] = []
    for path in pending:
        sql = path.read_text()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s)",
                    (path.name, _checksum(path)),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        done.append(path.name)
    return done


def _status(conn) -> int:
    applied = _applied(conn)
    pending, drifted = plan(conn)
    print(f"Applied:  {len(applied)}")
    for name in sorted(applied):
        print(f"   [x] {name}")
    print(f"Pending:  {len(pending)}")
    for path in pending:
        print(f"   [ ] {path.name}")
    if drifted:
        print(f"DRIFT (applied files edited on disk): {len(drifted)}")
        for path, old, new in drifted:
            print(f"   [!] {path.name}  recorded={old[:12]}  now={new[:12]}")
        return 1
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Apply L0 Lead DB migrations.")
    parser.add_argument("--status", action="store_true", help="show status and exit")
    args = parser.parse_args(argv)

    try:
        conn = connect()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    with conn:
        if args.status:
            return _status(conn)
        applied = apply_all(conn)
        if applied:
            print("Applied: " + ", ".join(applied))
        else:
            print("Up to date — no pending migrations.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
