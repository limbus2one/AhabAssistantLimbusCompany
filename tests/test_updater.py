"""
updater 纯逻辑单元测试

只覆盖无副作用、可跨平台运行的部分：
- parse_updater_args: 兼容旧版 ``<file>`` / ``--apply-update <file>`` 及新增 flag
- UpdateProgressWindow: 禁用状态必须完全无副作用（不建线程、不触 Win32）
- 增量清单路径安全校验（_normalize_manifest_path）回归
"""

from __future__ import annotations

import ctypes
import json
import os
import threading
from types import SimpleNamespace

import pytest

from updater import UpdateManifestError, UpdateProgressWindow, Updater, parse_updater_args

_ARCHIVE_ROOT = os.path.normpath("C:/temp/update_temp/AALC")


def test_parse_plain_file_name():
    args = parse_updater_args(["AALC.7z"])
    assert args.file_name == "AALC.7z"
    assert args.apply_mode is False
    assert args.parent_pid is None
    assert args.no_progress_ui is False


def test_parse_apply_update_form():
    args = parse_updater_args(["--apply-update", "AALC.7z"])
    assert args.file_name == "AALC.7z"
    assert args.apply_mode is True


def test_parse_extra_flags_any_position():
    args = parse_updater_args(["--parent-pid", "12345", "AALC.7z", "--no-progress-ui"])
    assert args.file_name == "AALC.7z"
    assert args.parent_pid == 12345
    assert args.no_progress_ui is True


def test_parse_invalid_parent_pid_is_ignored():
    args = parse_updater_args(["--parent-pid", "not-a-number", "AALC.7z"])
    assert args.parent_pid is None


def test_parse_empty_argv():
    args = parse_updater_args([])
    assert args.file_name is None
    assert args.apply_mode is False


def test_progress_window_disabled_has_no_side_effect():
    window = UpdateProgressWindow(enabled=False)
    assert window.enabled is False
    window.start()
    window.update(stage="x", value=1, maximum=2)
    window.close()
    assert window._thread is None


def test_progress_window_disabled_on_non_windows(monkeypatch):
    import updater

    monkeypatch.setattr(updater.os, "name", "posix")
    window = UpdateProgressWindow(enabled=True)
    assert window.enabled is False
    window.start()
    assert window._thread is None


def _make_updater_for_manifest_check() -> Updater:
    """绕过 __init__ 的目录创建副作用，仅装配路径校验所需字段。"""
    instance = Updater.__new__(Updater)
    instance.extract_folder_path = _ARCHIVE_ROOT
    instance.cover_folder_path = os.path.normpath("C:/install-root")
    return instance


@pytest.mark.parametrize(
    ("parent_pid", "no_progress_ui", "expect_pid", "expect_no_ui"),
    [
        (4242, False, True, False),
        (4242, True, True, True),
        (None, True, False, True),
    ],
)
def test_handoff_forwards_flags(monkeypatch, parent_pid, no_progress_ui, expect_pid, expect_no_ui):
    """handoff 必须把 --parent-pid 转发给新版更新器，否则优雅退出等待在标准路径失效。"""
    import updater

    instance = _make_updater_for_manifest_check()
    instance.updater_name = "AALC Updater.exe"
    instance.apply_updater_name = "AALC Updater.apply.exe"
    instance.temp_path = os.path.normpath("C:/temp/update_temp")
    instance.file_name = "AALC.7z"
    instance.no_progress_ui = no_progress_ui

    monkeypatch.setattr(updater.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(updater.shutil, "copy2", lambda _src, _dst: None)
    # 当前可执行文件与包内新版更新器不同路径，才会真正走 handoff 分支
    monkeypatch.setattr(updater.sys, "argv", [os.path.normpath("C:/elsewhere/AALC Updater.exe")])
    launched: dict = {}
    monkeypatch.setattr(updater.subprocess, "Popen", lambda args, **_kwargs: launched.setdefault("args", args))

    assert instance._handoff_to_new_updater(parent_pid=parent_pid) is True
    args = launched["args"]
    assert "--apply-update" in args and "AALC.7z" in args
    assert ("--parent-pid" in args and "4242" in args) is expect_pid
    assert ("--no-progress-ui" in args) is expect_no_ui


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AALC/AALC.exe", "AALC.exe"),
        ("AALC.exe", "AALC.exe"),
        ("AALC/module/a.py", os.path.join("module", "a.py")),
    ],
)
def test_normalize_manifest_path_strips_archive_root(raw, expected):
    instance = _make_updater_for_manifest_check()
    assert instance._normalize_manifest_path(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "/abs/path", "C:/abs/path", "../escape", "ok/../../escape", "bad\x00name"],
)
def test_normalize_manifest_path_rejects_illegal(raw):
    instance = _make_updater_for_manifest_check()
    with pytest.raises(UpdateManifestError):
        instance._normalize_manifest_path(raw)


