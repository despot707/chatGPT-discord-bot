"""Windows never enters the Railway Linux volume-initialization path."""

from unittest.mock import Mock

from src import runtime_storage


def test_windows_volume_initialization_is_a_noop(monkeypatch):
    monkeypatch.setattr(runtime_storage.sys, "platform", "win32")
    opened = Mock(side_effect=AssertionError("Windows must not open a Linux volume"))
    monkeypatch.setattr(runtime_storage.os, "open", opened)
    assert runtime_storage.prepare_runtime_storage("/app/data") is False
    opened.assert_not_called()
