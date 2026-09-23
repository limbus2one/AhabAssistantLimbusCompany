"""构建 E.G.O 饰品 YOLO 检测所需的模型资源。

产出两个文件，都随仓库分发，运行时只靠 ``onnxruntime`` 读取：

* ``assets/models/ego_gift_classes.yaml`` —— 类别索引 → 饰品元数据（id / 中英文名 / 体系）
* ``assets/models/ego_gift_yolo26n.onnx`` —— 导出的 ONNX 模型（``--export`` 时生成）

类别元数据来自 ``yoloego`` 工程的 ``assets/classes.txt`` + ``assets/manifest.json``，
两处按 ``class_index`` 对齐后再合并，保证模型输出的类别下标能直接查到饰品信息。

用法::

    # 只生成类别元数据（不需要 torch / ultralytics）
    python scripts/build_ego_gift_yolo.py --yoloego-root E:/Code/yoloego

    # 顺便从 .pt 导出 ONNX（需要在已装 ultralytics + torch 的环境里跑）
    python scripts/build_ego_gift_yolo.py --yoloego-root E:/Code/yoloego \
        --weights E:/Code/yoloego/models/kaggle_epoch160/runs/egogift_yolo26n_640/weights/last.pt \
        --export
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "assets" / "models"
DEFAULT_MODEL_NAME = "ego_gift_yolo26n.onnx"
DEFAULT_CLASSES_NAME = "ego_gift_classes.yaml"

# 强化标记类别没有对应的饰品，元数据留空即可
ENHANCE_CLASS_PREFIX = "enhance_"


def build_classes(yoloego_root: Path) -> list[dict]:
    """合并 classes.txt 与 manifest.json，按 class_index 生成有序类别表。"""
    classes_file = yoloego_root / "assets" / "classes.txt"
    manifest_file = yoloego_root / "assets" / "manifest.json"
    if not classes_file.is_file():
        raise SystemExit(f"未找到类别清单：{classes_file}")
    if not manifest_file.is_file():
        raise SystemExit(f"未找到类别元数据：{manifest_file}")

    names = [line.strip() for line in classes_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))

    by_index: dict[int, dict] = {}
    for gift in manifest:
        by_index[int(gift["class_index"])] = gift

    entries: list[dict] = []
    for index, name in enumerate(names):
        entry: dict = {"index": index, "name": name}
        gift = by_index.get(index)
        if gift is not None and not name.startswith(ENHANCE_CLASS_PREFIX):
            entry.update(
                {
                    "gift_id": str(gift["gift_id"]),
                    "name_zh": gift.get("name_zh"),
                    "name_en": gift.get("name_en"),
                    "keyword": gift.get("keyword"),
                    "tier": str(gift["tier"]) if gift.get("tier") is not None else None,
                }
            )
        entries.append(entry)

    return entries


def export_onnx(weights: Path, output: Path, imgsz: int, one_to_many: bool) -> None:
    """用 ultralytics 把 .pt 导出为 ONNX。"""
    try:
        from ultralytics import YOLO
    except ImportError as exc:  # pragma: no cover - 只在缺依赖时触发
        raise SystemExit(
            "导出 ONNX 需要 ultralytics 与 torch。请先安装，例如：\n"
            "  python -m pip install torch --index-url https://download.pytorch.org/whl/cpu\n"
            "  python -m pip install ultralytics onnx onnxslim\n"
            f"（原始错误：{exc}）"
        ) from exc

    if not weights.is_file():
        raise SystemExit(f"未找到权重文件：{weights}")

    model = YOLO(str(weights))
    exported = Path(
        model.export(
            format="onnx",
            imgsz=imgsz,
            opset=19,
            simplify=True,
            dynamic=False,
            end2end=not one_to_many,
        )
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(exported, output)
    print(f"ONNX 模型已生成：{output}")


def main() -> None:
    parser = argparse.ArgumentParser(description="构建 E.G.O 饰品 YOLO 模型资源")
    parser.add_argument("--yoloego-root", type=Path, required=True, help="yoloego 工程根目录")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--weights", type=Path, help="待导出的 .pt 权重，配合 --export 使用")
    parser.add_argument("--export", action="store_true", help="同时导出 ONNX 模型")
    parser.add_argument(
        "--one-to-many",
        action="store_true",
        help="导出传统检测头（需要自行 NMS），默认导出 YOLO26 的 end2end 头",
    )
    args = parser.parse_args()

    yoloego_root = args.yoloego_root.resolve()
    if not yoloego_root.is_dir():
        raise SystemExit(f"yoloego 根目录不存在：{yoloego_root}")

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    entries = build_classes(yoloego_root)
    gift_count = sum(1 for entry in entries if entry.get("gift_id"))
    payload = {
        "imgsz": args.imgsz,
        "model": DEFAULT_MODEL_NAME,
        "source": "yoloego",
        "classes": entries,
    }
    classes_path = output_dir / DEFAULT_CLASSES_NAME
    with open(classes_path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)
    print(f"类别元数据已生成：{classes_path}（{len(entries)} 类，其中 {gift_count} 个饰品）")

    if args.export:
        if not args.weights:
            raise SystemExit("--export 需要同时指定 --weights")
        export_onnx(args.weights, output_dir / DEFAULT_MODEL_NAME, args.imgsz, args.one_to_many)
    else:
        print(f"未指定 --export，跳过 ONNX 导出；请自行放置 {output_dir / DEFAULT_MODEL_NAME}")
        print(f"（当前解释器：{sys.executable}）")


if __name__ == "__main__":
    main()
