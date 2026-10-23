"""隔离主窗口的实际交接方法，避免导入整个 GUI 时启动配置、热键和设备服务。"""

import ast
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def window():
    source = Path(__file__).resolve().parents[1] / "app" / "my_app.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    main_window = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    methods = {"download_and_install", "_try_install_update", "_set_completion_actions_suspended"}
    handler_class = ast.ClassDef(name="Handlers", bases=[], keywords=[],
                                body=[node for node in main_window.body if getattr(node, "name", "") in methods],
                                decorator_list=[])
    timer_calls, launches, closed, suspended = [], [], [], []
    thread_type = type("Thread", (), {"isRunning": lambda self: True})
    namespace = {
        "QTimer": SimpleNamespace(singleShot=lambda _, callback: timer_calls.append(callback)),
        "QThread": thread_type,
        "start_update": lambda name: launches.append(name) or True,
        "suspend_completion_actions": suspended.append,
        "mediator": SimpleNamespace(completion_suppressed_changed=SimpleNamespace(emit=lambda _: None)),
        "log": logging.getLogger(__name__),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[handler_class], type_ignores=[])), str(source), "exec"), namespace)
    instance = namespace["Handlers"]()
    instance.updating = False
    instance._pending_update_file = None
    instance._pending_start_commands = []
    instance._scheduled_start_pending = False
    instance._has_running_script = lambda: False
    instance.resource_sync_coordinator = SimpleNamespace(is_busy=lambda: False)
    instance.tools_interface = SimpleNamespace(tools={})
    instance.setEnabled = lambda enabled: setattr(instance, "enabled", enabled)
    instance.close = lambda: closed.append(True)
    return instance, namespace, timer_calls, launches, closed, suspended


@pytest.mark.parametrize("busy", ["script", "queue", "scheduled", "tool", "resource"])
def test_install_waits_until_all_work_finishes(window, busy):
    instance, _, timers, launches, closed, suspended = window
    if busy == "script":
        instance._has_running_script = lambda: True
    elif busy == "queue":
        instance._pending_start_commands = [["aalc", "start"]]
    elif busy == "scheduled":
        instance._scheduled_start_pending = True
    elif busy == "tool":
        instance.tools_interface.tools = {"production": SimpleNamespace(w=object())}
    else:
        instance.resource_sync_coordinator.is_busy = lambda: True

    instance.download_and_install("AALC.zip")
    assert launches == closed == []
    assert instance.updating is False
    instance._set_completion_actions_suspended(False)
    assert suspended[-1] is True  # 任务收尾不能在等待更新时触发关机

    instance._has_running_script = lambda: False
    instance._pending_start_commands = []
    instance._scheduled_start_pending = False
    instance.tools_interface.tools.clear()
    instance.resource_sync_coordinator.is_busy = lambda: False
    timers.pop()()
    assert launches == ["AALC.zip"]
    assert closed == [True]
    assert instance.updating is True and instance.enabled is False


def test_failed_handoff_restores_interactive_window(window):
    instance, namespace, _, _, closed, suspended = window
    namespace["start_update"] = lambda _: False
    instance.download_and_install("AALC.zip")
    assert closed == []
    assert instance.updating is False and instance.enabled is True
    assert instance._pending_update_file is None
    assert suspended[-1] is False
