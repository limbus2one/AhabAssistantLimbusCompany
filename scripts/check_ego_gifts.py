"""从已有图片或已连接的 MuMu ADB 设备识别饰品，保存原图、标注图和 JSON。

在仓库根目录运行：
    python scripts/check_ego_gifts.py --serial emulator-5554
    python scripts/check_ego_gifts.py --image screenshot.png
"""

import argparse
import hashlib
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from module.yolo import yolo  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", type=Path, help="已有截图")
    source.add_argument("--serial", help="已连接的 ADB 设备，例如 emulator-5554")
    parser.add_argument("--conf", type=float, default=yolo.DEFAULT_CONF_THRESHOLD)
    parser.add_argument("--output", type=Path, default=Path("logs/ego_gifts"))
    args = parser.parse_args()
    if not 0 <= args.conf <= 1:
        parser.error("--conf 必须在 0 到 1 之间")
    logging.basicConfig(level=logging.INFO)

    if args.image:
        with Image.open(args.image) as image:
            frame = image.convert("RGB")
    else:
        from adbutils import adb

        frame = adb.device(serial=args.serial).screenshot().convert("RGB")

    if not yolo.available:
        raise RuntimeError("E.G.O 模型或类别元数据不可用")
    start = perf_counter()
    detections = yolo.find_gifts(frame, conf=args.conf)
    elapsed_ms = (perf_counter() - start) * 1000
    if not yolo.available:
        raise RuntimeError("E.G.O 模型加载失败，请查看日志")

    args.output.mkdir(parents=True, exist_ok=True)
    frame.save(args.output / "screenshot.png")
    annotated = np.array(frame)
    for detection in detections:
        x1, y1, x2, y2 = map(round, detection.xyxy)
        label = f"{detection.name_en or detection.class_name} {detection.confidence:.1%}"
        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
        origin = (x1, max(20, y1 - 10))
        cv2.putText(annotated, label, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(annotated, label, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1, cv2.LINE_AA)
    Image.fromarray(annotated).save(args.output / "annotated.png")
    report = {
        "source": str(args.image or args.serial),
        "size": frame.size,
        "model_sha256": hashlib.sha256(Path(yolo.model_path).read_bytes()).hexdigest(),
        "conf": args.conf,
        "elapsed_ms_including_model_load": elapsed_ms,
        "detections": [
            {**asdict(item), "system": item.system, "center": item.center} for item in detections
        ],
    }
    result = json.dumps(report, ensure_ascii=False, indent=2)
    (args.output / "detections.json").write_text(result + "\n", encoding="utf-8")
    print(result)  # noqa: T201


if __name__ == "__main__":
    main()
