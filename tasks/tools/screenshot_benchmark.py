import gc
import hashlib
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import psutil
from PySide6.QtCore import QThread

from module.automation.screenshot import ScreenShot
from module.config import cfg
from module.logger import log


BENCHMARK_DURATION_SECONDS = 60.0
BENCHMARK_TARGET_FPS = 30.0


@dataclass(frozen=True)
class ScreenshotBenchmarkResult:
    duration_seconds: float
    target_fps: float
    expected_frames: int
    attempted_frames: int
    successful_frames: int
    failed_frames: int
    average_ms: float | None
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None
    maximum_ms: float | None
    success_rate: float
    actual_fps: float
    deadline_miss_rate: float
    memory_growth_mb: float
    duplicate_frame_rate: float


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile / 100
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _frame_signature(image: Any) -> bytes:
    sampled_image = image.resize((64, 36))
    digest = hashlib.blake2b(digest_size=8)
    digest.update(str(getattr(image, "mode", "")).encode())
    digest.update(str(getattr(image, "size", "")).encode())
    digest.update(sampled_image.tobytes())
    return digest.digest()


def _read_rss() -> int:
    return psutil.Process().memory_info().rss


def run_screenshot_benchmark(
    duration_seconds: float = BENCHMARK_DURATION_SECONDS,
    target_fps: float = BENCHMARK_TARGET_FPS,
    capture: Callable[..., Any] = ScreenShot.take_screenshot,
    clock: Callable[[], float] = time.perf_counter,
    sleeper: Callable[[float], None] = time.sleep,
    memory_reader: Callable[[], int] = _read_rss,
) -> ScreenshotBenchmarkResult:
    if duration_seconds <= 0:
        raise ValueError("截图测试时长必须大于0")
    if target_fps <= 0:
        raise ValueError("截图测试目标FPS必须大于0")

    gc.collect()
    memory_before = memory_reader()
    interval = 1 / target_fps
    expected_frames = max(1, round(duration_seconds * target_fps))
    start_time = clock()
    end_time = start_time + duration_seconds
    next_slot_time = start_time

    attempted_frames = 0
    successful_frames = 0
    failed_frames = 0
    missed_deadlines = 0
    latencies_ms: list[float] = []
    previous_signature: bytes | None = None
    duplicate_frames = 0
    comparable_frames = 0
    current_slot = 0
    image = None

    while current_slot < expected_frames:
        now = clock()
        if now < next_slot_time:
            sleeper(next_slot_time - now)
            now = clock()

        if now >= next_slot_time + interval:
            skipped_slots = min(
                int((now - next_slot_time) // interval),
                expected_frames - current_slot,
            )
            missed_deadlines += skipped_slots
            current_slot += skipped_slots
            next_slot_time += skipped_slots * interval
            if current_slot >= expected_frames:
                break

        attempt_start = clock()
        try:
            image = capture(gray=False)
        except Exception as error:
            log.debug(f"截图性能测试单次截图异常: {type(error).__name__}: {error}")
            image = None
        attempt_end = clock()

        attempted_frames += 1
        current_slot += 1
        latency_ms = (attempt_end - attempt_start) * 1000
        slot_deadline = next_slot_time + interval
        if attempt_end > slot_deadline:
            missed_deadlines += 1

        if image is None:
            failed_frames += 1
        else:
            successful_frames += 1
            latencies_ms.append(latency_ms)
            try:
                signature = _frame_signature(image)
            except Exception as error:
                log.debug(f"截图性能测试帧签名失败: {type(error).__name__}: {error}")
            else:
                if previous_signature is not None:
                    comparable_frames += 1
                    if signature == previous_signature:
                        duplicate_frames += 1
                previous_signature = signature

        image = None
        next_slot_time = start_time + current_slot * interval

    remaining_time = end_time - clock()
    if remaining_time > 0:
        sleeper(remaining_time)
    actual_duration = max(clock() - start_time, 0)

    gc.collect()
    memory_after = memory_reader()
    average_ms = sum(latencies_ms) / len(latencies_ms) if latencies_ms else None
    success_rate = successful_frames / attempted_frames if attempted_frames else 0
    actual_fps = successful_frames / actual_duration if actual_duration else 0
    deadline_miss_rate = missed_deadlines / expected_frames
    duplicate_frame_rate = duplicate_frames / comparable_frames if comparable_frames else 0

    return ScreenshotBenchmarkResult(
        duration_seconds=actual_duration,
        target_fps=target_fps,
        expected_frames=expected_frames,
        attempted_frames=attempted_frames,
        successful_frames=successful_frames,
        failed_frames=failed_frames,
        average_ms=average_ms,
        p50_ms=_percentile(latencies_ms, 50),
        p95_ms=_percentile(latencies_ms, 95),
        p99_ms=_percentile(latencies_ms, 99),
        maximum_ms=max(latencies_ms) if latencies_ms else None,
        success_rate=success_rate,
        actual_fps=actual_fps,
        deadline_miss_rate=deadline_miss_rate,
        memory_growth_mb=(memory_after - memory_before) / (1024 * 1024),
        duplicate_frame_rate=duplicate_frame_rate,
    )


def _format_milliseconds(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.2f} ms"


def log_screenshot_benchmark_result(result: ScreenshotBenchmarkResult) -> None:
    log.info(
        f"截图性能测试完成: 测试时长={result.duration_seconds:.2f}秒, "
        f"目标FPS={result.target_fps:.2f}, 预期帧数={result.expected_frames}, "
        f"调用次数={result.attempted_frames}, 成功={result.successful_frames}, 失败={result.failed_frames}"
    )
    log.info(f"截图性能测试 - 平均耗时: {_format_milliseconds(result.average_ms)}")
    log.info(f"截图性能测试 - P50: {_format_milliseconds(result.p50_ms)}")
    log.info(f"截图性能测试 - P95: {_format_milliseconds(result.p95_ms)}")
    log.info(f"截图性能测试 - P99: {_format_milliseconds(result.p99_ms)}")
    log.info(f"截图性能测试 - 最大耗时: {_format_milliseconds(result.maximum_ms)}")
    log.info(f"截图性能测试 - 成功率: {result.success_rate:.2%}")
    log.info(f"截图性能测试 - 实际FPS: {result.actual_fps:.2f}")
    log.info(f"截图性能测试 - 期限错过率: {result.deadline_miss_rate:.2%}")
    log.info(f"截图性能测试 - 内存增长: {result.memory_growth_mb:+.2f} MB")
    log.info(f"截图性能测试 - 重复帧率: {result.duplicate_frame_rate:.2%}")


class ScreenshotBenchmarkWorker(QThread):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.finished.connect(self.deleteLater)

    def run(self):
        if not cfg.simulator:
            log.error("截图性能测试仅支持模拟器模式")
            return
        if cfg.simulator_type not in (0, 10):
            log.error(f"截图性能测试不支持当前模拟器类型: {cfg.simulator_type}")
            return

        try:
            from tasks.base.script_task_scheme import init_game

            init_game()
        except Exception as error:
            log.error(f"截图性能测试初始化游戏失败: {error}")
            return

        log.info(
            f"开始截图性能测试: 时长={BENCHMARK_DURATION_SECONDS:.0f}秒, "
            f"目标FPS={BENCHMARK_TARGET_FPS:.0f}, 模拟器类型={cfg.simulator_type}"
        )
        try:
            result = run_screenshot_benchmark()
        except Exception as error:
            log.error(f"截图性能测试失败: {type(error).__name__}: {error}")
            return
        log_screenshot_benchmark_result(result)
