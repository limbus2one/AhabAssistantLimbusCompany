from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

import cv2


NAMES = ["battle", "boss", "event", "focused", "risky", "shop", "abnormality"]
COLORS = [
    (80, 220, 80),
    (40, 40, 230),
    (220, 190, 50),
    (210, 70, 190),
    (30, 120, 230),
    (220, 210, 40),
    (160, 60, 210),
]
GROUP_RE = re.compile(r"^(onnx_nodes_\d+)(?:_shift_(\d+))?$")


def annotate(image_path: Path, label_path: Path) -> cv2.typing.MatLike:
    image = cv2.imread(str(image_path))
    if image is None:
        raise RuntimeError(f"无法读取图片：{image_path}")
    height, width = image.shape[:2]
    for line in label_path.read_text(encoding="utf-8").splitlines():
        cls_text, x_text, y_text, w_text, h_text = line.split()
        cls = int(cls_text)
        x, y, box_w, box_h = map(float, (x_text, y_text, w_text, h_text))
        x1 = max(0, round((x - box_w / 2) * width))
        y1 = max(0, round((y - box_h / 2) * height))
        x2 = min(width - 1, round((x + box_w / 2) * width))
        y2 = min(height - 1, round((y + box_h / 2) * height))
        color = COLORS[cls]
        cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
        label = NAMES[cls]
        text_size, _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 1)
        text_y = max(text_size[1] + 3, y1 - 4)
        cv2.rectangle(
            image,
            (x1, text_y - text_size[1] - 3),
            (x1 + text_size[0] + 4, text_y + 2),
            (0, 0, 0),
            -1,
        )
        cv2.putText(image, label, (x1 + 2, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.48, color, 1, cv2.LINE_AA)
    cv2.putText(image, image_path.name, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1, cv2.LINE_AA)
    return image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", type=Path, default=Path(r"E:\image"))
    parser.add_argument("--labels", type=Path, default=Path(r"E:\mirror_yolo\curated\labels"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--groups", nargs="*", help="只渲染指定 group id；省略则渲染全部")
    args = parser.parse_args()

    wanted = set(args.groups or [])
    groups: dict[str, list[tuple[int, Path, Path]]] = {}
    for label_path in args.labels.glob("onnx_nodes_*.txt"):
        match = GROUP_RE.match(label_path.stem)
        if not match:
            continue
        base, shift_text = match.groups()
        group_id = base.removeprefix("onnx_nodes_")
        if wanted and group_id not in wanted:
            continue
        image_path = args.images / f"{label_path.stem}.png"
        if image_path.exists():
            groups.setdefault(group_id, []).append((int(shift_text or 0), image_path, label_path))

    args.output.mkdir(parents=True, exist_ok=True)
    panel_w, panel_h, columns = 640, 360, 3
    for group_id, items in sorted(groups.items()):
        items.sort(key=lambda item: item[0])
        rows = math.ceil(len(items) / columns)
        sheet = cv2.UMat(rows * panel_h, columns * panel_w, cv2.CV_8UC3).get()
        sheet[:] = 0
        for index, (_, image_path, label_path) in enumerate(items):
            panel = cv2.resize(annotate(image_path, label_path), (panel_w, panel_h), interpolation=cv2.INTER_AREA)
            row, column = divmod(index, columns)
            sheet[row * panel_h : (row + 1) * panel_h, column * panel_w : (column + 1) * panel_w] = panel
        output_path = args.output / f"onnx_nodes_{group_id}.jpg"
        if not cv2.imwrite(str(output_path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 93]):
            raise RuntimeError(f"无法写入：{output_path}")
    print(f"已生成 {len(groups)} 张审查图：{args.output}")


if __name__ == "__main__":
    main()
