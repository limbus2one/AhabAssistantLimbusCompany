"""YOLO 检测接口测试。

覆盖三部分：

1. **几何与解码纯函数** —— ``letterbox`` / ``scale_boxes`` / ``nms`` /
   ``decode_predictions``。这些不依赖 onnxruntime，用合成张量就能锁住
   「end2end 头 / 传统头」两条分支和坐标还原是否正确。
2. **类别元数据** —— ``assets/models/ego_gift_classes.yaml`` 的类别数、
   类别下标到饰品 id / 体系的映射。
3. **真实模型冒烟** —— 模型文件就位时，跑一遍完整 ``predict`` 管线，
   确保 ONNX 输出形状与解码逻辑一致（模型缺失时自动跳过）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

import cv2

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from module.yolo import yolo  # noqa: E402
from module.yolo.yolo import (  # noqa: E402
    Detection,
    decode_predictions,
    letterbox,
    nms,
    scale_boxes,
)

# ---------------------------------------------------------------------------
# letterbox / scale_boxes
# ---------------------------------------------------------------------------


def test_letterbox_pads_16_9_scene_to_square():
    """1280x720 场景按 0.5 缩放后上下各留 140px 黑边。"""
    image = np.zeros((720, 1280, 3), dtype=np.uint8)

    padded, ratio, pad = letterbox(image, 640)

    assert padded.shape == (640, 640, 3)
    assert ratio == pytest.approx(0.5)
    assert pad == (0.0, 140.0)


def test_letterbox_square_input_needs_no_padding():
    image = np.zeros((640, 640, 3), dtype=np.uint8)

    padded, ratio, pad = letterbox(image, 640)

    assert padded.shape == (640, 640, 3)
    assert ratio == pytest.approx(1.0)
    assert pad == (0.0, 0.0)


def test_letterbox_padding_uses_the_ultralytics_grey():
    image = np.full((720, 1280, 3), 255, dtype=np.uint8)

    padded, _, _ = letterbox(image, 640)

    assert padded[0, 320].tolist() == [114, 114, 114]
    assert padded[320, 320].tolist() == [255, 255, 255]


def _spy_on_resize(monkeypatch):
    """记录 letterbox 实际传给 cv2.resize 的插值方式。"""
    seen = []
    real_resize = cv2.resize

    def spy(src, dsize, *args, **kwargs):
        seen.append(kwargs.get("interpolation"))
        return real_resize(src, dsize, *args, **kwargs)

    monkeypatch.setattr(cv2, "resize", spy)
    return seen


def test_letterbox_downsamples_with_area_filter(monkeypatch):
    """下采样必须用 INTER_AREA。

    INTER_LINEAR / INTER_CUBIC 只采 2x2 / 4x4 邻域，在 1920x1080 -> 640（3 倍）
    这种大幅下采样时会丢掉大部分像素、产生锯齿，把小图标抹糊。
    实测同一张实机截图：INTER_LINEAR 只认出 2/4，INTER_AREA 认出 4/4。
    """
    seen = _spy_on_resize(monkeypatch)

    letterbox(np.zeros((1080, 1920, 3), dtype=np.uint8), 640)

    assert seen == [cv2.INTER_AREA]


def test_letterbox_upsamples_with_linear_filter(monkeypatch):
    """放大时 INTER_AREA 会退化成最近邻，所以只在 ratio < 1 时用 AREA。"""
    seen = _spy_on_resize(monkeypatch)

    letterbox(np.zeros((200, 300, 3), dtype=np.uint8), 640)

    assert seen == [cv2.INTER_LINEAR]


def test_scale_boxes_undoes_letterbox():
    """letterbox 坐标系下的框还原回原图坐标。"""
    boxes = np.array([[100.0, 200.0, 300.0, 400.0]], dtype=np.float32)

    scaled = scale_boxes(boxes, 0.5, (0.0, 140.0), (720, 1280))

    assert scaled.tolist() == [[200.0, 120.0, 600.0, 520.0]]


def test_scale_boxes_clips_to_image_bounds():
    """超出画面的框会被裁剪到图像范围内。"""
    boxes = np.array([[-100.0, -100.0, 900.0, 900.0]], dtype=np.float32)

    scaled = scale_boxes(boxes, 0.5, (0.0, 140.0), (720, 1280))

    assert scaled.tolist() == [[0.0, 0.0, 1280.0, 720.0]]


def test_scale_boxes_handles_empty_input():
    scaled = scale_boxes(np.zeros((0, 4), dtype=np.float32), 0.5, (0.0, 0.0), (720, 1280))

    assert scaled.shape == (0, 4)


# ---------------------------------------------------------------------------
# nms
# ---------------------------------------------------------------------------


def test_nms_suppresses_overlapping_boxes_and_keeps_lowest_score_separate():
    boxes = np.array(
        [
            [0.0, 0.0, 100.0, 100.0],
            [10.0, 10.0, 110.0, 110.0],  # 与第一个框 IoU≈0.68，应被抑制
            [500.0, 500.0, 600.0, 600.0],  # 完全不重叠，保留
        ],
        dtype=np.float32,
    )
    scores = np.array([0.9, 0.8, 0.7], dtype=np.float32)

    keep = nms(boxes, scores, iou_threshold=0.45)

    assert keep == [0, 2]


def test_nms_returns_empty_for_empty_input():
    assert nms(np.zeros((0, 4), dtype=np.float32), np.zeros((0,), dtype=np.float32)) == []


# ---------------------------------------------------------------------------
# decode_predictions
# ---------------------------------------------------------------------------


def test_decode_end2end_filters_by_confidence():
    output = np.array([[[10, 20, 30, 40, 0.9, 5], [50, 60, 70, 80, 0.2, 7]]], dtype=np.float32)

    result = decode_predictions(output, num_classes=383, conf_threshold=0.35, end2end=True)

    assert result.shape == (1, 6)
    assert result[0, :4].tolist() == [10, 20, 30, 40]
    assert result[0, 4] == pytest.approx(0.9)
    assert result[0, 5] == pytest.approx(5)


def test_decode_end2end_infers_head_from_shape():
    """不显式传 end2end 时，(1, N, 6) 应被识别为 end2end 头。"""
    output = np.array([[[10, 20, 30, 40, 0.9, 5]]], dtype=np.float32)

    result = decode_predictions(output, num_classes=383, conf_threshold=0.35)

    assert result.shape == (1, 6)
    assert result[0, 5] == pytest.approx(5)


def test_decode_traditional_head_converts_cxcywh_and_picks_best_class():
    output = np.zeros((1, 7, 2), dtype=np.float32)
    output[0, :4, 0] = [100, 100, 40, 40]  # cx, cy, w, h
    output[0, 4:, 0] = [0.1, 0.8, 0.2]  # 类别 1 得分最高
    output[0, :4, 1] = [300, 300, 20, 20]
    output[0, 4:, 1] = [0.9, 0.1, 0.05]  # 类别 0 得分最高

    result = decode_predictions(output, num_classes=3, conf_threshold=0.35, end2end=False)

    assert result.shape == (2, 6)
    # nms 按置信度降序返回，0.9 的框在前
    assert result[0, :4].tolist() == [290, 290, 310, 310]
    assert result[0, 4] == pytest.approx(0.9)
    assert result[0, 5] == pytest.approx(0)
    assert result[1, :4].tolist() == [80, 80, 120, 120]
    assert result[1, 5] == pytest.approx(1)


def test_decode_traditional_head_accepts_transposed_layout():
    """未转置的 (1, 4 + nc, M) 也应能解出来。"""
    output = np.zeros((1, 7, 1), dtype=np.float32)
    output[0, :4, 0] = [100, 100, 40, 40]
    output[0, 4:, 0] = [0.1, 0.8, 0.2]

    result = decode_predictions(output, num_classes=3, conf_threshold=0.35, end2end=False)

    assert result.shape == (1, 6)
    assert result[0, 5] == pytest.approx(1)


def test_decode_rejects_unknown_shape():
    with pytest.raises(ValueError):
        decode_predictions(np.zeros((1, 5, 9), dtype=np.float32), num_classes=3, end2end=False)


def test_decode_returns_empty_when_nothing_passes_threshold():
    output = np.array([[[10, 20, 30, 40, 0.1, 5]]], dtype=np.float32)

    result = decode_predictions(output, num_classes=383, conf_threshold=0.35, end2end=True)

    assert result.shape == (0, 6)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_detection_maps_keyword_to_system():
    burn = Detection(0, "gift_9001", 0.9, (0, 0, 10, 10), keyword="Burn")
    keywordless = Detection(1, "gift_9002", 0.9, (0, 0, 10, 10), keyword="Keywordless")
    unknown = Detection(2, "enhance_1", 0.9, (0, 0, 10, 10))

    assert burn.system == "burn"
    assert keywordless.system is None
    assert unknown.system is None


def test_detection_geometry_helpers():
    detection = Detection(0, "gift_9001", 0.9, (10.0, 20.0, 30.0, 60.0))

    assert detection.center == (20, 40)
    assert detection.width == pytest.approx(20.0)
    assert detection.height == pytest.approx(40.0)
    assert detection.area == pytest.approx(800.0)


def test_detection_is_inside_uses_center_with_ratio_tolerance():
    detection = Detection(0, "gift_9001", 0.9, (10.0, 20.0, 30.0, 60.0))  # 中心 (20, 40)

    assert detection.is_inside((0, 0, 100, 100))
    assert not detection.is_inside((50, 50, 100, 100))
    # 中心刚好落在框外一点点，严格判定不通过，10% 容差可以放行
    assert not detection.is_inside((0, 0, 19, 39))
    assert detection.is_inside((0, 0, 19, 39), ratio=0.1)


# ---------------------------------------------------------------------------
# 类别元数据
# ---------------------------------------------------------------------------


def test_metadata_covers_all_model_classes():
    """类别表应与模型的 383 类对齐：381 个饰品 + 2 个强化标记。"""
    assert yolo.num_classes == 383
    assert [item["name"] for item in yolo.classes[-2:]] == ["enhance_1", "enhance_2"]
    assert all(item["name"].startswith("gift_") for item in yolo.classes[:381])


def test_metadata_exposes_gift_fields():
    """类别下标 0 应能查到饰品 id、中英文名与体系关键词。"""
    gift = yolo.classes[0]

    assert gift["name"] == "gift_9001"
    assert gift["gift_id"] == "9001"
    assert gift["name_zh"]
    assert gift["name_en"]
    assert gift["keyword"]


def test_metadata_marks_enhance_classes_as_giftless():
    """强化标记不是饰品，不应带 gift_id，否则会被当成可点击的饰品。"""
    assert all("gift_id" not in item for item in yolo.classes[381:])


def test_white_gossypium_id_matches_mirror_constant():
    """``tasks.mirror.mirror`` 里写死的白棉花 id 必须与元数据一致。"""
    from tasks.mirror.mirror import WHITE_GOSSYPIUM_GIFT_ID

    gossypium = next(item for item in yolo.classes if item.get("name_zh") == "白棉花")

    assert gossypium["gift_id"] == WHITE_GOSSYPIUM_GIFT_ID


def test_available_is_false_without_model_file():
    """模型文件缺失时 available 为 False，predict 直接返回空列表而不抛异常。"""
    if os.path.exists(yolo.model_path):
        pytest.skip("模型文件已就位，该用例只验证缺失时的降级行为")

    assert yolo.available is False
    assert yolo.predict(np.zeros((720, 1280, 3), dtype=np.uint8)) == []


# ---------------------------------------------------------------------------
# 真实模型冒烟
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.path.exists(yolo.model_path),
    reason="ONNX 模型未就位，跳过真实推理冒烟",
)
def test_predict_runs_end_to_end_on_real_model():
    """跑通真实 ONNX：输出能被解码，坐标落在画面内。"""
    image = np.zeros((720, 1280, 3), dtype=np.uint8)

    detections = yolo.predict(image)

    assert isinstance(detections, list)
    for detection in detections:
        assert 0 <= detection.class_id < yolo.num_classes
        assert 0.0 <= detection.confidence <= 1.0
        x1, y1, x2, y2 = detection.xyxy
        assert 0 <= x1 <= x2 <= 1280
        assert 0 <= y1 <= y2 <= 720
