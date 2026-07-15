from tasks.base.flow_watchdog import FlowRetryWatchdog


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_watchdog_only_recovers_after_timeout():
    clock = FakeClock()
    calls = []

    def recovery(**kwargs):
        calls.append(kwargs)

    watchdog = FlowRetryWatchdog(
        "测试流程",
        timeout=300,
        recovery_timeout=60,
        clock=clock,
        recovery=recovery,
    )

    clock.now = 299.9
    assert watchdog.check() is True
    assert calls == []

    clock.now = 300.0
    assert watchdog.check() is True
    assert calls == [{"timeout": 60.0, "source": "流程看门狗.测试流程"}]
    assert watchdog.timeout_count == 1


def test_progress_resets_idle_timeout():
    clock = FakeClock()
    calls = []

    def recovery(**kwargs):
        calls.append(kwargs)

    watchdog = FlowRetryWatchdog("测试流程", timeout=300, clock=clock, recovery=recovery)
    clock.now = 250
    watchdog.progress("页面推进")
    clock.now = 500

    assert watchdog.check() is True
    assert calls == []


def test_restart_result_is_propagated_and_recovery_timer_is_reset():
    clock = FakeClock()
    calls = []

    def recovery(**kwargs):
        calls.append(kwargs)
        return False

    watchdog = FlowRetryWatchdog("测试流程", timeout=300, clock=clock, recovery=recovery)
    clock.now = 300

    assert watchdog.check() is False
    assert len(calls) == 1

    clock.now = 301
    assert watchdog.check() is True
    assert len(calls) == 1
