"""Single source of truth for a test's lifecycle status.

The atomic status writer and the status-set constants that gate mutating
endpoints live here so main / ingest / edit share ONE public `write_status`
and ONE definition of the busy-status lists, instead of importing the
underscore-private `ingest._write_status` across modules and re-declaring the
lists in each caller (bugs 4.3, 4.5 — the drift that let `rebuilding` get left
out of restart recovery in bug 1.3).
"""

from pathlib import Path

from .store import write_json_atomic

# An upload that has not yet become a ready test.  'receiving' = the request
# body is still streaming in; 'ingesting' = the background CSV->parquet job
# runs.  Both must block delete/rename and both are repaired on restart.
INGEST_LIKE = ("receiving", "ingesting")

# Every status during which a background job holds the per-test write lock and
# is rewriting the test's files.  A mutating endpoint MUST 409 on these before
# taking test_write, or it parks on the lock for the whole (minutes-long) job,
# pinning a threadpool worker (bug 1.12).  'rebuilding' is the case INGEST_LIKE
# misses.
BUSY_STATUSES = ("receiving", "ingesting", "rebuilding")


def write_status(test_dir: Path, status: str, error: str = "") -> None:
    """Atomically publish a test's status.json (status + optional error)."""
    payload = {"status": status}
    if error:
        payload["error"] = error
    write_json_atomic(test_dir / "status.json", payload)
