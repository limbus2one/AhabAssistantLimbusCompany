from types import SimpleNamespace

from module.automation import automation as automation_module
from module.automation.automation import Automation


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def make_automation_stub():
    return SimpleNamespace(screenshot=None, last_screenshot_time=0.0)


def test_take_screenshot_retries_until_success(monkeypatch):
    clock = FakeClock()
    expected_image = object()
    results = iter([None, None, expected_image])

    monkeypatch.setattr(
        automation_module,
        "time",
        SimpleNamespace(monotonic=clock.monotonic, time=clock.time, sleep=clock.sleep),
    )
    monkeypatch.setattr(automation_module.cfg, "screenshot_interval", 0)
    monkeypatch.setattr(automation_module.ScreenShot, "take_screenshot", lambda gray: next(results))

    stub = make_automation_stub()
    result = Automation.take_screenshot(stub, timeout=5)

    assert result is expected_image
    assert stub.screenshot is expected_image
    assert clock.now == 2.0


def test_take_screenshot_returns_none_on_timeout(monkeypatch):
    clock = FakeClock()
    attempts = 0

    def failed_screenshot(gray):
        nonlocal attempts
        attempts += 1
        return None

    monkeypatch.setattr(
        automation_module,
        "time",
        SimpleNamespace(monotonic=clock.monotonic, time=clock.time, sleep=clock.sleep),
    )
    monkeypatch.setattr(automation_module.cfg, "screenshot_interval", 0)
    monkeypatch.setattr(automation_module.ScreenShot, "take_screenshot", failed_screenshot)

    stub = make_automation_stub()
    result = Automation.take_screenshot(stub, timeout=2)

    assert result is None
    assert attempts == 2
    assert clock.now == 2.0
