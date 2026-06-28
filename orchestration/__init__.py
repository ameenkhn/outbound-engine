"""Lane C — durable workflow orchestrator (decision 1B / T4).

Celery + Redis is the *trigger* (a beat schedule wakes workers on a cadence);
Postgres (``send_jobs``) is the durable *source of truth* for what was sent.
The split is deliberate: a crash can never lose a job or double-send one,
because every send is guarded by a UNIQUE ``idempotency_key`` and the
claim->send->record flow uses row locks (``FOR UPDATE SKIP LOCKED``).

Modules:
  * ``celery_app``  — the Celery app + beat schedule stub (guarded import so
    this package imports even when celery isn't installed).
  * ``queue``       — Postgres-backed durable outbox: enqueue / claim_due /
    mark_sent / mark_failed.
  * ``rate_limit``  — atomic per-bucket warmup caps (check_and_increment).
  * ``tasks``       — the dispatch task: claim -> 6A suppression re-check ->
    rate limit -> send -> record.

The DB-touching modules (``queue``, ``rate_limit``, ``tasks``) depend only on
``data.db`` + psycopg, NOT on celery, so they are unit-testable without a broker.
"""
