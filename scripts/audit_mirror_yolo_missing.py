import argparse
import json
from collections import Counter
from pathlib import Path

import cv2
import numpy as np


def read_labels(path):
    boxes = []
    if not path.exists():
        return boxes
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) != 5:
            continue
        boxes.append(
            {
                "class_id": int(parts[0]),
                "x": float(parts[1]),
                "y": float(parts[2]),
                "w": float(parts[3]),
                "h": float(parts[4]),
            }
        )
    return boxes


def write_labels(path, boxes):
    lines = [
        f"{box['class_id']} {box['x']:.6f} {box['y']:.6f} "
        f"{box['w']:.6f} {box['h']:.6f}"
        for box in boxes
    ]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def estimate_offsets(images):
    offsets = [0.0]
    steps = []
    previous = images[0]
    height, width = previous.shape[:2]
    left, right = int(width * 0.10), int(width * 0.90)
    top, bottom = int(height * 0.16), int(height * 0.83)
    window = cv2.createHanningWindow((right - left, bottom - top), cv2.CV_32F)

    for current in images[1:]:
        a = cv2.cvtColor(previous[top:bottom, left:right], cv2.COLOR_BGR2GRAY).astype(np.float32)
        b = cv2.cvtColor(current[top:bottom, left:right], cv2.COLOR_BGR2GRAY).astype(np.float32)
        (dx, dy), response = cv2.phaseCorrelate(a, b, window)
        step = -dx / width
        valid = response >= 0.08 and abs(dy) <= 15 and -0.015 <= step <= 0.24
        if not valid:
            step = 0.0
        offsets.append(offsets[-1] + step)
        steps.append(
            {
                "pixels": round(step * width, 2),
                "response": round(float(response), 4),
                "valid": bool(valid),
            }
        )
        previous = current
    return offsets, steps


