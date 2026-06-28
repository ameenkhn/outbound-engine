"""pytest bootstrap.

Load `.env` BEFORE test collection so the DB-guarded tests
(`skipif(not os.environ.get("DATABASE_URL"))`) actually see `DATABASE_URL`.

Without this, the skip guards evaluate at import/collection time — before
`data.db` would lazily load `.env` — so the Postgres integration tests
silently skip even when `.env` is configured. A silently-skipped DB test reads
as green while proving nothing, which is exactly the failure mode this avoids.
With this, `DATABASE_URL` in `.env` (or the real environment) is enough to run
the full suite locally and in CI.
"""
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # dotenv absent — real-environment DATABASE_URL still works
    pass
