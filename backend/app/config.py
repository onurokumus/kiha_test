import os
from pathlib import Path

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
