import math

from tasks.tools.screenshot_benchmark import run_screenshot_benchmark


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeImage:
    mode = "RGB"
    size = (1, 1)

    def __init__(self, content: bytes) -> None:
        self.content = content

    def tobytes(self) -> bytes:
        return self.content

    def resize(self, size):
        return self


def test_benchmark_calculates_latency_success_fps_memory_and_duplicate_rate():
    clock = FakeClock()
    memory_values = iter([100 * 1024 * 1024, 101 * 1024 * 1024])

    def capture(**kwargs):
        clock.sleep(0.1)
        return FakeImage(b"same-frame")

    result = run_screenshot_benchmark(
        duration_seconds=1,
        target_fps=2,
        capture=capture,
        clock=clock,
        sleeper=clock.sleep,
        memory_reader=lambda: next(memory_values),
    )

    assert result.expected_frames == 2
    assert result.attempted_frames == 2
    assert result.successful_frames == 2
    assert result.failed_frames == 0
    assert math.isclose(result.average_ms, 100)
    assert math.isclose(result.p50_ms, 100)
    assert math.isclose(result.p95_ms, 100)
    assert math.isclose(result.p99_ms, 100)
    assert math.isclose(result.maximum_ms, 100)
    assert result.success_rate == 1
    assert result.actual_fps == 2
    assert result.deadline_miss_rate == 0
    assert result.memory_growth_mb == 1
    assert result.duplicate_frame_rate == 1


def test_benchmark_counts_failures_and_missed_deadlines():
    clock = FakeClock()
    frames = iter([None, FakeImage(b"new-frame")])

    def capture(**kwargs):
        clock.sleep(0.6)
        return next(frames)

    result = run_screenshot_benchmark(
        duration_seconds=1,
        target_fps=2,
        capture=capture,
        clock=clock,
        sleeper=clock.sleep,
        memory_reader=lambda: 0,
    )

    assert result.attempted_frames == 2
    assert result.successful_frames == 1
    assert result.failed_frames == 1
    assert result.success_rate == 0.5
    assert result.deadline_miss_rate == 1
    assert math.isclose(result.actual_fps, 1 / 1.2)
    assert result.duplicate_frame_rate == 0
