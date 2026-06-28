"""No-DB, no-broker import smoke tests for Lane C.

These must pass everywhere — even though the shared venv has NEITHER celery nor
redis installed. They prove the guarded imports work: the orchestration package
and all its modules import cleanly, the Celery app degrades to a clear-error
shim instead of crashing at import, and the beat schedule + dispatch functions
are present and callable as plain functions.
"""
from __future__ import annotations

import importlib


def test_package_imports():
    import orchestration  # noqa: F401

    assert orchestration.__doc__


def test_celery_app_imports_without_celery_installed():
    # The whole point of the guarded import: this must not raise even though
    # celery isn't installed in the shared venv.
    celery_app = importlib.import_module("orchestration.celery_app")
    assert celery_app.REDIS_URL  # default redis://localhost:6379/0
    assert "redis" in celery_app.REDIS_URL
    # BEAT_SCHEDULE is a plain dict, inspectable without celery.
    assert "dispatch-due-sends" in celery_app.BEAT_SCHEDULE
    assert "enqueue-due-work" in celery_app.BEAT_SCHEDULE


def test_celery_shim_raises_clearly_when_used():
    celery_app = importlib.import_module("orchestration.celery_app")
    if celery_app.celery_available():
        # If celery IS installed, the app is real — nothing to assert here.
        return
    # With celery absent, using the app (e.g. as a task decorator) must raise a
    # clear, actionable RuntimeError — not an opaque AttributeError.
    import pytest

    with pytest.raises(RuntimeError) as exc:
        celery_app.app.task()  # decorator usage on the shim
    assert "requirements-orchestration.txt" in str(exc.value)


def test_queue_module_imports_and_has_api():
    q = importlib.import_module("orchestration.queue")
    for fn in ("enqueue", "claim_due", "mark_sent", "mark_failed", "mark_skipped"):
        assert callable(getattr(q, fn)), f"queue.{fn} missing"


def test_rate_limit_module_imports_and_has_api():
    rl = importlib.import_module("orchestration.rate_limit")
    assert callable(rl.check_and_increment)


def test_tasks_module_imports_and_has_dispatch():
    tasks = importlib.import_module("orchestration.tasks")
    for fn in ("dispatch_one", "dispatch_due_sends", "enqueue_due_work", "is_suppressed"):
        assert callable(getattr(tasks, fn)), f"tasks.{fn} missing"
    # The channel-adapter seam exists and is wired (L4): it resolves the adapter
    # from dispatch.registry. Called with no DB connection it fails fast with a
    # clear ValueError (it needs a conn to resolve the channel/message), proving
    # the seam is the real adapter dispatch — not the old NotImplementedError
    # stub. End-to-end adapter behavior is covered by tests/test_dispatch_db.py.
    assert callable(tasks.send_via_channel)
    import pytest

    with pytest.raises(ValueError):
        tasks.send_via_channel(1, 1, "idem-key")


def test_enqueue_due_work_stub_callable_without_db():
    # The beat 'enqueue' task stub returns a summary dict and needs no DB.
    tasks = importlib.import_module("orchestration.tasks")
    out = tasks.enqueue_due_work()
    assert isinstance(out, dict)
    assert "enqueued" in out
