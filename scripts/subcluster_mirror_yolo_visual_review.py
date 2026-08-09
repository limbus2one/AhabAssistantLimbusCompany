import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np


DEFAULT_CLUSTERS = [
    "abnormality_00",
    "abnormality_02",
    "abnormality_05",
    "abnormality_06",
    "abnormality_10",
    "abnormality_13",
    "abnormality_14",
]


def crop_and_feature(image, values):
    height, width = image.shape[:2]
    x, y, box_width, box_height = values
    left = max(0, int((x - box_width * 0.65) * width))
    top = max(0, int((y - box_height * 0.65) * height))
    right = min(width, int((x + box_width * 0.65) * width))
    bottom = min(height, int((y + box_height * 0.65) * height))
    crop = cv2.resize(image[top:bottom, left:right], (96, 96), interpolation=cv2.INTER_AREA)
    small = cv2.resize(crop, (32, 32), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    center = gray[4:28, 4:28]
    normalized = (center - center.mean()) / (center.std() + 1e-6)
    low_frequency = cv2.dct(normalized)[:8, :8].reshape(-1)
    pixels = cv2.resize(normalized, (16, 16), interpolation=cv2.INTER_AREA).reshape(-1)
    edges = cv2.resize(
        cv2.Canny((center * 255).astype(np.uint8), 60, 140).astype(np.float32) / 255.0,
        (16, 16),
        interpolation=cv2.INTER_AREA,
    ).reshape(-1)
    return crop, np.concatenate((low_frequency, pixels, edges))


def make_atlas(rows, output_path):
    cell, columns = 104, 9
    atlas = np.full((len(rows) * cell, columns * cell, 3), 24, np.uint8)
    for row_index, (subcluster_id, count, crops) in enumerate(rows):
        top = row_index * cell
        cv2.putText(
            atlas,
            f"{subcluster_id} n={count}",
            (4, top + 48),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        for column, crop in enumerate(crops[:8], 1):
            left = column * cell + 4
            atlas[top + 4 : top + 100, left : left + 96] = crop
    cv2.imwrite(str(output_path), atlas, [cv2.IMWRITE_JPEG_QUALITY, 94])


def main():
    parser = argparse.ArgumentParser(description="对镜牢混合视觉簇进行二次聚类")
    parser.add_argument("cluster_ids", nargs="*", default=DEFAULT_CLUSTERS)
    parser.add_argument("--images", type=Path, default=Path(r"E:\image"))
    parser.add_argument("--clusters", type=Path, default=Path(r"E:\mirror_yolo\label_clusters\clusters.json"))
    parser.add_argument("--output", type=Path, default=Path(r"E:\mirror_yolo\abnormality_subclusters"))
    parser.add_argument("--count", type=int, default=30)
    args = parser.parse_args()

    records = {
        record["id"]: record
        for record in json.loads(args.clusters.read_text(encoding="utf-8"))["clusters"]
    }
    members = [member for cluster_id in args.cluster_ids for member in records[cluster_id]["members"]]
    members.sort(key=lambda member: (member["image"], member["line"]))
    items = []
    current_name = None
    image = None
    for member in members:
        if member["image"] != current_name:
            current_name = member["image"]
            image = cv2.imread(str(args.images / current_name))
            if image is None:
                raise RuntimeError(f"无法读取图片: {args.images / current_name}")
        crop, feature = crop_and_feature(image, member["values"])
        items.append({**member, "crop": crop, "feature": feature})

    features = np.asarray([item["feature"] for item in items], np.float32)
    features = (features - features.mean(axis=0)) / (features.std(axis=0) + 1e-6)
    count = min(args.count, len(items))
    cv2.setRNGSeed(0)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 0.01)
    _, labels, centers = cv2.kmeans(features, count, None, criteria, 3, cv2.KMEANS_PP_CENTERS)

    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    records_out = []
    for index in range(count):
        member_indexes = np.flatnonzero(labels.reshape(-1) == index)
        distances = np.linalg.norm(features[member_indexes] - centers[index], axis=1)
        representatives = member_indexes[np.argsort(distances)[:8]]
        subcluster_id = f"mixed_{index:02d}"
        rows.append((subcluster_id, len(member_indexes), [items[i]["crop"] for i in representatives]))
        records_out.append(
            {
                "id": subcluster_id,
                "count": len(member_indexes),
                "members": [
                    {"image": items[i]["image"], "line": items[i]["line"]} for i in member_indexes
                ],
            }
        )

    for page in range(math.ceil(len(rows) / 10)):
        make_atlas(rows[page * 10 : (page + 1) * 10], args.output / f"mixed_{page + 1:02d}.jpg")
    (args.output / "subclusters.json").write_text(
        json.dumps({"clusters": records_out}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"二次聚类完成: {len(items)} -> {count}")


if __name__ == "__main__":
    main()
