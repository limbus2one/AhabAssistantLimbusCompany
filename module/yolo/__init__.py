from module.logger import log
from module.yolo.yolo import Detection, YoloDetector

yolo = YoloDetector(log)

__all__ = ["Detection", "YoloDetector", "yolo"]
