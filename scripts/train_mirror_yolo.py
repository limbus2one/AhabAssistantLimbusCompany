import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="训练镜牢节点 YOLO11s")
    parser.add_argument("--model", type=Path, default=Path(r"E:\mirror_yolo\base_models\yolo11s.pt"))
    parser.add_argument("--data", type=Path, default=Path(r"E:\mirror_yolo\dataset_v2\data.yaml"))
    parser.add_argument("--project", type=Path, default=Path(r"E:\mirror_yolo\runs"))
    parser.add_argument("--name", default="mirror_yolo11s_v2")
    parser.add_argument("--epochs", type=int, default=120)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--device", default="0")
    args = parser.parse_args()

    os.environ.setdefault(
        "YOLO_CONFIG_DIR",
        str(Path(__file__).resolve().parents[1] / ".ultralytics"),
    )
    from ultralytics import YOLO

    model = YOLO(str(args.model))
    model.train(
        data=str(args.data),
        project=str(args.project),
        name=args.name,
        exist_ok=False,
        epochs=args.epochs,
        patience=25,
        imgsz=640,
        batch=args.batch,
        workers=args.workers,
        device=args.device,
        optimizer="AdamW",
        cos_lr=True,
        close_mosaic=10,
        amp=True,
        seed=20260809,
        deterministic=True,
        plots=True,
        fliplr=0.0,
        translate=0.05,
        scale=0.25,
        mosaic=0.5,
    )


if __name__ == "__main__":
    main()
