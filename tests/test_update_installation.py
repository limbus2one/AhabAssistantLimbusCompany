"""真实临时文件上的安装故障注入；禁止启动/终止实际 AALC。"""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import updater
from update_support import InstallationLock, incomplete_update_path


@pytest.fixture
def installation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    instance = updater.Updater("AALC.zip")
    Path(instance.extract_folder_path).mkdir()
    launches = []
    errors = []
    monkeypatch.setattr(updater, "relaunch_app", lambda *_: launches.append(True))
    monkeypatch.setattr(updater, "show_error_dialog", lambda *args: errors.append(args))
    monkeypatch.setattr(instance, "_prepare_update_payload", lambda _: None)
    monkeypatch.setattr(instance, "terminate_processes", lambda: None)
    monkeypatch.setattr(updater.time, "sleep", lambda _: None)
    return instance, tmp_path, launches, errors


def put(root, name, content="old"):
    path = Path(root) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def manifest(instance, **changes):
    Path(instance.changes_file_path).write_text(json.dumps(changes), encoding="utf-8")


def test_missing_incremental_file_aborts_before_deleting_old_files(installation):
    instance, root, launches, errors = installation
    old = put(root, "obsolete.txt")
    manifest(instance, deleted=["obsolete.txt"], added=["missing.txt"])
    assert instance.run(apply_mode=True) is False
    assert old.read_text() == "old"
    assert "missing.txt" in errors[0][1]
    assert launches == [True]
    assert not Path(incomplete_update_path(root)).exists()


def test_empty_full_package_does_not_delete_images(installation):
    instance, root, _, _ = installation
    image = put(root, "assets/images/old.png")
    assert instance.run(apply_mode=True) is False
    assert image.read_text() == "old"


def test_partial_install_failure_restores_modified_and_deleted_files(installation, monkeypatch):
    instance, root, launches, _ = installation
    for name in ("obsolete.txt", "removed/nested.txt", "changed.txt"):
        put(root, name)
    for name in ("changed.txt", "new/file.txt", "bad.txt"):
        put(instance.extract_folder_path, name, "new")
    manifest(instance, deleted=["obsolete.txt"], deleted_dir=["removed"],
             added=["new/file.txt"], modified=["changed.txt", "bad.txt"])
    real_copy = updater.shutil.copy2

    def fail_copy(source, target):
        if str(source).endswith("bad.txt"):
            Path(target).write_text("partial")
            raise OSError("injected disk failure")
        return real_copy(source, target)

    monkeypatch.setattr(updater.shutil, "copy2", fail_copy)
    assert instance.run(apply_mode=True) is False
    for name in ("obsolete.txt", "removed/nested.txt", "changed.txt"):
        assert (root / name).read_text() == "old"
    assert not (root / "new").exists()
    assert not (root / "bad.txt").exists()
    assert not list(root.rglob(".aalc-update-*"))
    assert not Path(incomplete_update_path(root)).exists()
    assert launches == [True]


def test_failed_restore_keeps_marker_and_backups_without_restart(installation, monkeypatch):
    instance, root, launches, _ = installation
    put(root, "changed.txt")
    put(instance.extract_folder_path, "changed.txt", "new")
    manifest(instance, modified=["changed.txt"])
    real_replace = updater.os.replace

    def fail_replace(source, target):
        if Path(source).name.startswith(".aalc-update-") or Path(source).parent == Path(instance._backup_dir):
            raise PermissionError("injected locked target")
        return real_replace(source, target)

    monkeypatch.setattr(updater.os, "replace", fail_replace)
    assert instance.run(apply_mode=True) is False
    marker = json.loads(Path(incomplete_update_path(root)).read_text())
    assert Path(marker["backup"]).is_dir()
    assert (Path(marker["backup"]) / "0").read_text() == "old"
    assert launches == []


def test_full_install_replaces_images_and_preserves_user_config(installation):
    instance, root, launches, _ = installation
    put(root, "assets/images/stale.png")
    put(root, "config.yaml", "user configuration")
    put(root, "_internal/obsolete.dll")
    put(instance.extract_folder_path, "AALC.exe", "new binary")
    put(instance.extract_folder_path, "assets/images/new.png", "new image")
    put(instance.extract_folder_path, "_internal/current.dll", "new library")
    assert instance.run(apply_mode=True) is True
    assert (root / "AALC.exe").read_text() == "new binary"
    assert (root / "assets/images/new.png").read_text() == "new image"
    assert not (root / "assets/images/stale.png").exists()
    assert (root / "config.yaml").read_text() == "user configuration"
    assert not (root / "_internal/obsolete.dll").exists()
    assert (root / "_internal/current.dll").read_text() == "new library"
    assert not Path(incomplete_update_path(root)).exists()
    assert not Path(instance._backup_dir).exists()
    assert launches == [True]
    # 仅在下次更新清理成功会话；失败会话仍保留供恢复。
    failed_session = root / "update_temp/session-failed"
    put(failed_session, "backup/old.txt")
    updater.Updater("AALC.zip")
    assert not Path(instance.work_dir).exists()
    assert (failed_session / "backup/old.txt").exists()


def test_incomplete_install_requires_full_package(installation):
    instance, root, launches, errors = installation
    put(instance.extract_folder_path, "changed.txt", "new")
    manifest(instance, modified=["changed.txt"])
    marker = Path(incomplete_update_path(root))
    marker.write_text("previous failure")
    assert instance.run(apply_mode=True) is False
    assert marker.read_text() == "previous failure"
    assert not (root / "changed.txt").exists()
    assert "完整更新包" in errors[0][1]
    assert launches == []


