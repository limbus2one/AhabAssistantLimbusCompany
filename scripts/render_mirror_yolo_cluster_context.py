import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np


def main():
    parser = argparse.ArgumentParser(description="生成镜牢 YOLO 可疑聚类的整图上下文")
    parser.add_argument("cluster_ids", nargs="+")
    parser.add_argument("--images", type=Path, default=Path(r"E:\image"))
    parser.add_argument("--clusters", type=Path, default=Path(r"E:\mirror_yolo\label_clusters\clusters.json"))
    parser.add_argument("--output", type=Path, default=Path(r"E:\mirror_yolo\cluster_context"))
    args = parser.parse_args()

    records = {
        record["id"]: record
        for record in json.loads(args.clusters.read_text(encoding="utf-8"))["clusters"]
    }
    args.output.mkdir(parents=True, exist_ok=True)
    cell_width, cell_height, columns, page_size = 480, 270, 4, 16

    for cluster_id in args.cluster_ids:
        record = records[cluster_id]
        frames = []
        for member in record["members"]:
            image = cv2.imread(str(args.images / member["image"]))
            if image is None:
                raise RuntimeError(f"无法读取图片: {args.images / member['image']}")
            height, width = image.shape[:2]
            x, y, box_width, box_height = member["values"]
            left = max(0, int((x - box_width / 2) * width))
            top = max(0, int((y - box_height / 2) * height))
            right = min(width, int((x + box_width / 2) * width))
            bottom = min(height, int((y + box_height / 2) * height))
            cv2.rectangle(image, (left, top), (right, bottom), (0, 255, 255), 8)
            cv2.putText(
                image,
                cluster_id,
                (left, max(40, top - 10)),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.2,
                (0, 255, 255),
                3,
                cv2.LINE_AA,
            )
            crop_left = max(0, left - 20)
            crop_top = max(0, top - 20)
            crop_right = min(width, right + 20)
            crop_bottom = min(height, bottom + 20)
            crop = cv2.resize(
                image[crop_top:crop_bottom, crop_left:crop_right],
                (120, 120),
                interpolation=cv2.INTER_AREA,
            )
            frame = cv2.resize(image, (cell_width, cell_height), interpolation=cv2.INTER_AREA)
            frame[:120, :120] = crop
            cv2.rectangle(frame, (0, 0), (cell_width, 24), (0, 0, 0), -1)
            cv2.putText(
                frame,
                member["image"],
                (4, 18),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
            frames.append(frame)

        for page in range(math.ceil(len(frames) / page_size)):
            page_frames = frames[page * page_size : (page + 1) * page_size]
            rows = math.ceil(len(page_frames) / columns)
            sheet = np.full((rows * cell_height, columns * cell_width, 3), 20, np.uint8)
            for index, frame in enumerate(page_frames):
                row, column = divmod(index, columns)
                top, left = row * cell_height, column * cell_width
                sheet[top : top + cell_height, left : left + cell_width] = frame
            cv2.imwrite(
                str(args.output / f"{cluster_id}_{page + 1:02d}.jpg"),
                sheet,
                [cv2.IMWRITE_JPEG_QUALITY, 93],
            )
        print(f"上下文完成: {cluster_id} {len(frames)}", flush=True)


if __name__ == "__main__":
    main()
