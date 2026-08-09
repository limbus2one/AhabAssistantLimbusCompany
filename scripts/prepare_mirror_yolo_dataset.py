import argparse
import json
import os
import random
import shutil
from collections import Counter
from pathlib import Path


CLASS_NAMES = ["battle", "boss", "event", "focused", "risky", "shop", "abnormality"]


def label_counts(path):
    counts = Counter()
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 5:
            counts[int(parts[0])] += 1
    return counts


def choose_validation(groups, fraction, trials, seed):
    total_images = sum(group["images"] for group in groups)
    total_classes = Counter()
    for group in groups:
        total_classes.update(group["classes"])

    best = None
    rng = random.Random(seed)
    for _ in range(trials):
        selected = [group for group in groups if rng.random() < fraction]
        if not selected:
            continue
        images = sum(group["images"] for group in selected)
        classes = Counter()
        for group in selected:
            classes.update(group["classes"])
        score = abs(images / total_images - fraction) * 4
        for class_id, total in total_classes.items():
            if total:
                score += abs(classes[class_id] / total - fraction)
                if classes[class_id] == 0:
                    score += 10
        if best is None or score < best[0]:
            best = score, {group["id"] for group in selected}
    if best is None:
        raise RuntimeError("无法生成验证集")
    return best[1]


def main():
    parser = argparse.ArgumentParser(description="按 capture_id 分组准备镜牢 YOLO 数据集")
    parser.add_argument("--images", type=Path, default=Path(r"E:\image"))
    parser.add_argument("--labels", type=Path, default=Path(r"E:\mirror_yolo\curated\labels"))
    parser.add_argument("--groups", type=Path, default=Path(r"E:\mirror_yolo\curated\groups.json"))
    parser.add_argument("--output", type=Path, default=Path(r"E:\mirror_yolo\dataset_v1"))
    parser.add_argument("--validation-fraction", type=float, default=0.2)
    parser.add_argument("--trials", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=20260809)
    args = parser.parse_args()

    if args.output.exists():
        raise FileExistsError(f"输出目录已存在，为避免覆盖已停止: {args.output}")

    source_groups = json.loads(args.groups.read_text(encoding="utf-8"))
    groups = []
    excluded_empty = []
    for group_id, filenames in source_groups.items():
        kept = []
        counts = Counter()
        for filename in filenames:
            label_path = args.labels / f"{Path(filename).stem}.txt"
            current = label_counts(label_path)
            if not current:
                excluded_empty.append(filename)
                continue
            kept.append(filename)
            counts.update(current)
        if kept:
            groups.append({"id": group_id, "filenames": kept, "images": len(kept), "classes": counts})

    validation_groups = choose_validation(
        groups, args.validation_fraction, args.trials, args.seed
    )
    split_records = {"train": [], "val": []}
    split_counts = {"train": Counter(), "val": Counter()}

    for group in groups:
        split = "val" if group["id"] in validation_groups else "train"
        for filename in group["filenames"]:
            split_records[split].append(filename)
            split_counts[split].update(label_counts(args.labels / f"{Path(filename).stem}.txt"))

    for split, filenames in split_records.items():
        image_dir = args.output / "images" / split
        label_dir = args.output / "labels" / split
        image_dir.mkdir(parents=True, exist_ok=False)
        label_dir.mkdir(parents=True, exist_ok=False)
        for filename in filenames:
            source_image = args.images / filename
            target_image = image_dir / filename
            os.link(source_image, target_image)
            shutil.copy2(args.labels / f"{Path(filename).stem}.txt", label_dir / f"{Path(filename).stem}.txt")

    yaml = "\n".join(
        [
            f"path: {args.output.as_posix()}",
            "train: images/train",
            "val: images/val",
            "names:",
            *[f"  {index}: {name}" for index, name in enumerate(CLASS_NAMES)],
            "",
        ]
    )
    (args.output / "data.yaml").write_text(yaml, encoding="utf-8")
    report = {
        "seed": args.seed,
        "validation_fraction": args.validation_fraction,
        "excluded_empty_images": len(excluded_empty),
        "excluded_empty_filenames": excluded_empty,
        "splits": {
            split: {
                "images": len(filenames),
                "groups": len(
                    {
                        group["id"]
                        for group in groups
                        if (group["id"] in validation_groups) == (split == "val")
                    }
                ),
                "boxes": sum(split_counts[split].values()),
                "classes": {
                    CLASS_NAMES[class_id]: split_counts[split][class_id]
                    for class_id in range(len(CLASS_NAMES))
                },
            }
            for split, filenames in split_records.items()
        },
    }
    (args.output / "split_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
