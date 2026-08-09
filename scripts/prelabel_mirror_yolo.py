import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort


CLASSES = [
    "battle",
    "boss",
    "event",
    "focused",
    "risky",
    "shop",
    "abnormality",
]


def predict(session, image_path, confidence, iou):
    image = cv2.imread(str(image_path))
    if image is None:
        raise RuntimeError(f"无法读取图片: {image_path}")

    height, width = image.shape[:2]
    length = max(height, width)
    square = np.zeros((length, length, 3), np.uint8)
    square[:height, :width] = image
    blob = cv2.dnn.blobFromImage(square, 1 / 255, (640, 640), swapRB=True)
    outputs = session.run(None, {session.get_inputs()[0].name: blob})[0][0].T

    boxes, scores, class_ids = [], [], []
    for output in outputs:
        class_id = int(np.argmax(output[4:]))
        score = float(output[4 + class_id])
        if score < confidence:
            continue
        boxes.append(
            [
                float(output[0] - output[2] / 2),
                float(output[1] - output[3] / 2),
                float(output[2]),
                float(output[3]),
            ]
        )
        scores.append(score)
        class_ids.append(class_id)

    labels = []
    scale = length / 640
    for raw_index in cv2.dnn.NMSBoxes(boxes, scores, 0, iou):
        index = int(np.asarray(raw_index).reshape(-1)[0])
        x, y, box_width, box_height = boxes[index]
        left = max(0.0, x * scale)
        top = max(0.0, y * scale)
        right = min(float(width), (x + box_width) * scale)
        bottom = min(float(height), (y + box_height) * scale)
        if right <= left or bottom <= top:
            continue
        labels.append(
            (
                class_ids[index],
                (left + right) / 2 / width,
                (top + bottom) / 2 / height,
                (right - left) / width,
                (bottom - top) / height,
            )
        )
    return labels


def main():
    parser = argparse.ArgumentParser(description="用现有镜牢 ONNX 模型生成可人工复核的 YOLO 预标注")
    parser.add_argument("--images", type=Path, default=Path(r"E:\image"))
    parser.add_argument("--model", type=Path, default=Path("assets/model/best.onnx"))
    parser.add_argument("--output", type=Path, default=Path(r"E:\mirror_yolo\prelabels"))
    parser.add_argument("--confidence", type=float, default=0.15)
    parser.add_argument("--iou", type=float, default=0.4)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    images = sorted(args.images.glob("*.png"))
    if args.limit is not None:
        images = images[: args.limit]
    if not images:
        raise SystemExit(f"没有找到 PNG 图片: {args.images}")

    labels_dir = args.output / "labels"
    labels_dir.mkdir(parents=True, exist_ok=True)
    classes_text = "\n".join(CLASSES) + "\n"
    (args.output / "classes.txt").write_text(classes_text, encoding="utf-8")
    (labels_dir / "classes.txt").write_text(classes_text, encoding="utf-8")

    session = ort.InferenceSession(str(args.model), providers=["CPUExecutionProvider"])
    class_counts = Counter()
    groups = defaultdict(list)
    completed = 0

    for index, image_path in enumerate(images, 1):
        label_path = labels_dir / f"{image_path.stem}.txt"
        if args.overwrite or not label_path.exists():
            labels = predict(session, image_path, args.confidence, args.iou)
            label_path.write_text(
                "".join(f"{class_id} {x:.6f} {y:.6f} {w:.6f} {h:.6f}\n" for class_id, x, y, w, h in labels),
                encoding="utf-8",
            )
        else:
            labels = [line.split() for line in label_path.read_text(encoding="utf-8").splitlines() if line.strip()]

        class_counts.update(int(label[0]) for label in labels)
        group_id = re.sub(r"_shift_\d+$", "", image_path.stem)
        groups[group_id].append(image_path.name)
        completed += 1
        if index % 25 == 0 or index == len(images):
            print(f"预标注进度: {index}/{len(images)}", flush=True)

    summary = {
        "images": completed,
        "groups": len(groups),
        "confidence": args.confidence,
        "iou": args.iou,
        "detections": sum(class_counts.values()),
        "class_counts": {CLASSES[class_id]: class_counts[class_id] for class_id in range(len(CLASSES))},
    }
    (args.output / "groups.json").write_text(
        json.dumps(dict(groups), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
