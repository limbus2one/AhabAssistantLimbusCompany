import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import cv2
import numpy as np


UNKNOWN = "__unknown__"


def crop_feature(image, values):
    height, width = image.shape[:2]
    x, y, box_width, box_height = values
    left = max(0, int((x - box_width * 0.65) * width))
    top = max(0, int((y - box_height * 0.65) * height))
    right = min(width, int((x + box_width * 0.65) * width))
    bottom = min(height, int((y + box_height * 0.65) * height))
    crop = cv2.resize(image[top:bottom, left:right], (32, 32), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    center = gray[4:28, 4:28]
    normalized = (center - center.mean()) / (center.std() + 1e-6)
    low_frequency = cv2.dct(normalized)[:8, :8].reshape(-1)
    center_pixels = cv2.resize(normalized, (16, 16), interpolation=cv2.INTER_AREA).reshape(-1)
    center_edges = cv2.resize(
        cv2.Canny((center * 255).astype(np.uint8), 60, 140).astype(np.float32) / 255.0,
        (16, 16),
        interpolation=cv2.INTER_AREA,
    ).reshape(-1)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    histogram = cv2.calcHist([hsv], [0, 1], None, [8, 4], [0, 180, 0, 256]).reshape(-1)
    histogram /= max(1.0, histogram.sum())
    return np.concatenate((low_frequency, center_pixels, center_edges, histogram))


def main():
    parser = argparse.ArgumentParser(description="应用镜牢节点粗标注的视觉复核结果")
    parser.add_argument("--images", type=Path, default=Path(r"E:\image"))
    parser.add_argument("--labels", type=Path, default=Path(r"E:\mirror_yolo\curated\labels"))
    parser.add_argument("--clusters", type=Path, default=Path(r"E:\mirror_yolo\label_clusters\clusters.json"))
    parser.add_argument(
        "--decisions", type=Path, default=Path("scripts/mirror_yolo_cluster_decisions.json")
    )
    parser.add_argument(
        "--subclusters",
        type=Path,
        default=Path(r"E:\mirror_yolo\abnormality_subclusters\subclusters.json"),
    )
    parser.add_argument("--report", type=Path, default=Path(r"E:\mirror_yolo\curated\visual_review.json"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    classes = (args.labels / "classes.txt").read_text(encoding="utf-8").splitlines()
    class_ids = {name: index for index, name in enumerate(classes)}
    cluster_data = json.loads(args.clusters.read_text(encoding="utf-8"))["clusters"]
    decisions = json.loads(args.decisions.read_text(encoding="utf-8"))
    subcluster_data = json.loads(args.subclusters.read_text(encoding="utf-8"))["clusters"]
    member_subclusters = {
        (member["image"], member["line"]): record["id"]
        for record in subcluster_data
        for member in record["members"]
    }
    records = {record["id"]: record for record in cluster_data}
    member_clusters = {
        (member["image"], member["line"]): record["id"]
        for record in cluster_data
        for member in record["members"]
    }

    delete_clusters = set(decisions["delete"])
    delete_clusters.update(
        cluster_id
        for cluster_id in records
        if any(cluster_id.startswith(prefix) for prefix in decisions["delete_prefixes"])
    )
    explicit = decisions["set"]
    automatic = decisions["auto"]
    member_set = decisions["member_set"]
    subcluster_set = decisions["subcluster_set"]
    handled = delete_clusters | set(explicit) | set(automatic)
    excluded = set(decisions["exclude_from_references"])

    items = []
    by_cluster = defaultdict(list)
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
            cluster_id = member_clusters[(image_path.name, line_index)]
            item = {
                "image": image_path.name,
                "line": line_index,
                "line_text": line,
                "class_id": class_id,
                "cluster": cluster_id,
                "feature": crop_feature(image, values),
            }
            items.append(item)
            by_cluster[cluster_id].append(item)

    features = np.asarray([item["feature"] for item in items], np.float32)
    feature_mean = features.mean(axis=0)
    feature_std = features.std(axis=0) + 1e-6
    for item in items:
        item["feature"] = (item["feature"] - feature_mean) / feature_std

    centroids = []
    radii = defaultdict(list)
    for cluster_id, cluster_items in by_cluster.items():
        if cluster_id in handled or cluster_id in excluded:
            continue
        target = classes[cluster_items[0]["class_id"]]
        center = np.mean([item["feature"] for item in cluster_items], axis=0)
        distances = [float(np.linalg.norm(item["feature"] - center)) for item in cluster_items]
        centroids.append((target, cluster_id, center))
        radii[target].extend(distances)
    for cluster_id in delete_clusters:
        cluster_items = by_cluster[cluster_id]
        center = np.mean([item["feature"] for item in cluster_items], axis=0)
        distances = [float(np.linalg.norm(item["feature"] - center)) for item in cluster_items]
        centroids.append((UNKNOWN, cluster_id, center))
        radii[UNKNOWN].extend(distances)
    thresholds = {
        target: float(np.percentile(distances, 95) * 1.05 + 1e-6)
        for target, distances in radii.items()
    }

    changes = []
    output_lines = defaultdict(list)
    counts_before = Counter()
    counts_after = Counter()
    for item in items:
        old_name = classes[item["class_id"]]
        counts_before[old_name] += 1
        cluster_id = item["cluster"]
        target = old_name
        reason = "keep"
        if cluster_id in delete_clusters:
            target = None
            reason = "visual-delete"
        elif cluster_id in explicit:
            target = explicit[cluster_id]
            reason = "visual-cluster"
        elif cluster_id in automatic:
            default = automatic[cluster_id]
            distances = sorted(
                (float(np.linalg.norm(item["feature"] - center)), name, reference)
                for name, reference, center in centroids
            )
            nearest_distance, nearest_name, reference = distances[0]
            second_distance = distances[1][0]
            if (
                nearest_distance <= thresholds[nearest_name]
                and nearest_distance <= second_distance * 0.8
            ):
                target = None if nearest_name == UNKNOWN else nearest_name
                reason = f"visual-nearest:{reference}"
            else:
                target = default
                reason = "visual-default"

        subcluster_id = member_subclusters.get((item["image"], item["line"]))
        if subcluster_id in subcluster_set:
            target = subcluster_set[subcluster_id]
            reason = f"visual-subcluster:{subcluster_id}"

        member_key = f"{item['image']}#{item['line']}"
        if member_key in member_set:
            target = member_set[member_key]
            reason = "visual-member"

        if target is not None:
            parts = item["line_text"].split()
            parts[0] = str(class_ids[target])
            output_lines[Path(item["image"]).stem].append(" ".join(parts))
            counts_after[target] += 1
        if target != old_name:
            changes.append(
                {
                    "image": item["image"],
                    "line": item["line"],
                    "cluster": cluster_id,
                    "from": old_name,
                    "to": target,
                    "reason": reason,
                }
            )

    report = {
        "applied": args.apply,
        "detections_before": sum(counts_before.values()),
        "detections_after": sum(counts_after.values()),
        "counts_before": counts_before,
        "counts_after": counts_after,
        "changes": changes,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.apply:
        for label_path in args.labels.glob("*.txt"):
            if label_path.name == "classes.txt":
                continue
            lines = output_lines.get(label_path.stem, [])
            label_path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "changes"}, ensure_ascii=False, indent=2))
    print(f"修改条目: {len(changes)}")


if __name__ == "__main__":
    main()
