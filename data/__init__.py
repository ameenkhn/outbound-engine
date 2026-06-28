"""L0 Data Foundation — the Lead DB spine.

The frozen schema lives in ``data/migrations/``. Apply it with::

    python -m data.migrate            # apply pending migrations
    python -m data.migrate --status   # show applied / pending

Connection config comes from the ``DATABASE_URL`` environment variable (see
``.env.example``). Every later layer imports :func:`data.db.connect`.
"""
