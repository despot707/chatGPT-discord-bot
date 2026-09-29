"""Repair the mounted data directory before irrevocably dropping root privileges.

Railway mounts volumes as root; RAILWAY_RUN_UID=0 permits this startup-only
repair. The Discord/LLM clients still run as the image's unprivileged bot user.
No recursive chown, symlink traversal, or world-writable chmod. Obsolete legacy-memory files are removed only when the feature is explicitly disabled.
"""

from __future__ import annotations

import logging
import os
import stat
import sys

logger = logging.getLogger(__name__)


def prepare_runtime_storage(mount_path: str | None = None) -> bool:
    """Fix this app's SQLite volume, then drop to botuser (10001:10001)."""
    if sys.platform == "win32":
        return False
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return False
    path = mount_path or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
    if not path:
        return False
    # Production uses the Railway-managed mount; tests can supply an isolated dir.
    if mount_path is None and path != "/app/data":
        raise RuntimeError("Unexpected data volume mount; refusing privileged initialization")
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fchown(fd, 10001, 10001)
        os.fchmod(fd, stat.S_IMODE(os.fstat(fd).st_mode) | 0o700)
        long_term_enabled = os.environ.get("ENABLE_LONG_TERM_MEMORY", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        legacy_memory_names = {
            "memory.sqlite3",
            "memory.sqlite3-wal",
            "memory.sqlite3-shm",
            "memory.sqlite3-journal",
        }
        for entry in os.scandir(fd):
            if not entry.is_file(follow_symlinks=False):
                continue
            if not long_term_enabled and entry.name in legacy_memory_names:
                os.unlink(entry.name, dir_fd=fd)
                logger.info("Removed obsolete disabled long-term-memory storage: %s", entry.name)
                continue
            if any(
                entry.name.endswith(suffix)
                for suffix in (".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".sqlite3-journal")
            ):
                os.chown(entry.name, 10001, 10001, dir_fd=fd, follow_symlinks=False)
    finally:
        os.close(fd)
    os.setgroups([])
    os.setgid(10001)
    os.setuid(10001)
    os.environ["HOME"] = "/home/botuser"
    logger.info(
        "Persistent data volume initialized; bot runtime uid=%s gid=%s", os.geteuid(), os.getegid()
    )
    return True
