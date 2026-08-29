import subprocess
import sys
import threading
from pathlib import Path

import updater as updater_module

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_ocr_engine_is_lazy_on_import() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import module.ocr; print('rapidocr' in sys.modules)",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "False"


def test_main_window_import_defers_task_runtime_dependencies() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import app.my_app; "
                "names = ('playsound3', 'cv2', 'module.automation', "
                "'module.game_and_screen', 'app.team_setting_card'); "
                "print(*(name in sys.modules for name in names))"
            ),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip().endswith("False False False False False")


def test_unchanged_config_is_not_rewritten(monkeypatch, tmp_path) -> None:
    from ruamel.yaml import YAML

    from module import EXAMPLE_PATH
    from module.config.config import Config
    from module.config.config_typing import ConfigModel

    config = object.__new__(Config)
    config.yaml = YAML()
    config._lock = threading.RLock()
    config.config_path = tmp_path / "config.yaml"
    config.example_path = Path(EXAMPLE_PATH)
    config.backup_path = tmp_path / "backup"
    config._defaults = config._load_default_config()
    config.config = ConfigModel(**config._defaults)
    with config.config_path.open("w", encoding="utf-8") as file:
        config.yaml.dump(config.config.model_dump(), file)

    save_calls = []
    monkeypatch.setattr(config, "backup_config", lambda: None)
    monkeypatch.setattr(config, "_save_config", lambda: save_calls.append(True))

    config._load_config()

    assert save_calls == []


def test_updater_and_extractor_are_windowless(monkeypatch, tmp_path) -> None:
    assert "console=False" in (REPO_ROOT / "updater.spec").read_text(encoding="utf-8")

    updater = updater_module.Updater.__new__(updater_module.Updater)
    updater.exe_path = str(tmp_path / "7za.exe")
    updater.download_file_path = str(tmp_path / "update.7z")
    updater.temp_path = str(tmp_path)
    Path(updater.exe_path).touch()
    updater._reset_extraction_workspace = lambda: None
    run_calls = []
    monkeypatch.setattr(updater_module.subprocess, "run", lambda *args, **kwargs: run_calls.append((args, kwargs)))

    updater.extract_file()

    assert run_calls[0][1]["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_updater_restarts_without_prompt_and_marks_post_update(monkeypatch, tmp_path) -> None:
    app_path = tmp_path / "AALC.exe"
    app_path.touch()
    updater = updater_module.Updater.__new__(updater_module.Updater)
    updater.cover_folder_path = str(tmp_path)
    popen_calls = []

    monkeypatch.setattr(updater_module.subprocess, "Popen", lambda *args, **kwargs: popen_calls.append((args, kwargs)))

    updater.restart_application()

    assert popen_calls == [
        (
            ([str(app_path), "--post-update"],),
            {
                "cwd": str(tmp_path),
                "creationflags": subprocess.DETACHED_PROCESS,
            },
        )
    ]


def test_successful_update_flow_restarts_without_input(monkeypatch) -> None:
    updater = updater_module.Updater.__new__(updater_module.Updater)
    calls = []

    def fail_on_input(*_args, **_kwargs):
        raise AssertionError("successful update must not prompt")

    monkeypatch.setattr(updater, "_prepare_update_payload", lambda apply_mode: calls.append(("prepare", apply_mode)))
    monkeypatch.setattr(updater, "validate_update_payload", lambda: calls.append(("validate", None)))
    monkeypatch.setattr(updater, "_handoff_to_new_updater", lambda: False)
    monkeypatch.setattr(updater, "terminate_processes", lambda: calls.append(("terminate", None)))
    monkeypatch.setattr(updater, "cover_folder", lambda: calls.append(("cover", None)))
    monkeypatch.setattr(updater, "cleanup", lambda: calls.append(("cleanup", None)))
    monkeypatch.setattr(updater, "restart_application", lambda: calls.append(("restart", None)))
    monkeypatch.setattr("builtins.input", fail_on_input)

    assert updater.run() is True
    assert calls == [
        ("prepare", False),
        ("validate", None),
        ("terminate", None),
        ("cover", None),
        ("cleanup", None),
        ("restart", None),
    ]


def test_post_update_start_skips_network_checks() -> None:
    from app.resource_sync_coordinator import ResourceSyncCoordinator

    class CoordinatorStub:
        _startup_argv = ["AALC.exe", "--post-update"]
        continued = False

        def _continue_startup_sequence_once(self):
            self.continued = True

        def _start_resource_sync_gate_check(self, **_kwargs):
            raise AssertionError("post-update startup must not start a network check")

    coordinator = CoordinatorStub()
    ResourceSyncCoordinator.start_startup_check(coordinator)

    assert coordinator.continued is True
