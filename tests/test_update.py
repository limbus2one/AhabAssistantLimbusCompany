"""更新关键路径回归：临时目录安装、故障恢复、交接及下载；不接触实际 AALC。"""

import ctypes
import json
import struct
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

import updater
from module.update import download
from update_support import InstallationLock, incomplete_update_path


def write(root, name, text="old"):
    path = Path(root) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_updater(root, subsystem):
    path = Path(root, "AALC Updater.exe")
    data = bytearray(222)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 128)
    data[128:132] = b"PE\0\0"
    struct.pack_into("<H", data, 128 + 24 + 68, subsystem)
    path.write_bytes(data)
    return path


@pytest.fixture
def install(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(tmp_path, "update_temp/AALC.zip", "archive placeholder")
    instance = updater.Updater("AALC.zip")
    Path(instance.extract_folder_path).mkdir()
    monkeypatch.setattr(instance, "_prepare_update_payload", lambda _: None)
    monkeypatch.setattr(instance, "terminate_processes", lambda: None)
    monkeypatch.setattr(updater, "show_error_dialog", lambda *_: None)
    monkeypatch.setattr(updater.time, "sleep", lambda _: None)
    launches = []
    monkeypatch.setattr(updater, "relaunch_app", lambda *_: launches.append(True))
    return instance, tmp_path, launches


@pytest.mark.parametrize("manifest", [None, {"deleted": ["old.txt"], "added": ["missing.txt"]}])
def test_incomplete_payload_does_not_modify_installation(install, manifest):
    instance, root, launches = install
    old = write(root, "old.txt")
    if manifest is not None:
        Path(instance.changes_file_path).write_text(json.dumps(manifest))
    assert instance.run(apply_mode=True) is False
    assert old.read_text() == "old" and launches == [True]
    assert not Path(incomplete_update_path(root)).exists()


@pytest.mark.parametrize("restore_fails", [False, True])
def test_install_failure_restores_files_or_preserves_backup(install, monkeypatch, restore_fails):
    instance, root, launches = install
    write(root, "old.txt")
    write(root, "removed/nested.txt")
    for name in ("old.txt", "new/file.txt", "bad.txt"):
        write(instance.extract_folder_path, name, "new")
    Path(instance.changes_file_path).write_text(json.dumps({
        "deleted_dir": ["removed"], "modified": ["old.txt", "new/file.txt", "bad.txt"],
    }))
    real_copy, real_replace = updater.shutil.copy2, updater.os.replace

    def copy(source, target):
        if Path(source).name == "bad.txt":
            Path(target).write_text("partial")
            raise OSError("injected copy failure")
        return real_copy(source, target)

    def replace(source, target):
        if restore_fails and Path(source).parent == Path(instance._backup_dir):
            raise PermissionError("injected restore failure")
        return real_replace(source, target)

    monkeypatch.setattr(updater.shutil, "copy2", copy)
    monkeypatch.setattr(updater.os, "replace", replace)
    assert instance.run(apply_mode=True) is False
    assert not list(root.rglob(".aalc-update-*"))
    assert Path(incomplete_update_path(root)).exists() is restore_fails
    if restore_fails:
        assert Path(instance._backup_dir, "journal.jsonl").exists() and launches == []
    else:
        assert (root / "old.txt").read_text() == (root / "removed/nested.txt").read_text() == "old"
        assert not (root / "new").exists() and launches == [True]


def test_full_update_replaces_managed_files_and_preserves_config(install):
    instance, root, launches = install
    write(root, "config.yaml")
    for name in ("assets/images/old.png", "_internal/old.dll"):
        write(root, name)
    for name in ("AALC.exe", "assets/images/new.png", "_internal/new.dll"):
        write(instance.extract_folder_path, name, "new")
    assert instance.run(apply_mode=True)
    assert (root / "AALC.exe").read_text() == "new" and launches == [True]
    assert (root / "config.yaml").read_text() == "old"
    assert not (root / "assets/images/old.png").exists() and not (root / "_internal/old.dll").exists()
    backups = [json.loads(line) for line in Path(instance._backup_dir, "journal.jsonl").read_text().splitlines()]
    for entry in backups:
        old_name = "old.png" if Path(entry["target"]).name == "images" else "old.dll"
        assert Path(entry["backup"], old_name).read_text() == "old"
    updater._update_work_dir()
    assert not Path(instance.work_dir).exists()


def test_full_update_failure_restores_resource_and_runtime_directories(install, monkeypatch):
    instance, root, launches = install
    old_files = ("AALC.exe", "assets/images/old.png", "_internal/old.dll")
    for name in old_files:
        write(root, name)
    for name in ("AALC.exe", "assets/images/new.png", "_internal/new.dll", "zzz/fail.txt"):
        write(instance.extract_folder_path, name, "new")
    original_copy = updater.shutil.copy2

    def copy(source, destination):
        if Path(source).name == "fail.txt":
            raise OSError("injected full update failure")
        return original_copy(source, destination)

    monkeypatch.setattr(updater.shutil, "copy2", copy)
    assert instance.run(apply_mode=True) is False
    assert all((root / name).read_text() == "old" for name in old_files)
    assert not (root / "assets/images/new.png").exists()
    assert not (root / "_internal/new.dll").exists()
    assert not Path(incomplete_update_path(root)).exists() and launches == [True]


def test_handoff_keeps_window_and_forwards_arguments(install, monkeypatch):
    instance, _, _ = install
    write(instance.extract_folder_path, "AALC.exe")
    write_updater(instance.extract_folder_path, 2)
    window = updater._NullProgress()
    window.get_window_handle = lambda: 123
    launched = []
    monkeypatch.setattr(instance, "_wait_for_parent_exit", lambda _: None)
    monkeypatch.setattr(updater.subprocess, "Popen", lambda args, **_: launched.append(args) or SimpleNamespace(wait=lambda: 2))
    assert instance.run(parent_pid=456, progress=window) is False
    args = updater.parse_updater_args(launched[0][1:])
    assert (args.apply_mode, args.parent_pid, args.progress_window, args.work_dir) == (True, 456, 123, instance.work_dir)


@pytest.mark.parametrize("operation", ["copy", "launch"])
def test_handoff_failure_aborts_without_installing(install, monkeypatch, operation):
    instance, root, launches = install
    old = write(root, "AALC.exe")
    write(instance.extract_folder_path, "AALC.exe", "new")
    write_updater(instance.extract_folder_path, 2)
    messages = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "argv", ["AALC Updater.exe", "AALC.zip"])
    monkeypatch.setattr(updater, "Updater", lambda *args, **kwargs: instance)
    monkeypatch.setattr(updater, "_setup_logging", lambda: instance.log_path)
    monkeypatch.setattr(updater, "_maybe_relaunch_from_temp", lambda _: None)
    monkeypatch.setattr(updater, "UpdateProgressWindow", lambda **_: updater._NullProgress())
    monkeypatch.setattr(updater, "show_error_dialog", lambda *args: messages.append(args))

    def fail(*args, **kwargs):
        raise OSError("injected handoff failure")

    monkeypatch.setattr(updater.subprocess, "Popen", fail)
    if operation == "copy":
        monkeypatch.setattr(updater.shutil, "copy2", fail)
    with pytest.raises(SystemExit) as error:
        updater.main()
    assert error.value.code == 2 and "injected handoff failure" in messages[0][1]
    assert old.read_text() == "old" and launches == [True]
    assert not instance._install_touched
    assert not Path(incomplete_update_path(root)).exists()
    assert not Path(instance.work_dir, "completed.flag").exists()


