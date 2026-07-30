"""Benchmark AALC's PC screenshot and template-matching paths.

Keep the Limbus Company window visible (or enable background screenshots), then run:

    python scripts/benchmark_pc_vision.py \
        --target battle/pause_assets.png \
        --crop 1250 8 1475 85 --iterations 100

The crop uses screenshot coordinates: left, top, right, bottom.  It must contain
the target.  The benchmark never clicks or otherwise changes the game state.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

@dataclass(frozen=True)
class Result:
    name: str
    samples_ms: list[float]

    @property
    def mean(self) -> float:
        return statistics.fmean(self.samples_ms)

    @property
    def p50(self) -> float:
        return percentile(self.samples_ms, 0.50)

    @property
    def p95(self) -> float:
        return percentile(self.samples_ms, 0.95)

    @property
    def stddev(self) -> float:
        return statistics.pstdev(self.samples_ms)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def measure(name: str, operation: Callable[[], object], warmup: int, iterations: int) -> Result:
    for _ in range(warmup):
        operation()

    samples = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        operation()
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    return Result(name, samples)


def print_result(result: Result) -> None:
    print(
        f"{result.name:<31} mean={result.mean:8.3f} ms  "
        f"p50={result.p50:8.3f} ms  p95={result.p95:8.3f} ms  "
        f"std={result.stddev:8.3f} ms"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark AALC PC screenshot and find_element performance")
    parser.add_argument("--target", required=True, help="AALC image path, e.g. battle/pause_assets.png")
    parser.add_argument(
        "--crop",
        nargs=4,
        type=int,
        metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
        required=True,
        help="ROI in screenshot coordinates; it must contain the target",
    )
    parser.add_argument("--iterations", type=int, default=100, help="Measured iterations per case (default: 100)")
    parser.add_argument("--warmup", type=int, default=10, help="Warm-up iterations per case (default: 10)")
    parser.add_argument("--threshold", type=float, default=0.8)
    parser.add_argument(
        "--model",
        choices=("clam", "normal", "aggressive"),
        default="aggressive",
        help="Use aggressive to make full-screen vs ROI comparison explicit",
    )
    parser.add_argument("--color", action="store_true", help="Capture color instead of grayscale")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    # Importing AALC initializes configuration, OCR and input backends. Keep it
    # after argparse so --help remains instant and side-effect free.
    from module.automation import auto
    from module.automation.screenshot import ScreenShot
    from module.config import cfg
    from module.game_and_screen import screen

    if args.iterations < 2 or args.warmup < 0:
        raise SystemExit("--iterations must be >= 2 and --warmup must be >= 0")
    if args.model != "aggressive":
        raise SystemExit("region screenshot comparison requires --model aggressive (local ROI coordinates)")
    left, top, right, bottom = args.crop
    if left < 0 or top < 0 or right <= left or bottom <= top:
        raise SystemExit("invalid --crop: expected non-negative LEFT TOP RIGHT BOTTOM with positive size")

    if not screen.init_handle():
        raise SystemExit(f"game window not found (configured title: {cfg.get_value('game_title_name')!r})")

    gray = not args.color

    def raw_screenshot():
        image = ScreenShot.background_screenshot(gray=gray)
        if image is None:
            raise RuntimeError("screenshot failed")
        return image

    def raw_region_screenshot():
        image = ScreenShot.background_screenshot(gray=gray, region=args.crop)
        if image is None:
            raise RuntimeError("region screenshot failed")
        return image

    # Use one immutable screenshot for both pure matching cases. This excludes
    # capture time and keeps the full-screen/ROI comparison fair.
    auto.screenshot = raw_screenshot()
    width, height = auto.screenshot.size
    if right > width or bottom > height:
        raise SystemExit(f"crop {args.crop} exceeds screenshot size {width}x{height}")

    find_kwargs = {
        "threshold": args.threshold,
        "model": args.model,
        "take_screenshot": False,
    }

    screenshot_result = measure("background screenshot: full", raw_screenshot, args.warmup, args.iterations)
    region_screenshot_result = measure(
        "background screenshot: ROI",
        raw_region_screenshot,
        args.warmup,
        args.iterations,
    )
    full_match = measure(
        "pure find_element: full screen",
        lambda: auto.find_element(args.target, **find_kwargs),
        args.warmup,
        args.iterations,
    )
    crop_match = measure(
        "pure find_element: ROI",
        lambda: auto.find_element(args.target, my_crop=args.crop, **find_kwargs),
        args.warmup,
        args.iterations,
    )

    def capture_and_find(my_crop=None):
        # Deliberately use the raw backend here. auto.take_screenshot() applies
        # cfg.screenshot_interval, which would measure throttling rather than
        # screenshot + matching work.
        auto.screenshot = raw_screenshot()
        return auto.find_element(args.target, my_crop=my_crop, **find_kwargs)

    full_e2e = measure(
        "screenshot + find: full screen",
        capture_and_find,
        args.warmup,
        args.iterations,
    )
    crop_e2e = measure(
        "full screenshot + find: ROI",
        lambda: capture_and_find(args.crop),
        args.warmup,
        args.iterations,
    )

    def capture_region_and_find():
        # A region screenshot uses local coordinates with (0, 0) at the ROI's
        # top-left, so do not apply the original global crop a second time.
        auto.screenshot = raw_region_screenshot()
        return auto.find_element(args.target, **find_kwargs)

    region_e2e = measure(
        "region output + find",
        capture_region_and_find,
        args.warmup,
        args.iterations,
    )

    auto.screenshot = raw_screenshot()
    full_hit = auto.find_element(args.target, **find_kwargs)
    crop_hit = auto.find_element(args.target, my_crop=args.crop, **find_kwargs)
    auto.screenshot = raw_region_screenshot()
    region_hit = auto.find_element(args.target, **find_kwargs)

    print(f"window={width}x{height}, gray={gray}, model={args.model}, target={args.target}")
    print(f"crop={args.crop}, iterations={args.iterations}, warmup={args.warmup}")
    print(f"target hit: full={full_hit!r}, cropped full={crop_hit!r}, region capture={region_hit!r}")
    print(f"configured screenshot_interval={cfg.screenshot_interval!r}s (excluded from timings)")
    print()
    for result in (
        screenshot_result,
        region_screenshot_result,
        full_match,
        crop_match,
        full_e2e,
        crop_e2e,
        region_e2e,
    ):
        print_result(result)

    print()
    if crop_match.mean:
        print(f"pure matching ROI speedup: {full_match.mean / crop_match.mean:.2f}x")
    if region_screenshot_result.mean:
        print(f"background capture speedup: {screenshot_result.mean / region_screenshot_result.mean:.2f}x")
    if crop_e2e.mean:
        print(f"full-capture ROI speedup:   {full_e2e.mean / crop_e2e.mean:.2f}x")
    if region_e2e.mean:
        print(f"region-output E2E speedup:  {full_e2e.mean / region_e2e.mean:.2f}x")
    print(f"ROI area / full area:       {((right-left)*(bottom-top))/(width*height):.2%}")

    if not full_hit:
        print("WARNING: target was not found on the captured frame; timings represent a miss path.")
    elif not crop_hit:
        print("WARNING: full screen found the target but ROI did not; the ROI does not contain the target.")
        return 2
    elif not region_hit:
        print("WARNING: cropped full screen found the target but the region capture did not.")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