def crop_feature(image, box):
    height, width = image.shape[:2]
    half_w = box["w"] * width * 0.48
    half_h = box["h"] * height * 0.48
    center_x, center_y = box["x"] * width, box["y"] * height
    left = max(0, int(center_x - half_w))
    right = min(width, int(center_x + half_w))
    top = max(0, int(center_y - half_h))
    bottom = min(height, int(center_y + half_h))
    crop = image[top:bottom, left:right]
    if crop.size == 0:
        return None
    gray = cv2.cvtColor(cv2.resize(crop, (48, 48), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    gray = gray.astype(np.float32)
    gray = (gray - gray.mean()) / (gray.std() + 1e-6)
    return gray.reshape(-1)


def similarity(feature, references):
    if feature is None or not references:
        return -1.0
    scores = [float(np.mean(feature * reference)) for reference in references]
    return max(scores)


def cluster_observations(frame_boxes, offsets):
    clusters = []
    for frame_index, boxes in enumerate(frame_boxes):
        for box in boxes:
            world_x = box["x"] + offsets[frame_index]
            candidates = [
                cluster
                for cluster in clusters
                if cluster["class_id"] == box["class_id"]
                and abs(cluster["world_x"] - world_x) <= 0.022
                and abs(cluster["y"] - box["y"]) <= 0.030
                and frame_index not in cluster["frames"]
            ]
            if candidates:
                cluster = min(
                    candidates,
                    key=lambda item: abs(item["world_x"] - world_x) + abs(item["y"] - box["y"]),
                )
                cluster["items"].append((frame_index, box))
                cluster["frames"].add(frame_index)
                cluster["world_x"] = float(np.median([item[1]["x"] + offsets[item[0]] for item in cluster["items"]]))
                cluster["y"] = float(np.median([item[1]["y"] for item in cluster["items"]]))
            else:
                clusters.append(
                    {
                        "class_id": box["class_id"],
                        "world_x": world_x,
                        "y": box["y"],
                        "frames": {frame_index},
                        "items": [(frame_index, box)],
                    }
                )
    return clusters


def has_nearby_box(boxes, x, y):
    return any(abs(box["x"] - x) <= 0.035 and abs(box["y"] - y) <= 0.040 for box in boxes)


def make_review_sheet(candidates, images_dir, output):
    if not candidates:
        return
    tiles = []
    for candidate in candidates:
        image = cv2.imread(str(images_dir / candidate["image"]))
        height, width = image.shape[:2]
        x, y = candidate["x"] * width, candidate["y"] * height
        crop_w, crop_h = 260, 190
        left = max(0, min(width - crop_w, int(x - crop_w / 2)))
        top = max(0, min(height - crop_h, int(y - crop_h / 2)))
        tile = image[top : top + crop_h, left : left + crop_w].copy()
        box_left = int(x - candidate["w"] * width / 2) - left
        box_right = int(x + candidate["w"] * width / 2) - left
        box_top = int(y - candidate["h"] * height / 2) - top
        box_bottom = int(y + candidate["h"] * height / 2) - top
        color = (50, 220, 50) if candidate["accepted"] else (40, 80, 230)
        cv2.rectangle(tile, (box_left, box_top), (box_right, box_bottom), color, 2)
        text = f"{candidate['class_name']} sim={candidate['similarity']:.2f}"
        cv2.rectangle(tile, (0, 0), (260, 28), (0, 0, 0), -1)
        cv2.putText(tile, text, (5, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA)
        tiles.append(tile)

    columns = 5
    rows = (len(tiles) + columns - 1) // columns
    sheet = np.full((rows * 190, columns * 260, 3), 24, dtype=np.uint8)
    for index, tile in enumerate(tiles):
        row, column = divmod(index, columns)
        sheet[row * 190 : (row + 1) * 190, column * 260 : (column + 1) * 260] = tile
    cv2.imwrite(str(output), sheet, [cv2.IMWRITE_JPEG_QUALITY, 94])


def main():
    parser = argparse.ArgumentParser(description="利用同场景横移截图复核 YOLO 漏标")
    parser.add_argument("--images", type=Path, default=Path(r"E:\image"))
    parser.add_argument("--labels", type=Path, default=Path(r"E:\mirror_yolo\curated\labels"))
    parser.add_argument("--groups", type=Path, default=Path(r"E:\mirror_yolo\curated\groups.json"))
    parser.add_argument("--report", type=Path, default=Path(r"E:\mirror_yolo\curated\missing_review.json"))
    parser.add_argument("--sheet", type=Path, default=Path(r"E:\mirror_yolo\curated\missing_review.jpg"))
    parser.add_argument("--min-similarity", type=float, default=0.68)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    classes = (args.labels / "classes.txt").read_text(encoding="utf-8").splitlines()
    groups = json.loads(args.groups.read_text(encoding="utf-8"))
    candidates = []
    group_reports = []

    for group_id, filenames in groups.items():
        if len(filenames) < 5:
            continue
        images = [cv2.imread(str(args.images / filename)) for filename in filenames]
        if any(image is None for image in images):
            raise RuntimeError(f"无法读取场景组图片: {group_id}")
        frame_boxes = [read_labels(args.labels / f"{Path(filename).stem}.txt") for filename in filenames]
        offsets, steps = estimate_offsets(images)
        clusters = cluster_observations(frame_boxes, offsets)
        group_candidates = []

        for cluster in clusters:
            if len(cluster["frames"]) < 2:
                continue
            widths = [item[1]["w"] for item in cluster["items"]]
            heights = [item[1]["h"] for item in cluster["items"]]
            ys = [item[1]["y"] for item in cluster["items"]]
            references = [crop_feature(images[index], box) for index, box in cluster["items"]]
            references = [feature for feature in references if feature is not None]
            for frame_index, filename in enumerate(filenames):
                if frame_index in cluster["frames"]:
                    continue
                x = cluster["world_x"] - offsets[frame_index]
                y = float(np.median(ys))
                if not (0.065 <= x <= 0.92 and 0.17 <= y <= 0.87):
                    continue
                if has_nearby_box(frame_boxes[frame_index], x, y):
                    continue
                candidate_box = {
                    "class_id": cluster["class_id"],
                    "x": x,
                    "y": y,
                    "w": float(np.median(widths)),
                    "h": float(np.median(heights)),
                }
                score = similarity(crop_feature(images[frame_index], candidate_box), references)
                candidate = {
                    "group": group_id,
                    "image": filename,
                    "class_id": cluster["class_id"],
                    "class_name": classes[cluster["class_id"]],
                    "x": round(x, 6),
                    "y": round(y, 6),
                    "w": round(candidate_box["w"], 6),
                    "h": round(candidate_box["h"], 6),
                    "observed_frames": len(cluster["frames"]),
                    "similarity": round(score, 4),
                    "accepted": score >= args.min_similarity,
                }
                candidates.append(candidate)
                group_candidates.append(candidate)

        group_reports.append(
            {
                "group": group_id,
                "steps": steps,
                "candidates": len(group_candidates),
                "accepted": sum(item["accepted"] for item in group_candidates),
            }
        )

    accepted = [candidate for candidate in candidates if candidate["accepted"]]
    if args.apply:
        additions = {}
        for candidate in accepted:
            additions.setdefault(Path(candidate["image"]).stem, []).append(candidate)
        for stem, new_boxes in additions.items():
            path = args.labels / f"{stem}.txt"
            boxes = read_labels(path)
            boxes.extend(
                {
                    "class_id": item["class_id"],
                    "x": item["x"],
                    "y": item["y"],
                    "w": item["w"],
                    "h": item["h"],
                }
                for item in new_boxes
            )
            write_labels(path, boxes)

    report = {
        "applied": args.apply,
        "minimum_similarity": args.min_similarity,
        "groups_checked": len(group_reports),
        "candidates": len(candidates),
        "accepted": len(accepted),
        "accepted_by_class": Counter(candidate["class_name"] for candidate in accepted),
        "items": candidates,
        "groups": group_reports,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    make_review_sheet(candidates, args.images, args.sheet)
    print(json.dumps({key: value for key, value in report.items() if key not in {"items", "groups"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
