"""最小更新补丁回归；只操作临时目录，不启动实际 AALC。"""

import json
import struct
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import updater


@pytest.fixture
def install(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "update_temp").mkdir()
    (tmp_path / "update_temp/AALC.zip").touch()
    instance = updater.Updater("AALC.zip")
    Path(instance.extract_folder_path).mkdir()
    monkeypatch.setattr(instance, "_prepare_update_payload", lambda _: None)
    monkeypatch.setattr(instance, "terminate_processes", lambda: None)
    return instance, tmp_path


@pytest.mark.parametrize("incremental", [False, True])
def test_update_preserves_config_and_cleans_payload(install, incremental):
    instance, root = install
    (root / "config.yaml").write_text("keep")
    Path(instance.extract_folder_path, "AALC.exe").write_text("new")
    if incremental:
        Path(instance.changes_file_path).write_text(json.dumps({"modified": ["AALC.exe"]}))
    assert instance.run(apply_mode=True)
    assert (root / "AALC.exe").read_text() == "new"
    assert (root / "config.yaml").read_text() == "keep"
    assert not Path(instance.download_file_path).exists()


def test_missing_package_does_not_create_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError):
        updater.Updater("AALC.zip")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("path", ["", "../escape", "C:/absolute", "ok/../../escape"])
def test_manifest_rejects_unsafe_paths(install, path):
    with pytest.raises(updater.UpdateManifestError):
        install[0]._normalize_manifest_path(path)


def test_missing_payload_aborts_before_terminating_app(install, monkeypatch):
    instance, _ = install
    monkeypatch.setattr(instance, "terminate_processes", lambda: pytest.fail("invalid package reached installation"))
    with pytest.raises(updater.UpdateManifestError):
        instance.run()


def test_extractor_is_hidden_and_failure_propagates(install, monkeypatch):
    instance, root = install
    instance.exe_path = str(root / "7za.exe")
    Path(instance.exe_path).touch()

    def fail(command, **kwargs):
        assert kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW
        assert kwargs["check"] is True
        raise subprocess.CalledProcessError(2, command)

    monkeypatch.setattr(updater.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        instance.extract_file()


def test_cleanup_keeps_updater_parent_but_stops_old_main(install, monkeypatch):
    instance, root = install
    killed = []

    def exited_name():
        raise updater.psutil.NoSuchProcess(1003)

    processes = [SimpleNamespace(pid=pid, info={"pid": pid, "name": name}, name=lambda name=name: name,
                                exe=lambda name=name: str(root / name),
                                terminate=lambda pid=pid: killed.append(pid), wait=lambda **_: None)
                 for pid, name in [(1001, "AALC.exe"), (1002, "AALC Updater.exe")]]
    parents = [*processes, SimpleNamespace(pid=1003, name=exited_name)]
    monkeypatch.setattr(updater.psutil, "Process", lambda: SimpleNamespace(parents=lambda: parents))
    monkeypatch.setattr(updater.psutil, "process_iter", lambda **_: processes)
    updater.Updater.terminate_processes(instance)
    assert killed == [1001]


@pytest.mark.parametrize("console", [False, True])
def test_handoff_preserves_gui_updater_or_reports_launch_failure(install, monkeypatch, console):
    instance, root = install
    current = root / instance.updater_name
    current.write_bytes(b"current GUI updater")
    monkeypatch.setattr(sys, "executable", str(current))
    payload = bytearray(222)
    struct.pack_into("<I", payload, 0x3C, 128)
    struct.pack_into("<H", payload, 128 + 24 + 68, 3 if console else 2)
    extracted = Path(instance.extract_folder_path, instance.updater_name)
    extracted.write_bytes(payload)

    def launch(*args, **kwargs):
        assert not console and kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW
        raise OSError("cannot launch updater")

    monkeypatch.setattr(updater.subprocess, "Popen", launch)
    if console:
        assert instance._handoff_to_new_updater() is False
        assert extracted.read_bytes() == current.read_bytes()
    else:
        with pytest.raises(OSError, match="cannot launch"):
            instance._handoff_to_new_updater()
