"""Celery app for Lane C, with a guarded import so this module loads even when
celery isn't installed.

Why guarded: the shared venv has psycopg + pytest + dotenv but NOT celery/redis
(those live in ``requirements-orchestration.txt``). The durable queue / rate
limit / dispatch logic must stay importable and unit-testable without a broker.
So:

  * ``import celery`` is attempted lazily. If it's missing we install a thin
    shim ``app`` whose attribute access / call raises a clear, actionable error
    ONLY when something actually tries to *use* Celery at runtime. Importing the
    module, reading ``BEAT_SCHEDULE``, etc. all keep working.

Config:
  * broker + result backend come from ``REDIS_URL`` (default
    ``redis://localhost:6379/0``).
  * ``BEAT_SCHEDULE`` is a stub that periodically fires the "enqueue due work"
    and "dispatch" tasks. Tasks themselves live in ``orchestration.tasks`` and
    are registered via the ``include`` list so this module has no import cycle.
"""
from __future__ import annotations

import os

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# How often beat wakes the workers. Real cadence is a tuning knob (warmup ramp,
# back-pressure); these are sane stub defaults.
DISPATCH_INTERVAL_SECONDS = float(os.environ.get("ORCH_DISPATCH_INTERVAL", "10"))
SWEEP_INTERVAL_SECONDS = float(os.environ.get("ORCH_SWEEP_INTERVAL", "60"))


class _CeleryUnavailable:
    """Stand-in for the Celery app when celery isn't installed.

    Any real use (registering a task, sending a task, starting a worker) raises
    with an actionable message. Merely importing this module — or reading
    ``BEAT_SCHEDULE`` / ``REDIS_URL`` — does not touch it, so no-DB / no-broker
    tests and tooling keep working.
    """

    _MSG = (
        "Celery is not installed. The orchestration runtime needs it:\n"
        "  pip install -r requirements-orchestration.txt\n"
        "(The durable queue, rate limiter and dispatch logic are usable without "
        "Celery; only running the beat scheduler / workers requires it.)"
    )

    def __init__(self, reason: str = "") -> None:
        self._reason = reason

    def _fail(self, *_args, **_kwargs):
        detail = f"{self._MSG}\n(import error: {self._reason})" if self._reason else self._MSG
        raise RuntimeError(detail)

    # Decorator usage: @app.task(...) -> must blow up clearly if reached.
    def task(self, *_args, **_kwargs):
        self._fail()

    def __getattr__(self, _name):
        self._fail()

    def __call__(self, *_args, **_kwargs):
        self._fail()


# Beat schedule stub. Kept module-level (a plain dict) so it is inspectable and
# testable without celery present. Wired onto the real app below when available.
BEAT_SCHEDULE = {
    # Periodically enqueue work that has come due (sourcing/score/personalize
    # produce messages; this turns due ones into pending send_jobs). Stub task.
    "enqueue-due-work": {
        "task": "orchestration.tasks.enqueue_due_work",
        "schedule": SWEEP_INTERVAL_SECONDS,
    },
    # Periodically drain the durable outbox: claim due send_jobs and dispatch.
    "dispatch-due-sends": {
        "task": "orchestration.tasks.dispatch_due_sends",
        "schedule": DISPATCH_INTERVAL_SECONDS,
    },
}


def _build_app():
    """Construct the real Celery app, or a clear-error shim if celery is absent."""
    try:
        from celery import Celery  # noqa: WPS433 (intentional local/guarded import)
    except Exception as exc:  # ImportError in practice; broad to be safe
        return _CeleryUnavailable(reason=str(exc))

    app = Celery(
        "exly_orchestration",
        broker=REDIS_URL,
        backend=REDIS_URL,
        include=["orchestration.tasks"],
    )
    app.conf.update(
        task_acks_late=True,            # don't ack until the task returns (crash-safe redelivery)
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,   # one in-flight task per worker — respects back-pressure
        task_default_queue="outbound",
        beat_schedule=BEAT_SCHEDULE,
        timezone="UTC",
        enable_utc=True,
    )
    return app


# The module-level app. Either a real Celery instance or the shim. Workers /
# beat use:  celery -A orchestration.celery_app:app worker --beat
app = _build_app()


def celery_available() -> bool:
    """True iff the real Celery app was constructed (celery is installed)."""
    return not isinstance(app, _CeleryUnavailable)