@pytest.mark.parametrize("incremental", [False, True])
def test_legacy_console_updater_cannot_take_over_or_replace_windowed_updater(install, monkeypatch, incremental):
    instance, root, launches = install
    current = write_updater(root, 2)
    expected = current.read_bytes()
    monkeypatch.setattr(sys, "executable", str(current))
    write_updater(instance.extract_folder_path, 3)
    write(root, "AALC.exe")
    write(instance.extract_folder_path, "AALC.exe", "new")
    if incremental:
        Path(instance.changes_file_path).write_text(json.dumps({"modified": ["AALC.exe", instance.updater_name]}))
    monkeypatch.setattr(instance, "_handoff_to_new_updater", lambda **_: pytest.fail("legacy updater was launched"))
    assert instance.run() is True
    assert current.read_bytes() == expected and launches == [True]
    assert (root / "AALC.exe").read_text() == "new"
    assert Path(instance._backup_dir, "journal.jsonl").exists()


@pytest.mark.parametrize("args", [[], ["--apply-update"]])
def test_missing_package_argument_exits_before_creating_workspace(tmp_path, monkeypatch, args):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "argv", ["AALC Updater.exe", *args])
    messages = []
    monkeypatch.setattr(updater, "show_error_dialog", lambda *args: messages.append(args))
    with pytest.raises(SystemExit) as error:
        updater.main()
    assert error.value.code == 2 and "缺少更新包" in messages[0][1]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("args, message", [
    (["AALC.zip"], "更新包不存在"),
    (["--apply-update", "AALC.zip"], "更新包不存在"),
    (["../outside.zip"], "更新包必须"),
])
def test_invalid_package_preserves_previous_backup(tmp_path, monkeypatch, args, message):
    monkeypatch.chdir(tmp_path)
    write(tmp_path, "outside.zip")
    old = write(tmp_path, "AALC.exe")
    backup = write(tmp_path, "update_temp/session-previous/backup/AALC.exe")
    completed = write(tmp_path, "update_temp/session-previous/completed.flag", "")
    before = set(tmp_path.rglob("*"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "argv", ["AALC Updater.exe", *args])
    monkeypatch.setattr(updater, "_maybe_relaunch_from_temp", lambda _: pytest.fail("invalid package was accepted"))
    monkeypatch.setattr(updater, "_setup_logging", lambda: None)
    messages = []
    monkeypatch.setattr(updater, "show_error_dialog", lambda *args: messages.append(args))
    with pytest.raises(SystemExit) as error:
        updater.main()
    assert error.value.code == 2 and message in messages[0][1]
    assert old.read_text() == backup.read_text() == "old" and completed.exists()
    assert set(tmp_path.rglob("*")) == before


@pytest.mark.parametrize("operation", ["unlink", "remove"])
def test_extraction_cleanup_failure_retries_without_using_stale_files(install, monkeypatch, operation):
    instance, _, _ = install
    write(instance.extract_folder_path, "stale.txt")
    write(instance.work_dir, "changes.json", "{}")
    failures, extractions = [], []

    def fail(*args, **kwargs):
        failures.append(True)
        raise PermissionError("cleanup blocked")

    monkeypatch.setattr(updater.os, operation, fail)
    monkeypatch.setattr(updater.shutil, "unpack_archive", lambda *_: extractions.append(True))
    with pytest.raises(updater.UpdatePayloadError, match="cleanup blocked"):
        updater.Updater._prepare_update_payload(instance, False)
    assert len(failures) == updater._EXTRACT_MAX_ATTEMPTS and not extractions


@pytest.mark.parametrize("path", ["", "/abs", "C:/abs", "../escape", "ok/../../escape", "bad\0name"])
def test_manifest_rejects_unsafe_paths(install, path):
    with pytest.raises(updater.UpdateManifestError):
        install[0]._normalize_manifest_path(path)


def test_process_cleanup_does_not_kill_other_installations(install, monkeypatch):
    instance, root, _ = install
    killed = []
    processes = [SimpleNamespace(pid=pid, info={"pid": pid, "name": name}, exe=lambda path=path: str(path),
                                terminate=lambda pid=pid: killed.append(pid), wait=lambda **_: None)
                 for pid, name, path in [(1001, "AALC.exe", root.parent / "other/AALC.exe"),
                                         (1002, "adb.exe", root / "assets/adb.exe"),
                                         (1003, "AALC Updater.exe", root / "update_temp/updater.exe")]]
    monkeypatch.setattr(updater.psutil, "process_iter", lambda **_: processes)
    monkeypatch.setattr(updater, "_ancestor_pids", lambda: set())
    updater.Updater.terminate_processes(instance)
    assert killed == [1002]


def test_install_lock_excludes_other_processes(tmp_path):
    lock = InstallationLock(tmp_path)
    command = [sys.executable, "-c", "import sys; from update_support import InstallationLock; "
               "lock=InstallationLock(sys.argv[1]); sys.exit(0 if lock.acquire() else 7)", str(tmp_path)]
    assert lock.acquire()
    try:
        assert subprocess.run(command, timeout=15).returncode == 7
    finally:
        lock.close()
    assert subprocess.run(command, timeout=15).returncode == 0


@pytest.mark.parametrize("succeeds", [True, False])
def test_download_only_publishes_complete_package(tmp_path, monkeypatch, succeeds):
    target = write(tmp_path, "AALC.zip")
    calls, progress = [], []
    def response(*_, **__):
        assert target.read_text() == "old"
        calls.append(True)
        body = b"complete" if succeeds and len(calls) == 2 else b"short"
        return nullcontext(SimpleNamespace(headers={"content-length": "8"}, raise_for_status=lambda: None,
                                           iter_content=lambda **_: iter([body])))
    monkeypatch.setattr(download.requests, "get", response)
    monkeypatch.setattr(download.time, "sleep", lambda _: None)
    with nullcontext() if succeeds else pytest.raises(requests.RequestException):
        download.download_update("https://example.test/update", str(target), progress.append, lambda _: None)
    assert target.read_text() == ("complete" if succeeds else "old")
    assert len(calls) == (2 if succeeds else 3) and (100 in progress) is succeeds
    assert not Path(f"{target}.temp").exists()


@pytest.mark.parametrize("data, accepted", [({"stage": "安装", "value": 1}, True), ({"value": "bad"}, False)])
def test_progress_relay_validates_payload(data, accepted):
    window = updater.UpdateProgressWindow()
    raw = json.dumps(data).encode()
    buffer = ctypes.create_string_buffer(raw)
    message = updater._ProgressMessage(0x41414C43, len(raw), ctypes.cast(buffer, ctypes.c_void_p))
    assert window._receive_progress(ctypes.byref(message)) is accepted


def test_progress_stage_does_not_keep_previous_completion_or_details():
    window = updater.UpdateProgressWindow()
    window.update(stage="正在安装", detail="已写入 50 / 50", value=50, maximum=50)
    window.update(stage="正在恢复旧版本...")
    assert (window._detail, window._value, window._maximum) == ("", 0, 0)
    window.update(detail="恢复旧文件")
    window.update(detail="")
    assert window._detail == ""
