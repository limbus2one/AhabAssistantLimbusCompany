import argparse
import json
import math
from pathlib import Path

import cv2


COLORS = [
    (70, 220, 70),
    (40, 40, 230),
    (220, 180, 40),
    (220, 80, 220),
    (30, 110, 255),
    (230, 210, 60),
    (180, 80, 255),
]


def read_labels(path, width, height):
    boxes = []
    if not path.exists():
        return boxes
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) != 5:
            continue
        class_id = int(parts[0])
        x, y, w, h = map(float, parts[1:])
        boxes.append(
            (
                class_id,
                int((x - w / 2) * width),
                int((y - h / 2) * height),
                int((x + w / 2) * width),
                int((y + h / 2) * height),
            )
        )
    return boxes


def annotate(image, boxes, classes, filename):
    for class_id, left, top, right, bottom in boxes:
        color = COLORS[class_id % len(COLORS)]
        label = classes[class_id]
        cv2.rectangle(image, (left, top), (right, bottom), color, 3)
        (text_width, text_height), baseline = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, 0.75, 2
        )
        text_top = max(0, top - text_height - baseline - 4)
        cv2.rectangle(
            image,
            (left, text_top),
            (left + text_width + 6, text_top + text_height + baseline + 4),
            (0, 0, 0),
            -1,
        )
        cv2.putText(
            image,
            label,
            (left + 3, text_top + text_height + 1),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.75,
            color,
            2,
            cv2.LINE_AA,
        )
    cv2.rectangle(image, (0, 0), (780, 36), (0, 0, 0), -1)
    cv2.putText(
        image,
        filename,
        (8, 26),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return image


def main():
    parser = argparse.ArgumentParser(description="生成镜牢 YOLO 标注场景组总览")
    parser.add_argument("--images", type=Path, default=Path(r"E:\image"))
    parser.add_argument("--labels", type=Path, default=Path(r"E:\mirror_yolo\curated\labels"))
    parser.add_argument("--groups", type=Path, default=Path(r"E:\mirror_yolo\curated\groups.json"))
    parser.add_argument("--output", type=Path, default=Path(r"E:\mirror_yolo\review_sheets"))
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    classes = (args.labels / "classes.txt").read_text(encoding="utf-8").splitlines()
    groups = json.loads(args.groups.read_text(encoding="utf-8"))
    if args.limit is not None:
        groups = dict(list(groups.items())[: args.limit])
    args.output.mkdir(parents=True, exist_ok=True)

    index = []
    for number, (group_id, filenames) in enumerate(groups.items(), 1):
        frames = []
        detection_count = 0
        for filename in filenames:
            image = cv2.imread(str(args.images / filename))
            if image is None:
                raise RuntimeError(f"无法读取图片: {args.images / filename}")
            height, width = image.shape[:2]
            boxes = read_labels(args.labels / f"{Path(filename).stem}.txt", width, height)
            detection_count += len(boxes)
            frames.append(annotate(image, boxes, classes, filename))

        if len(frames) == 1:
            sheet = frames[0]
        else:
            thumb_width, thumb_height, columns = 640, 360, 3
            rows = math.ceil(len(frames) / columns)
            sheet = frames[0].copy()
            sheet = cv2.resize(sheet, (thumb_width, thumb_height))
            sheet = cv2.copyMakeBorder(
                sheet,
                0,
                thumb_height * rows - thumb_height,
                0,
                thumb_width * columns - thumb_width,
                cv2.BORDER_CONSTANT,
                value=(20, 20, 20),
            )
            for frame_index, frame in enumerate(frames):
                row, column = divmod(frame_index, columns)
                thumb = cv2.resize(frame, (thumb_width, thumb_height), interpolation=cv2.INTER_AREA)
                top, left = row * thumb_height, column * thumb_width
                sheet[top : top + thumb_height, left : left + thumb_width] = thumb

        output_path = args.output / f"{group_id}.jpg"
        cv2.imwrite(str(output_path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 92])
        index.append(
            {
                "group": group_id,
                "sheet": output_path.name,
                "images": filenames,
                "detections": detection_count,
            }
        )
        if number % 25 == 0 or number == len(groups):
            print(f"总览进度: {number}/{len(groups)}", flush=True)

    (args.output / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
