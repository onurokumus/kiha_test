import os
from pathlib import Path


def _cors_origins() -> tuple[str, ...]:
    """Return the exact browser origins allowed to call the API directly.

    The nginx deployment uses a same-origin /ptt/api proxy and therefore does
    not need CORS. These entries cover local development and direct API access
    from the heliweb UI. Override the complete list with a comma-separated
    KIHA_CORS_ORIGINS value when the server name, scheme, or port differs.
    """
    defaults = (
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://heliweb",
        "https://heliweb",
    )
    configured = os.environ.get("KIHA_CORS_ORIGINS")
    values = configured.split(",") if configured is not None else defaults

    # An Origin never contains a URL path. Strip trailing slashes so common
    # values such as "https://ptt.example/" still match the browser header.
    return tuple(dict.fromkeys(
        origin.strip().rstrip("/")
        for origin in values
        if origin.strip().rstrip("/")
    ))


CORS_ORIGINS = _cors_origins()

# repo_root/data/tests/<test_name>/...
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("KIHA_DATA_DIR", REPO_ROOT / "data"))
TESTS_DIR = DATA_DIR / "tests"
TRASH_DIR = DATA_DIR / "trash"          # soft-deleted tests, undo window
TRASH_MAX_AGE_S = 3600                  # purged on the next delete after this

# Assumed sample rate when a file's time column is unusable (non-increasing /
# corrupted / a single repeated coarse timestamp). Ingest then synthesizes a
# perfect uniform time axis at this rate instead of failing. Overridable per
# upload with ?fs=; 2048 Hz matches the propeller rig's vibration sampling.
DEFAULT_FS_HZ = 2048.0

PYRAMID_LEVELS = [16, 256, 4096]   # downsample factors, each a multiple of the previous
ROW_GROUP_SIZE = 65536             # parquet row group size (rows) for data.parquet
INGEST_BATCH = 65536               # rows per pyramid-build batch (multiple of max level)

# plot serving
MAX_POINTS_RAW = 6000              # viewport spans <= this many raw samples -> serve raw
POINT_BUDGET_CAP = 8000            # hard cap on points returned per series

# signal processing (filters / FFT read the raw slice at full resolution)
MAX_FILTER_SAMPLES = 8_000_000     # reject filter/spectrum requests over larger ranges

# Reject an upload once the received body exceeds this many bytes — guards the
# data volume against a mistaken multi-GB non-CSV drop. A 1 h CSV is ~6-10 GB, so
# the default leaves headroom; override with KIHA_MAX_UPLOAD_BYTES (bytes).
MAX_UPLOAD_BYTES = int(os.environ.get("KIHA_MAX_UPLOAD_BYTES", 20 * 1024**3))
# Bytes of the first chunk inspected for binary content (a NUL byte -> not CSV).
UPLOAD_SNIFF_BYTES = 8192

# Resumable browser uploads.  A 16 MiB part is large enough to avoid
# request/latency overhead on the heliweb LAN while keeping retries cheap and
# bounding Starlette's temporary multipart spool.  The server advertises the
# value during initiation; clients must never assume it.
UPLOAD_CHUNK_BYTES = int(
    os.environ.get("KIHA_UPLOAD_CHUNK_BYTES", 16 * 1024**2))
# Multipart framing is tiny in normal requests.  This allowance is enforced by
# an ASGI receive wrapper *before* form parsing, so a malformed request cannot
# make Starlette spool an unbounded file part.
UPLOAD_MULTIPART_OVERHEAD_BYTES = int(
    os.environ.get("KIHA_UPLOAD_MULTIPART_OVERHEAD_BYTES", 1024**2))
# Incomplete sessions survive restarts and normal disconnects.  They are
# permanently cleaned after this age on startup or the next initiation.
UPLOAD_STALE_AGE_S = int(
    os.environ.get("KIHA_UPLOAD_STALE_AGE_S", 7 * 24 * 3600))
# Advisory headroom kept free when reserving a new upload.  ENOSPC is still
# handled during each commit because filesystem usage can change afterward.
UPLOAD_DISK_RESERVE_BYTES = int(
    os.environ.get("KIHA_UPLOAD_DISK_RESERVE_BYTES", 1024**3))

if UPLOAD_CHUNK_BYTES <= 0:
    raise ValueError("KIHA_UPLOAD_CHUNK_BYTES must be greater than zero")
if UPLOAD_CHUNK_BYTES > 64 * 1024**2:
    raise ValueError(
        "KIHA_UPLOAD_CHUNK_BYTES cannot exceed 67108864 (64 MiB)")
if UPLOAD_MULTIPART_OVERHEAD_BYTES < 64 * 1024:
    raise ValueError(
        "KIHA_UPLOAD_MULTIPART_OVERHEAD_BYTES must be at least 65536")
if UPLOAD_STALE_AGE_S <= 0:
    raise ValueError("KIHA_UPLOAD_STALE_AGE_S must be greater than zero")
if UPLOAD_DISK_RESERVE_BYTES < 0:
    raise ValueError("KIHA_UPLOAD_DISK_RESERVE_BYTES cannot be negative")
