"""YOLO 目标检测接口封装。

直接使用 ``onnxruntime`` 推理导出的 YOLO ONNX 模型，**不依赖 torch / ultralytics**，
因此不会给主项目引入额外运行依赖（``onnxruntime`` 本来就是既有依赖）。

模型与类别元数据都放在 ``assets/models/`` 下随仓库一起分发，运行时不依赖训练工程。

用法::

    from module.yolo import yolo

    for det in yolo.predict(screenshot):          # screenshot: PIL.Image / np.ndarray
        print(det.class_name, det.gift_id, det.system, det.confidence, det.center)

同时兼容 YOLO26 的两种导出头：

* **end2end**（默认，NMS-free）：输出 ``(1, 300, 6)``，最后一维是
  ``[x1, y1, x2, y2, conf, cls]``，置信度与类别已由模型内部解好，无需 NMS；
* **传统头**（``--one-to-many`` 导出）：输出 ``(1, 4 + nc, N)``，需要自行做 NMS。

具体走哪条分支由 ONNX 输出张量的形状 + 类别数在加载时判定，不靠猜。
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any, Sequence

import cv2
import numpy as np
import yaml
from PIL import Image

from utils.singletonmeta import SingletonMeta

# 游戏内 Keyword 归一化后的体系名，与 tasks.all_systems 的取值保持一致。
# manifest 里的 keyword 是游戏原文（如 Burn / Keywordless），这里只保留能落到
# 十种体系上的取值；Keywordless 之类的通用饰品不返回体系。
GIFT_SYSTEMS = frozenset(
    {
        "burn",
        "bleed",
        "tremor",
        "rupture",
        "poise",
        "sinking",
        "charge",
        "slash",
        "pierce",
        "blunt",
    }
)

# letterbox 的填充色，与 Ultralytics 训练/导出时的默认值一致
_PAD_VALUE = 114


@dataclass(frozen=True)
class Detection:
    """单个检测结果。坐标为**原图像素坐标**，与截图坐标系一致。"""

    class_id: int
    class_name: str
    confidence: float
    xyxy: tuple[float, float, float, float]
    gift_id: str | None = None
    name_zh: str | None = None
    name_en: str | None = None
    keyword: str | None = None
    tier: str | None = None

    @property
    def center(self) -> tuple[int, int]:
        """检测框中心点，用于直接喂给 ``auto.mouse_click``。"""
        x1, y1, x2, y2 = self.xyxy
        return int(round((x1 + x2) / 2)), int(round((y1 + y2) / 2))

    @property
    def width(self) -> float:
        return self.xyxy[2] - self.xyxy[0]

    @property
    def height(self) -> float:
        return self.xyxy[3] - self.xyxy[1]

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def system(self) -> str | None:
        """饰品体系（burn / bleed / ...）；通用饰品返回 ``None``。"""
        if not self.keyword:
            return None
        normalized = self.keyword.strip().lower()
        return normalized if normalized in GIFT_SYSTEMS else None

    def is_inside(self, crop: Sequence[float], ratio: float = 0.0) -> bool:
        """判断检测框中心是否落在 ``(x1, y1, x2, y2)`` 区域内。

        ``ratio`` 为容差比例：0.1 表示边界向外放宽 10% 宽高；默认 0，即严格判定。
        """
        cx, cy = self.center
        x1, y1, x2, y2 = crop
        margin_x = (x2 - x1) * ratio
        margin_y = (y2 - y1) * ratio
        return (x1 - margin_x) <= cx <= (x2 + margin_x) and (y1 - margin_y) <= cy <= (y2 + margin_y)


# ---------------------------------------------------------------------------
# 纯函数：letterbox / 解码 / NMS / 坐标还原
# 拆出来是为了能脱离 onnxruntime 单独做单元测试。
# ---------------------------------------------------------------------------


def letterbox(
    image: np.ndarray,
    new_shape: int | tuple[int, int] = 640,
    color: int = _PAD_VALUE,
) -> tuple[np.ndarray, float, tuple[float, float]]:
    """等比缩放并居中填充到 ``new_shape``。

    Returns:
        ``(padded, ratio, (pad_x, pad_y))``。``pad_x/pad_y`` 是左侧/顶部填充的像素数。
    """
    if isinstance(new_shape, int):
        new_w = new_h = new_shape
    else:
        new_h, new_w = new_shape

    src_h, src_w = image.shape[:2]
    if src_h <= 0 or src_w <= 0:
        raise ValueError(f"无效的图像尺寸: {image.shape}")

    ratio = min(new_h / src_h, new_w / src_w)
    unpad_w, unpad_h = round(src_w * ratio), round(src_h * ratio)

    resized = image
    if (src_w, src_h) != (unpad_w, unpad_h):
        resized = cv2.resize(image, (unpad_w, unpad_h), interpolation=cv2.INTER_LINEAR)

    dw = (new_w - unpad_w) / 2
    dh = (new_h - unpad_h) / 2
    top, bottom = round(dh - 0.1), round(dh + 0.1)
    left, right = round(dw - 0.1), round(dw + 0.1)

    padded = cv2.copyMakeBorder(
        resized,
        top,
        bottom,
        left,
        right,
        cv2.BORDER_CONSTANT,
        value=(color, color, color) if resized.ndim == 3 else color,
    )
    return padded, ratio, (float(left), float(top))


def scale_boxes(
    boxes: np.ndarray,
    ratio: float,
    pad: tuple[float, float],
    original_shape: tuple[int, int],
) -> np.ndarray:
    """把 letterbox 坐标系下的框还原回原图坐标，并裁剪到图像范围内。"""
    if boxes.size == 0:
        return boxes.reshape(0, 4)

    pad_x, pad_y = pad
    boxes = boxes.astype(np.float32, copy=True)
    boxes[:, [0, 2]] = (boxes[:, [0, 2]] - pad_x) / ratio
    boxes[:, [1, 3]] = (boxes[:, [1, 3]] - pad_y) / ratio

    height, width = original_shape
    boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, width)
    boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, height)
    return boxes


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float = 0.45) -> list[int]:
    """标准 IoU NMS，返回保留框的下标（按置信度降序）。"""
    if boxes.size == 0:
        return []

    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(x2 - x1, 0) * np.maximum(y2 - y1, 0)
    order = scores.argsort()[::-1]

    keep: list[int] = []
    while order.size > 0:
        best = order[0]
        keep.append(int(best))
        if order.size == 1:
            break

        rest = order[1:]
        xx1 = np.maximum(x1[best], x1[rest])
        yy1 = np.maximum(y1[best], y1[rest])
        xx2 = np.minimum(x2[best], x2[rest])
        yy2 = np.minimum(y2[best], y2[rest])

        inter = np.maximum(xx2 - xx1, 0) * np.maximum(yy2 - yy1, 0)
        union = areas[best] + areas[rest] - inter
        iou = np.where(union > 0, inter / np.maximum(union, 1e-9), 0.0)

        order = rest[iou <= iou_threshold]

    return keep


def decode_predictions(
    output: np.ndarray,
    num_classes: int,
    conf_threshold: float = 0.35,
    iou_threshold: float = 0.45,
    end2end: bool | None = None,
) -> np.ndarray:
    """把 ONNX 原始输出解成 ``(N, 6)`` 的 ``[x1, y1, x2, y2, conf, cls]``。

    兼容两种导出头：

    * ``(1, N, 6)``：end2end / NMS-free，模型已给出最终框、置信度与类别；
    * ``(1, 4 + nc, M)``：传统头，需要按类取最大值再 NMS。

    Args:
        end2end: 显式指定导出头类型。``None`` 时按张量形状推断 —— 只有
            ``num_classes == 2`` 时 ``4 + nc`` 恰好等于 ``6``、形状上无法区分，
            所以真实调用一律由 :meth:`YoloDetector._detect_end2end` 显式传入。

    Returns:
        浮点数组，形状 ``(N, 6)``，坐标为 letterbox 坐标系；无目标时返回 ``(0, 6)``。
    """
    pred = np.asarray(output, dtype=np.float32)
    while pred.ndim > 2 and pred.shape[0] == 1:
        pred = pred[0]
    if pred.ndim == 1:
        pred = pred.reshape(1, -1)
    if pred.ndim != 2:
        raise ValueError(f"无法识别的模型输出维度 {np.asarray(output).shape}")

    expected_head_width = 4 + num_classes

    if end2end is None:
        # 形状推断：哪个维度等于 4+nc，那个维度就是通道维
        if pred.shape[1] == expected_head_width and pred.shape[0] != 6:
            end2end = False
        elif pred.shape[0] == expected_head_width and pred.shape[1] != 6:
            end2end = False
        else:
            end2end = pred.shape[-1] == 6

    if end2end:
        detections = pred
        if detections.shape[1] != 6:
            raise ValueError(
                f"end2end 头期望末维为 6，实际为 {detections.shape[1]}（输出形状 {pred.shape}）"
            )
    else:
        if pred.shape[1] == expected_head_width:
            detections = _decode_raw_head(pred, num_classes, conf_threshold, iou_threshold)
        elif pred.shape[0] == expected_head_width:
            detections = _decode_raw_head(pred.T, num_classes, conf_threshold, iou_threshold)
        else:
            raise ValueError(
                f"无法识别的模型输出形状 {pred.shape}（类别数 {num_classes}）；"
                f"期望 (1, N, 6) 或 (1, {expected_head_width}, M)"
            )

    if detections.size == 0:
        return np.zeros((0, 6), dtype=np.float32)

    detections = detections[detections[:, 4] >= conf_threshold]
    if detections.size == 0:
        return np.zeros((0, 6), dtype=np.float32)

    # end2end 头已在模型内部做过 NMS，这里再跑一次只是兜底去重，成本可忽略
    keep = nms(detections[:, :4], detections[:, 4], iou_threshold)
    return detections[keep]


def _decode_raw_head(
    pred: np.ndarray,
    num_classes: int,
    conf_threshold: float,
    iou_threshold: float,
) -> np.ndarray:
    """解传统头 ``(M, 4 + nc)``：cx, cy, w, h + 每类分数。"""
    if pred.size == 0:
        return np.zeros((0, 6), dtype=np.float32)

    boxes_cxcywh = pred[:, :4]
    class_scores = pred[:, 4 : 4 + num_classes]
    if class_scores.size == 0:
        return np.zeros((0, 6), dtype=np.float32)

    class_ids = class_scores.argmax(axis=1)
    confidences = class_scores[np.arange(class_scores.shape[0]), class_ids]

    mask = confidences >= conf_threshold
    if not mask.any():
        return np.zeros((0, 6), dtype=np.float32)

    boxes_cxcywh = boxes_cxcywh[mask]
    confidences = confidences[mask]
    class_ids = class_ids[mask]

    cx, cy, w, h = boxes_cxcywh.T
    boxes_xyxy = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1)

    keep = nms(boxes_xyxy, confidences, iou_threshold)
    return np.concatenate(
        [boxes_xyxy[keep], confidences[keep, None], class_ids[keep, None].astype(np.float32)],
        axis=1,
    ).astype(np.float32)


# ---------------------------------------------------------------------------
# 检测器
# ---------------------------------------------------------------------------


class YoloDetector(metaclass=SingletonMeta):
    """YOLO 检测器（单例）。

    模型在**首次推理时**才加载，避免未用到 YOLO 的流程为它付出启动开销。
    """

    DEFAULT_MODEL_PATH = "./assets/models/ego_gift_yolo26n.onnx"
    DEFAULT_CLASSES_PATH = "./assets/models/ego_gift_classes.yaml"
    DEFAULT_CONF_THRESHOLD = 0.35
    DEFAULT_IOU_THRESHOLD = 0.45

    def __init__(
        self,
        logger: Any,
        model_path: str | None = None,
        classes_path: str | None = None,
        conf_threshold: float | None = None,
        iou_threshold: float | None = None,
        providers: Sequence[str] | None = None,
        intra_op_num_threads: int | None = 2,
    ) -> None:
        self.logger = logger
        self.model_path = model_path or self.DEFAULT_MODEL_PATH
        self.classes_path = classes_path or self.DEFAULT_CLASSES_PATH
        self.conf_threshold = (
            conf_threshold if conf_threshold is not None else self.DEFAULT_CONF_THRESHOLD
        )
        self.iou_threshold = iou_threshold if iou_threshold is not None else self.DEFAULT_IOU_THRESHOLD
        self.providers = list(providers) if providers else ["CPUExecutionProvider"]
        self.intra_op_num_threads = intra_op_num_threads

        self._session = None
        self._input_name: str | None = None
        self._output_is_end2end = False
        self._load_lock = threading.RLock()
        self._load_failed = False

        self.imgsz = 640
        self.classes: list[dict] = []
        self._classes_loaded = False

    # -- 元数据 ---------------------------------------------------------

    @property
    def num_classes(self) -> int:
        self._ensure_classes()
        return len(self.classes)

    def _ensure_classes(self) -> None:
        if self._classes_loaded:
            return
        self._classes_loaded = True
        try:
            with open(self.classes_path, encoding="utf-8") as handle:
                payload = yaml.safe_load(handle) or {}
        except FileNotFoundError:
            self.logger.error(f"未找到 YOLO 类别元数据：{self.classes_path}")
            return
        except Exception as exc:  # pragma: no cover - 仅在文件损坏时触发
            self.logger.error(f"读取 YOLO 类别元数据失败：{exc}")
            return

        self.classes = list(payload.get("classes") or [])
        self.imgsz = int(payload.get("imgsz") or self.imgsz)
        self.logger.debug(f"YOLO 类别元数据加载完成，共 {len(self.classes)} 类")

    # -- 会话 -----------------------------------------------------------

    @property
    def available(self) -> bool:
        """模型文件与元数据是否齐备且可用。"""
        self._ensure_classes()
        if not self.classes:
            return False
        return os.path.exists(self.model_path) and not self._load_failed

    def _ensure_session(self):
        if self._session is not None:
            return self._session
        with self._load_lock:
            if self._session is not None:
                return self._session
            if self._load_failed:
                return None

            self._ensure_classes()
            if not self.classes:
                self._load_failed = True
                return None
            if not os.path.exists(self.model_path):
                self.logger.error(f"未找到 YOLO 模型文件：{self.model_path}")
                self._load_failed = True
                return None

            try:
                import onnxruntime as ort

                options = ort.SessionOptions()
                options.log_severity_level = 3
                if self.intra_op_num_threads:
                    options.intra_op_num_threads = self.intra_op_num_threads
                session = ort.InferenceSession(
                    self.model_path,
                    sess_options=options,
                    providers=self.providers,
                )
            except Exception as exc:
                self.logger.error(f"加载 YOLO 模型失败：{exc}")
                self._load_failed = True
                return None

            self._session = session
            self._input_name = session.get_inputs()[0].name
            self._output_is_end2end = self._detect_end2end(session)
            self.logger.debug(
                f"YOLO 模型加载完成：{self.model_path}"
                f"（{'end2end' if self._output_is_end2end else '传统头'}，{self.num_classes} 类）"
            )
            return self._session

    def _detect_end2end(self, session) -> bool:
        """按输出张量形状判定导出头类型。

        * 传统头是 ``(1, 4 + nc, M)``：通道维等于 ``4 + nc``，先判它；
        * end2end 是 ``(1, N, 6)``：末维固定为 6。

        先判 ``4 + nc`` 是为了兼容 ``num_classes == 2`` 时两个条件都为真的歧义。
        """
        try:
            shape = session.get_outputs()[0].shape
        except Exception:
            return True
        if len(shape) != 3:
            return False

        expected = 4 + self.num_classes
        middle, last = shape[1], shape[2]
        if middle == expected:
            return False
        if last == 6:
            return True
        return False

    # -- 推理 -----------------------------------------------------------

    @staticmethod
    def _to_rgb_array(image: Image.Image | np.ndarray) -> np.ndarray:
        """统一转成 HWC、3 通道、uint8 的 RGB 数组。"""
        if isinstance(image, Image.Image):
            array = np.array(image.convert("RGB"))
        elif isinstance(image, np.ndarray):
            array = image
        else:
            raise TypeError(f"不支持的图像类型：{type(image)!r}")

        if array.ndim == 2:
            array = np.stack([array] * 3, axis=-1)
        elif array.ndim == 3 and array.shape[2] == 1:
            array = np.repeat(array, 3, axis=2)
        elif array.ndim == 3 and array.shape[2] == 4:
            array = array[:, :, :3]

        if array.ndim != 3 or array.shape[2] != 3:
            raise ValueError(f"不支持的图像形状：{array.shape}")

        if array.dtype != np.uint8:
            array = np.clip(array, 0, 255).astype(np.uint8)
        return np.ascontiguousarray(array)

    def predict(
        self,
        image: Image.Image | np.ndarray,
        conf: float | None = None,
        iou: float | None = None,
        imgsz: int | None = None,
    ) -> list[Detection]:
        """对整张图做一次检测。

        Args:
            image: ``PIL.Image`` 或 ``np.ndarray``（RGB，HWC）。
            conf: 置信度阈值，默认取实例配置。
            iou: NMS 的 IoU 阈值，默认取实例配置。
            imgsz: 推理尺寸，默认取元数据里的 ``imgsz``。

        Returns:
            按置信度降序排列的 :class:`Detection` 列表；模型不可用时返回 ``[]``。
        """
        session = self._ensure_session()
        if session is None:
            return []

        conf_threshold = self.conf_threshold if conf is None else conf
        iou_threshold = self.iou_threshold if iou is None else iou
        input_size = int(imgsz or self.imgsz)

        array = self._to_rgb_array(image)
        original_shape = array.shape[:2]

        padded, ratio, pad = letterbox(array, input_size)
        tensor = padded.astype(np.float32) / 255.0
        tensor = np.ascontiguousarray(tensor.transpose(2, 0, 1)[None, ...])

        try:
            outputs = session.run(None, {self._input_name: tensor})
        except Exception as exc:
            self.logger.error(f"YOLO 推理失败：{exc}")
            return []

        try:
            detections = decode_predictions(
                outputs[0],
                num_classes=self.num_classes,
                conf_threshold=conf_threshold,
                iou_threshold=iou_threshold,
                end2end=self._output_is_end2end,
            )
        except ValueError as exc:
            self.logger.error(f"YOLO 输出解析失败：{exc}")
            return []

        if detections.size == 0:
            return []

        boxes = scale_boxes(detections[:, :4], ratio, pad, original_shape)

        results: list[Detection] = []
        for box, confidence, class_id in zip(boxes, detections[:, 4], detections[:, 5], strict=True):
            index = int(class_id)
            meta = self.classes[index] if 0 <= index < len(self.classes) else {}
            results.append(
                Detection(
                    class_id=index,
                    class_name=meta.get("name") or str(index),
                    confidence=float(confidence),
                    xyxy=(float(box[0]), float(box[1]), float(box[2]), float(box[3])),
                    gift_id=meta.get("gift_id"),
                    name_zh=meta.get("name_zh"),
                    name_en=meta.get("name_en"),
                    keyword=meta.get("keyword"),
                    tier=meta.get("tier"),
                )
            )

        results.sort(key=lambda item: item.confidence, reverse=True)
        return results

    def predict_best(
        self,
        image: Image.Image | np.ndarray,
        crop: Sequence[float] | None = None,
        conf: float | None = None,
        iou: float | None = None,
        imgsz: int | None = None,
    ) -> Detection | None:
        """只取一个最可信的检测结果，可选限定在 ``crop`` 区域内。

        饰品奖励界面一格里只会有一个饰品，用它比 ``predict`` 更省事。
        """
        detections = self.predict(image, conf=conf, iou=iou, imgsz=imgsz)
        if crop is not None:
            detections = [item for item in detections if item.is_inside(crop)]
        if not detections:
            return None
        return max(detections, key=lambda item: item.confidence)

    def find_gifts(
        self,
        image: Image.Image | np.ndarray,
        crop: Sequence[float] | None = None,
        conf: float | None = None,
        only_gifts: bool = True,
    ) -> list[Detection]:
        """只返回 E.G.O 饰品（排除 ``enhance_1`` / ``enhance_2`` 强化标记）。"""
        detections = self.predict(image, conf=conf)
        if crop is not None:
            detections = [item for item in detections if item.is_inside(crop)]
        if only_gifts:
            detections = [item for item in detections if item.gift_id]
        return detections
