"""独立测试当前模拟器画面中的镜牢 bus 行识别。

默认会把 bus 拖到屏幕右侧和纵向中间基准，但绝不会点击 bus、节点或“进入”按钮。

示例：
    python scripts/test_bus_row_detection.py
    python scripts/test_bus_row_detection.py --mode hard
    python scripts/test_bus_row_detection.py --no-normalize-view
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from PIL import ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from module.automation import auto  # noqa: E402
from module.config import cfg  # noqa: E402
from tasks.base.script_task_scheme import init_game  # noqa: E402
from tasks.mirror.search_road import identify_bus_row  # noqa: E402
from utils.path_manager import path_manager  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(description="识别当前镜牢地图中 bus 所在行，并输出全部节点信息")
    parser.add_argument(
        "--mode",
        choices=("auto", "normal", "hard"),
        default="auto",
        help="镜牢模式；auto 使用配置中的 hard_mirror，默认 auto",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "debug" / "bus-row",
        help="JSON 和标注截图输出目录",
    )
    parser.add_argument(
        "--no-normalize-view",
        action="store_false",
        dest="normalize_view",
        help="禁止画面归一化；默认会把 bus 拖到屏幕右侧和纵向中间基准",
    )
    parser.set_defaults(normalize_view=True)
    return parser.parse_args()


def resolve_hard_mode(mode):
    if mode == "hard":
        return True
    if mode == "normal":
        return False
    return bool(cfg.hard_mirror)


def save_annotated_screenshot(result, destination):
    if auto.screenshot is None:
        return None

    image = auto.screenshot.convert("RGB")
    draw = ImageDraw.Draw(image)
    scale = cfg.set_win_size / 1440
    radius = max(5, int(12 * scale))

    if result.bus_position:
        bus_x, bus_y = result.bus_position
        draw.ellipse(
            (bus_x - radius, bus_y - radius, bus_x + radius, bus_y + radius),
            outline=(255, 215, 0),
            width=max(2, int(4 * scale)),
        )
        draw.text((bus_x + radius, bus_y - radius), "BUS", fill=(255, 215, 0))

    for index, node in enumerate(result.nodes, start=1):
        node_x, node_y = node.screen_pos
        color = (80, 220, 120) if node.geometry_valid else (255, 90, 90)
        draw.rectangle(
            (node_x - radius, node_y - radius, node_x + radius, node_y + radius),
            outline=color,
            width=max(2, int(3 * scale)),
        )
        label = f"{index}:{node.node_type} C{node.column} R{node.relative_row}"
        draw.text((node_x + radius, node_y + radius), label, fill=color)

    row_label = result.row.value if result.row else "unknown"
    draw.text((15, 15), f"bus_row={row_label} confidence={result.confidence:.2f}", fill=(255, 215, 0))
    image.save(destination)
    return destination


def main():
    args = parse_args()
    hard_mode = resolve_hard_mode(args.mode)

    path_manager.initialize_paths()
    init_game()
    result = identify_bus_row(
        hard_mode=hard_mode,
        complete_current_node=False,
        normalize_view=args.normalize_view,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = args.output_dir / f"bus-row-{timestamp}.json"
    image_path = args.output_dir / f"bus-row-{timestamp}.png"

    payload = result.to_dict()
    payload["annotated_screenshot"] = str(image_path)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    save_annotated_screenshot(result, image_path)

    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.write(f"JSON: {json_path}\n")
    sys.stdout.write(f"截图: {image_path}\n")

    if result.bus_position is None:
        return 2
    if result.row is None:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
