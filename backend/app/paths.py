"""Portable link checks for the Linux Python 3.11 deployment baseline."""
from pathlib import Path
import stat


def is_link_or_junction(path: Path) -> bool:
    """Inspect the entry itself, including dangling links, without following it.

    Path.is_junction needs Python 3.12. Windows lstat exposes the mount-point
    reparse tag on 3.8+, so older runtimes must still reject directory junctions.
    Only absent entries are harmless; access and I/O errors reach the caller.
    """
    try:
        info = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    return (stat.S_ISLNK(info.st_mode)
            or getattr(info, 'st_reparse_tag', None) == 0xA0000003)  # IO_REPARSE_TAG_MOUNT_POINT
