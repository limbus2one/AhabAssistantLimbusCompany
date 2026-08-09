import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np


CLUSTERS_PER_CLASS = [40, 25, 30, 20, 15, 20, 15]


def load_crop(image, values):
    height, width = image.shape[:2]
    x, y, box_width, box_height = values
    left = max(0, int((x - box_width * 0.65) * width))
    top = max(0, int((y - box_height * 0.65) * height))
    right = min(width, int((x + box_width * 0.65) * width))
    bottom = min(height, int((y + box_height * 0.65) * height))
    crop = image[top:bottom, left:right]
    return cv2.resize(crop, (96, 96), interpolation=cv2.INTER_AREA)


def feature(crop):
    small = cv2.resize(crop, (64, 64), interpolation=cv2.INTER_AREA)
    gray_u8 = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = gray_u8.astype(np.float32) / 255.0
    normalized = (gray - gray.mean()) / (gray.std() + 1e-6)
    low_frequency = cv2.dct(normalized)[:8, :8].reshape(-1)
    shape = cv2.resize(normalized, (24, 24), interpolation=cv2.INTER_AREA).reshape(-1)
    edges = cv2.resize(
        cv2.Canny(gray_u8, 60, 140).astype(np.float32) / 255.0,
        (24, 24),
        interpolation=cv2.INTER_AREA,
    ).reshape(-1)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    histogram = cv2.calcHist([hsv], [0, 1], None, [8, 4], [0, 180, 0, 256]).reshape(-1)
    histogram /= max(1.0, histogram.sum())
    return np.concatenate((low_frequency, shape, edges, histogram))


def make_atlas(rows, output_path):
    cell = 104
    columns = 9
    atlas = np.full((len(rows) * cell, columns * cell, 3), 24, np.uint8)
    for row_index, (cluster_id, count, crops) in enumerate(rows):
        top = row_index * cell
        cv2.putText(
            atlas,
            f"{cluster_id} n={count}",
            (4, top + 48),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        for column, crop in enumerate(crops[: columns - 1], 1):
            left = column * cell + 4
            atlas[top + 4 : top + 100, left : left + 96] = crop
    cv2.imwrite(str(output_path), atlas, [cv2.IMWRITE_JPEG_QUALITY, 94])


def main():
    parser = argparse.ArgumentParser(description="按视觉相似度聚类镜牢 YOLO 粗标注")
    parser.add_argument("--images", type=Path, default=Path(r"E:\image"))
    parser.add_argument("--labels", type=Path, default=Path(r"E:\mirror_yolo\curated\labels"))
    parser.add_argument("--output", type=Path, default=Path(r"E:\mirror_yolo\label_clusters"))
    parser.add_argument("--class-names", nargs="+")
    args = parser.parse_args()

    classes = (args.labels / "classes.txt").read_text(encoding="utf-8").splitlines()
    items = [[] for _ in classes]
    for label_path in sorted(args.labels.glob("*.txt")):
        if label_path.name == "classes.txt":
            continue
        image_path = args.images / f"{label_path.stem}.png"
        image = cv2.imread(str(image_path))
        if image is None:
            raise RuntimeError(f"无法读取图片: {image_path}")
        for line_index, line in enumerate(label_path.read_text(encoding="utf-8").splitlines()):
            parts = line.split()
            if len(parts) != 5:
                continue
            class_id = int(parts[0])
            values = tuple(map(float, parts[1:]))
            crop = load_crop(image, values)
            items[class_id].append(
                {
                    "image": image_path.name,
                    "line": line_index,
                    "values": values,
                    "crop": crop,
                    "feature": feature(crop),
                }
            )

    args.output.mkdir(parents=True, exist_ok=True)
    cluster_records = []
    cv2.setRNGSeed(0)
    for class_id, class_items in enumerate(items):
        if args.class_names and classes[class_id] not in args.class_names:
            continue
        if not class_items:
            print(f"聚类跳过: {classes[class_id]} 0", flush=True)
            continue
        features = np.asarray([item["feature"] for item in class_items], np.float32)
        reduced = (features - features.mean(axis=0)) / (features.std(axis=0) + 1e-6)
        cluster_count = min(CLUSTERS_PER_CLASS[class_id], len(class_items))
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.01)
        _, labels, centers = cv2.kmeans(
            reduced,
            cluster_count,
            None,
            criteria,
            2,
            cv2.KMEANS_PP_CENTERS,
        )
        rows = []
        for cluster_index in range(cluster_count):
            member_indexes = np.flatnonzero(labels.reshape(-1) == cluster_index)
            distances = np.linalg.norm(reduced[member_indexes] - centers[cluster_index], axis=1)
            representative_indexes = member_indexes[np.argsort(distances)[:8]]
            cluster_id = f"{classes[class_id]}_{cluster_index:02d}"
            rows.append(
                (
                    cluster_id,
                    len(member_indexes),
                    [class_items[index]["crop"] for index in representative_indexes],
                )
            )
            cluster_records.append(
                {
                    "id": cluster_id,
                    "predicted_class": class_id,
                    "count": len(member_indexes),
                    "members": [
                        {
                            "image": class_items[index]["image"],
                            "line": class_items[index]["line"],
                            "values": class_items[index]["values"],
                        }
                        for index in member_indexes
                    ],
                }
            )

        for page in range(math.ceil(len(rows) / 10)):
            page_rows = rows[page * 10 : (page + 1) * 10]
            make_atlas(page_rows, args.output / f"{classes[class_id]}_{page + 1:02d}.jpg")
        print(f"聚类完成: {classes[class_id]} {len(class_items)} -> {cluster_count}", flush=True)

    (args.output / "clusters.json").write_text(
        json.dumps({"classes": classes, "clusters": cluster_records}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