def test_install_lock_excludes_another_process_and_releases_on_exit(tmp_path):
    lock = InstallationLock(tmp_path)
    assert lock.acquire()
    code = (
        "import sys; from update_support import InstallationLock; "
        "lock = InstallationLock(sys.argv[1]); sys.exit(0 if lock.acquire() else 7)"
    )
    command = [sys.executable, "-c", code, str(tmp_path)]
    try:
        assert subprocess.run(command, timeout=15).returncode == 7
    finally:
        lock.close()
    assert subprocess.run(command, timeout=15).returncode == 0
    assert lock.acquire()  # 子进程未调用 close，系统也应释放锁
    lock.close()


def test_locked_install_does_not_touch_files_or_relaunch(installation, monkeypatch):
    instance, root, launches, _ = installation
    put(instance.extract_folder_path, "AALC.exe", "new binary")
    old = put(root, "AALC.exe")
    lock = InstallationLock(root)
    assert lock.acquire()
    monkeypatch.setattr(updater, "_INSTALL_LOCK_WAIT_SECONDS", 0)
    try:
        assert instance.run(apply_mode=True) is False
    finally:
        lock.close()
    assert old.read_text() == "old"
    assert launches == []


def test_process_cleanup_is_scoped_to_current_install(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    instance = updater.Updater("AALC.zip")
    killed = []

    def process(pid, name, path):
        return SimpleNamespace(pid=pid, info={"pid": pid, "name": name}, exe=lambda: str(path),
                               terminate=lambda: killed.append(pid), wait=lambda **_: None)

    processes = [
        process(1001, "AALC.exe", tmp_path.parent / "another-install" / "AALC.exe"),
        process(1002, "adb.exe", tmp_path / "assets/binary/adb.exe"),
        process(1003, "AALC Updater.apply.exe", tmp_path / "update_temp/session-other/updater.exe"),
    ]
    monkeypatch.setattr(updater.psutil, "process_iter", lambda **_: processes)
    monkeypatch.setattr(updater, "_ancestor_pids", lambda: set())
    instance.terminate_processes()
    assert killed == [1002]


def test_update_sessions_do_not_share_extraction_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first = updater.Updater("AALC.zip")
    second = updater.Updater("AALC.zip")
    assert first.work_dir != second.work_dir
    assert first.extract_folder_path != second.extract_folder_path
    assert first.changes_file_path != second.changes_file_path
    args = updater.parse_updater_args(["AALC.zip", "--work-dir", first.work_dir])
    assert updater.Updater(args.file_name, work_dir=args.work_dir).work_dir == first.work_dir
    with pytest.raises(updater.UpdatePayloadError):
        updater.Updater("AALC.zip", work_dir=os.fspath(tmp_path))


def test_progress_starts_after_parent_exit(installation, monkeypatch):
    instance, _, _, _ = installation
    calls = []
    monkeypatch.setattr(instance, "_wait_for_parent_exit", lambda _: calls.append("parent-exited"))
    progress = updater._NullProgress()
    monkeypatch.setattr(progress, "start", lambda: calls.append("show"))
    instance.run(apply_mode=True, parent_pid=123, progress=progress)
    assert calls == ["parent-exited", "show"]


def test_handoff_forwards_isolated_workspace(installation, monkeypatch):
    instance, _, _, _ = installation
    put(instance.extract_folder_path, instance.updater_name, "updater")
    launched = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda argv, **_: launched.append(argv))
    assert instance._handoff_to_new_updater(parent_pid=123)
    args = updater.parse_updater_args(launched[0][1:])
    assert args.work_dir == instance.work_dir
    assert args.parent_pid == 123


@pytest.mark.parametrize("blocked_by, exit_code", [("lock", 1), ("incomplete", 2)])
def test_packaged_startup_gate_blocks_before_loading_gui(tmp_path, monkeypatch, blocked_by, exit_code):
    source = Path(__file__).resolve().parents[1] / "main.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    guard = next(node for node in tree.body if isinstance(node, ast.If) and "frozen" in ast.unparse(node.test))
    monkeypatch.chdir(tmp_path)
    lock = InstallationLock(tmp_path)
    if blocked_by == "lock":
        assert lock.acquire()
    else:
        put(tmp_path, "update_temp/install-incomplete.json", "unfinished")
    messages, cleanup = [], []
    monkeypatch.setitem(sys.modules, "ctypes", SimpleNamespace(windll=SimpleNamespace(
        user32=SimpleNamespace(MessageBoxW=lambda *args: messages.append(args)))))
    monkeypatch.setitem(sys.modules, "pyuac", SimpleNamespace(isUserAdmin=lambda: True))
    namespace = {
        "__name__": "__main__", "os": os,
        "sys": SimpleNamespace(frozen=True, argv=["AALC.exe"], exit=sys.exit),
        "atexit": SimpleNamespace(register=cleanup.append),
        "send_args_to_existing_instance": lambda *_: False,
    }
    try:
        with pytest.raises(SystemExit) as error:
            exec(compile(ast.Module(body=[guard], type_ignores=[]), str(source), "exec"), namespace)
        assert error.value.code == exit_code
        assert len(messages) == 1
    finally:
        for close in cleanup:
            close()
        lock.close()
