import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest


def api():
    assert Path("src/runtime_storage.py").exists(), "root-volume startup repair is missing"
    return importlib.import_module("src.runtime_storage")


def test_prepare_root_volume_allows_nonroot_sqlite_after_drop(tmp_path):
    api()
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("requires root subprocess for privilege-drop integration")
    tmp_path.chmod(0o755)
    for p in tmp_path.parents:
        if p.name.startswith("pytest"):
            p.chmod(0o755)
    volume = tmp_path / "data"
    volume.mkdir(mode=0o755)
    legacy = volume / "memory.sqlite3"
    legacy.touch(mode=0o600)
    probe = (
        "import os,sqlite3; os.setgid(10001); os.setuid(10001); sqlite3.connect(%r).execute('CREATE TABLE a(x)')"
        % str(legacy)
    )
    before = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert before.returncode != 0 and "unable to open database" in before.stderr
    script = (
        "from src.runtime_storage import prepare_runtime_storage; import os,sqlite3; "
        f"prepare_runtime_storage({str(volume)!r}); "
        "assert os.geteuid()==10001; assert os.getegid()==10001; "
        f"sqlite3.connect({str(legacy)!r}).execute('CREATE TABLE a(x)')"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert volume.stat().st_uid == 10001 and legacy.stat().st_uid == 10001


def test_symlinked_mount_refused_before_ownership_change(tmp_path):
    api()
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("requires root")
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "volume"
    link.symlink_to(target, target_is_directory=True)
    command = f"from src.runtime_storage import prepare_runtime_storage; prepare_runtime_storage({str(link)!r})"
    r = subprocess.run([sys.executable, "-c", command], capture_output=True, text=True)
    assert r.returncode != 0
    assert target.stat().st_uid == 0


def test_no_mount_does_not_change_filesystem(monkeypatch):
    m = api()
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    assert m.prepare_runtime_storage() is False


def test_nonroot_startup_is_noop(monkeypatch, tmp_path):
    m = api()
    monkeypatch.setattr(m.os, "geteuid", lambda: 10001, raising=False)
    assert m.prepare_runtime_storage(str(tmp_path)) is False
