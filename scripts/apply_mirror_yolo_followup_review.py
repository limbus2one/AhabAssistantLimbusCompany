import argparse
import json
from collections import Counter
from pathlib import Path


CLASS_NAMES = ["battle", "boss", "event", "focused", "risky", "shop", "abnormality"]
CLASS_IDS = {name: index for index, name in enumerate(CLASS_NAMES)}
RESTORE_RAW_CLASSES = {CLASS_IDS["event"]}
BOSS17_FIXES = {
    "mixed_01": "risky",
    "mixed_07": "abnormality",
    "mixed_08": "abnormality",
}
CANDIDATE_OVERRIDES = {
    ("onnx_nodes_1786131433033025200_shift_3.png", 0.289198, 0.514414): "risky",
    ("onnx_nodes_1786131433033025200_shift_4.png", 0.096998, 0.514414): "risky",
}


def read_labels(path):
    result = []
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 5:
            result.append([int(parts[0]), *map(float, parts[1:])])
    return result


def write_labels(path, boxes):
    lines = [f"{box[0]} {box[1]:.6f} {box[2]:.6f} {box[3]:.6f} {box[4]:.6f}" for box in boxes]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def find_box(boxes, values, expected_class=None):
    candidates = [
        (index, box)
        for index, box in enumerate(boxes)
        if (expected_class is None or box[0] == expected_class)
        and abs(box[1] - values[0]) <= 0.006
        and abs(box[2] - values[1]) <= 0.006
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: sum(abs(item[1][offset + 1] - values[offset]) for offset in range(4)),
    )[0]


def main():
    parser = argparse.ArgumentParser(description="应用镜牢标签的第二轮视觉复核")
    parser.add_argument("--labels", type=Path, default=Path(r"E:\mirror_yolo\curated\labels"))
    parser.add_argument("--raw-labels", type=Path, default=Path(r"E:\mirror_yolo\prelabels\labels"))
    parser.add_argument("--boss17", type=Path, default=Path(r"E:\mirror_yolo\boss17_subclusters\subclusters.json"))
    parser.add_argument("--missing", type=Path, default=Path(r"E:\mirror_yolo\curated\missing_review.json"))
    parser.add_argument("--report", type=Path, default=Path(r"E:\mirror_yolo\curated\followup_review.json"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    labels_by_stem = {}

    def current(stem):
        if stem not in labels_by_stem:
            labels_by_stem[stem] = read_labels(args.labels / f"{stem}.txt")
        return labels_by_stem[stem]

    changes = []
    for raw_path in sorted(args.raw_labels.glob("*.txt")):
        if raw_path.name == "classes.txt":
            continue
        boxes = current(raw_path.stem)
        for raw_box in read_labels(raw_path):
            if raw_box[0] not in RESTORE_RAW_CLASSES or find_box(boxes, raw_box[1:]) is not None:
                continue
            boxes.append(raw_box)
            changes.append(
                {
                    "kind": "restore",
                    "image": f"{raw_path.stem}.png",
                    "to": CLASS_NAMES[raw_box[0]],
                }
            )

    subclusters = json.loads(args.boss17.read_text(encoding="utf-8"))["clusters"]
    for cluster in subclusters:
        target_name = BOSS17_FIXES.get(cluster["id"])
        if target_name is None:
            continue
        target_id = CLASS_IDS[target_name]
        for member in cluster["members"]:
            raw = read_labels(args.raw_labels / f"{Path(member['image']).stem}.txt")
            if member["line"] >= len(raw):
                raise RuntimeError(f"原始标签行不存在: {member}")
            raw_box = raw[member["line"]]
            boxes = current(Path(member["image"]).stem)
            index = find_box(boxes, raw_box[1:], expected_class=CLASS_IDS["boss"])
            if index is None:
                existing = find_box(boxes, raw_box[1:])
                if existing is not None and boxes[existing][0] == target_id:
                    continue
                raise RuntimeError(f"无法在 curated 中定位 boss_17 成员: {member}")
            changes.append(
                {
                    "kind": "reclassify",
                    "image": member["image"],
                    "from": CLASS_NAMES[boxes[index][0]],
                    "to": target_name,
                    "subcluster": cluster["id"],
                }
            )
            boxes[index][0] = target_id

    missing = json.loads(args.missing.read_text(encoding="utf-8"))["items"]
    for item in missing:
        if not item["accepted"]:
            continue
        override_key = (item["image"], item["x"], item["y"])
        target_name = CANDIDATE_OVERRIDES.get(override_key, item["class_name"])
        new_box = [CLASS_IDS[target_name], item["x"], item["y"], item["w"], item["h"]]
        boxes = current(Path(item["image"]).stem)
        if find_box(boxes, new_box[1:]) is not None:
            continue
        boxes.append(new_box)
        changes.append(
            {
                "kind": "add",
                "image": item["image"],
                "to": target_name,
                "similarity": item["similarity"],
            }
        )

    report = {
        "applied": args.apply,
        "changes": len(changes),
        "summary": Counter(
            f"{item.get('from', 'missing')}->{item['to']}" for item in changes
        ),
        "items": changes,
    }
    if args.apply:
        for stem, boxes in labels_by_stem.items():
            write_labels(args.labels / f"{stem}.txt", boxes)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "items"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
