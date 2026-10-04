"""Check configurable retry limits without starting the game."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[1]


def load_source(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def runtime(monkeypatch):
    cfg = SimpleNamespace(retry_count=0, retry_timeout=90)
    auto = Mock()
    auto.find_element.return_value = False
    auto.click_element.return_value = False
    auto.take_screenshot.return_value = object()
    monkeypatch.setitem(sys.modules, "module.config", SimpleNamespace(cfg=cfg))
    monkeypatch.setitem(sys.modules, "module.automation", SimpleNamespace(auto=auto))
    monkeypatch.setitem(sys.modules, "module.logger", SimpleNamespace(log=Mock()))
    monkeypatch.setitem(
        sys.modules, "module.game_and_screen", SimpleNamespace(screen=Mock())
    )
    monkeypatch.setitem(
        sys.modules, "module.decorator.decorator",
        SimpleNamespace(begin_and_finish_time_log=lambda **kw: lambda fn: fn),
    )
    monkeypatch.setitem(
        sys.modules, "utils.utils", SimpleNamespace(check_game_running=lambda: True)
    )
    return cfg, auto


def test_config_defaults_and_invalid_limits():
    model = load_source("retry_config_model", "module/config/config_typing.py").ConfigModel
    defaults = YAML().load((ROOT / "assets/config/config.example.yaml").read_text(encoding="utf-8"))
    config = model(**defaults)
    assert (config.retry_count, config.retry_timeout) == (0, 90)
    for field, value in (("retry_count", -1), ("retry_timeout", 0), ("retry_timeout", -1)):
        with pytest.raises(ValidationError):
            model(**{**defaults, field: value})


@pytest.mark.parametrize("count", [0, 3, 50])
def test_reward_recognition_uses_configured_count(runtime, monkeypatch, count):
    cfg, auto = runtime
    cfg.retry_count = count
    monkeypatch.setitem(sys.modules, "tasks.base.retry", SimpleNamespace(retry=lambda: True))
    reward = load_source("retry_reward", "tasks/mirror/reward_card.py")
    assert reward.get_reward_card() is False
    # Initial recognition attempt plus the configured number of retries.
    assert auto.take_screenshot.call_count == (count or 30) + 1


@pytest.mark.parametrize("default", [15, 30, 250])
def test_default_counts_and_custom_override(runtime, default):
    from tasks.base import get_retry_count

    cfg, _ = runtime
    assert get_retry_count(default) == default
    cfg.retry_count = 7
    assert get_retry_count(default) == 7


def test_configured_timeout_and_explicit_battle_timeout(runtime, monkeypatch):
    cfg, _ = runtime
    cfg.retry_timeout = 120
    retry = load_source("retry_runtime", "tasks/base/retry.py")
    monkeypatch.setattr(retry.time, "time", lambda: 1119)
    retry.kill_game = Mock()
    retry.restart_game = Mock()
    assert retry.check_times(1000, logs=False) is False
    monkeypatch.setattr(retry.time, "time", lambda: 1121)
    assert retry.check_times(1000, logs=False) is True
    retry.kill_game.assert_called_once()
    retry.restart_game.assert_called_once()
    assert retry.check_times(1000, timeout=900, logs=False) is False
    retry.kill_game.assert_called_once()


@pytest.mark.parametrize("timeout, attempts", [(90, 2), (180, 3)])
def test_main_menu_loading_uses_configured_timeout(runtime, monkeypatch, timeout, attempts):
    cfg, auto = runtime
    cfg.retry_count = 2
    cfg.retry_timeout = timeout
    retry = Mock(return_value=True)
    monkeypatch.setitem(
        sys.modules, "tasks.base.retry",
        SimpleNamespace(
            retry=retry,
            click_title_screen_safely=lambda: None,
            ensure_simulator_game_started=lambda: False,
        ),
    )
    monkeypatch.setitem(
        sys.modules, "tasks.mirror.reward_card", SimpleNamespace(get_reward_card=lambda: None)
    )
    menu = load_source("retry_menu", "tasks/base/back_init_menu.py")
    auto.find_element.side_effect = lambda name, **kw: name == "base/waiting_assets.png"
    clock = iter([100, 100, 195, 281])
    monkeypatch.setattr(menu, "monotonic", lambda: next(clock))
    assert menu.back_init_menu(allow_restart=False) is False
    assert retry.call_count == attempts