def test_update_thread_safe_smoke():
    """update 的共享状态在多线程写入下不应抛异常（禁用态走早退分支）。"""
    window = UpdateProgressWindow(enabled=False)
    errors = []

    def worker():
        try:
            for i in range(100):
                window.update(value=i)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []


def test_handoff_reuses_existing_progress_window(monkeypatch):
    import updater

    instance = _make_updater_for_manifest_check()
    instance.updater_name = "AALC Updater.exe"
    instance.apply_updater_name = "AALC Updater.apply.exe"
    instance.temp_path = os.path.normpath("C:/temp/update_temp")
    instance.file_name = "AALC.7z"
    instance.no_progress_ui = False
    instance.progress = SimpleNamespace(get_window_handle=lambda: 987654)
    monkeypatch.setattr(updater.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(updater.shutil, "copy2", lambda *_args: None)
    launched = {}
    monkeypatch.setattr(updater.subprocess, "Popen", lambda args, **_kwargs: launched.setdefault("args", args))
    assert instance._handoff_to_new_updater() is True
    args = parse_updater_args(launched["args"][1:])
    assert args.progress_window == 987654
    assert args.apply_mode is True


@pytest.mark.parametrize("exit_code", [0, 2])
def test_handoff_keeps_owner_alive_until_apply_finishes(monkeypatch, tmp_path, exit_code):
    monkeypatch.chdir(tmp_path)
    instance = Updater("AALC.7z")
    waited = []
    monkeypatch.setattr(instance, "_prepare_update_payload", lambda _apply: None)
    monkeypatch.setattr(instance, "validate_update_payload", lambda: None)
    monkeypatch.setattr(instance, "_handoff_to_new_updater", lambda **_kwargs: True)
    instance._apply_process = SimpleNamespace(wait=lambda: (waited.append(True), exit_code)[1])
    assert instance.run() is (exit_code == 0)
    assert waited == [True]


@pytest.mark.parametrize("data, accepted", [
    ({"stage": "正在安装", "detail": "版本文件", "value": 1, "maximum": 2}, True),
    ({"stage": []}, False),
    ({"value": "1"}, False),
    ({"unexpected": 1}, False),
    ([], False),
])
def test_progress_relay_validates_and_updates_same_window(data, accepted):
    import updater

    progress = UpdateProgressWindow()
    original_stage = progress._stage
    raw = json.dumps(data).encode("utf-8")
    buffer = ctypes.create_string_buffer(raw)
    message = updater._ProgressMessage(0x41414C43, len(raw), ctypes.cast(buffer, ctypes.c_void_p))
    assert progress._receive_progress(ctypes.byref(message)) is accepted
    assert progress._thread is None
    if accepted:
        assert progress._stage == "正在安装"
        assert progress._log_lines == ["版本文件"]
        assert (progress._value, progress._maximum) == (1, 2)
    else:
        assert progress._stage == original_stage
