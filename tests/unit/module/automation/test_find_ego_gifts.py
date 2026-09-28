"""``auto.find_ego_gifts`` 的接线测试。

模型本身的解码与推理在 ``tests/unit/module/yolo/`` 覆盖；这里只验证 automation 层
的逻辑，把 ``yolo.predict`` 顶成假的，所以不需要真实截图：

- 灰度帧必须重新取彩色图（YOLO 依赖颜色），彩色帧则要复用、不重复截图
  —— 这是「acquire_ego_gift 一次循环只截一帧」这条优化能不能成立的关键；
- 自动过滤掉 ``enhance_1`` / ``enhance_2`` 强化标记；
- ``my_crop`` 按检测框中心过滤，而不是裁剪输入图；
- 用完必须把缓存帧还原成灰度，否则后续 ``cv2.matchTemplate`` 会因为
  截图是 3 通道、模板是灰度而直接报错；
- 推理异常时返回空列表而不是把异常抛给业务循环。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from module.automation import auto  # noqa: E402
from module.yolo import yolo  # noqa: E402
from module.yolo.yolo import Detection  # noqa: E402

FRAME_SIZE = (1920, 1080)


def _color_frame() -> Image.Image:
    """模拟一帧彩色截图（RGB）。"""
    width, height = FRAME_SIZE
    return Image.fromarray(np.zeros((height, width, 3), dtype=np.uint8), mode="RGB")


def _gray_frame() -> Image.Image:
    """模拟一帧灰度截图（L），业务循环里默认拿到的就是这种。"""
    width, height = FRAME_SIZE
    return Image.fromarray(np.zeros((height, width), dtype=np.uint8), mode="L")


def _gift(x: int = 100, y: int = 100, confidence: float = 0.9) -> Detection:
    return Detection(
        class_id=0,
        class_name="gift_9001",
        confidence=confidence,
        xyxy=(x, y, x + 50, y + 50),
        gift_id="9001",
        name_zh="炼狱炎蝶之梦",
        keyword="Burn",
    )


def _enhance_marker(x: int = 400, y: int = 400) -> Detection:
    return Detection(class_id=381, class_name="enhance_1", confidence=0.95, xyxy=(x, y, x + 40, y + 40))


class _Recorder:
    """记录 take_screenshot 的调用参数，并返回可控的帧。"""

    def __init__(self, frame: Image.Image) -> None:
        self.frame = frame
        self.calls: list[bool] = []

    def __call__(self, gray: bool = True):
        self.calls.append(gray)
        auto.screenshot = self.frame
        return self.frame


@pytest.fixture
def fake_predict(monkeypatch):
    """把 yolo.predict 换成返回固定结果的假实现。"""

    def _install(detections):
        def predict(image, conf=None):
            assert image.mode == "RGB"
            assert image.size == FRAME_SIZE
            return list(detections)

        monkeypatch.setattr(yolo, "predict", predict)
        return detections

    return _install


# ---------------------------------------------------------------------------
# 截图策略
# ---------------------------------------------------------------------------


def test_gray_frame_triggers_color_recapture(monkeypatch, fake_predict):
    """当前帧是灰度时，必须重新取一张彩色图再推理。"""
    recorder = _Recorder(_color_frame())
    monkeypatch.setattr(auto, "take_screenshot", recorder)
    monkeypatch.setattr(auto, "screenshot", _gray_frame())
    fake_predict([_gift()])

    detections = auto.find_ego_gifts()

    assert recorder.calls == [False], "灰度帧应触发一次 gray=False 的截图"
    assert len(detections) == 1
    assert detections[0].gift_id == "9001"


def test_color_frame_is_reused_without_extra_screenshot(monkeypatch, fake_predict):
    """当前帧已经是彩色时直接复用，不再截图。

    acquire_ego_gift 在循环顶部先取一帧彩色图，再调这个方法；如果这里又截一次，
    每次循环就会因为截图间隔节流白白多花将近一秒。
    """
    recorder = _Recorder(_color_frame())
    monkeypatch.setattr(auto, "take_screenshot", recorder)
    monkeypatch.setattr(auto, "screenshot", _color_frame())
    fake_predict([_gift()])

    auto.find_ego_gifts()

    assert recorder.calls == [], "彩色帧应被复用，不该再截图"


def test_take_screenshot_flag_forces_recapture(monkeypatch, fake_predict):
    """显式要求重新截图时，即使当前是彩色帧也要重取。"""
    recorder = _Recorder(_color_frame())
    monkeypatch.setattr(auto, "take_screenshot", recorder)
    monkeypatch.setattr(auto, "screenshot", _color_frame())
    fake_predict([_gift()])

    auto.find_ego_gifts(take_screenshot=True)

    assert recorder.calls == [False]


def test_returns_empty_list_when_screenshot_fails(monkeypatch, fake_predict):
    """截图失败时返回空列表，不把 None 往下传。"""
    monkeypatch.setattr(auto, "take_screenshot", lambda gray=True: None)
    monkeypatch.setattr(auto, "screenshot", None)
    fake_predict([_gift()])

    assert auto.find_ego_gifts() == []


# ---------------------------------------------------------------------------
# 过滤
# ---------------------------------------------------------------------------


def test_drops_enhance_markers(monkeypatch, fake_predict):
    """强化标记（enhance_1 / enhance_2）不是饰品，不能当成可点击的卡片。"""
    monkeypatch.setattr(auto, "take_screenshot", _Recorder(_color_frame()))
    monkeypatch.setattr(auto, "screenshot", _color_frame())
    fake_predict([_gift(), _enhance_marker()])

    detections = auto.find_ego_gifts()

    assert [item.class_name for item in detections] == ["gift_9001"]


def test_find_ego_gifts_captures_rgb_and_filters_markers_and_region(monkeypatch, fake_predict):
    frame = _color_frame()
    calls = []

    def capture(gray=True):
        calls.append(gray)
        auto.screenshot = frame
        return frame

    monkeypatch.setattr(auto, "take_screenshot", capture)
    monkeypatch.setattr(auto, "screenshot", None)
    fake_predict([_gift(), _enhance_marker(100, 100), _gift(400, 400)])

    detections = auto.find_ego_gifts(my_crop=(0, 0, 200, 200), take_screenshot=True)

    assert calls == [False]
    assert detections == [_gift()]
    assert auto.screenshot.mode == "L"


def test_my_crop_filters_by_center(monkeypatch, fake_predict):
    """my_crop 只保留中心落在区域内的检测，坐标保持整图绝对坐标。"""
    monkeypatch.setattr(auto, "take_screenshot", _Recorder(_color_frame()))
    monkeypatch.setattr(auto, "screenshot", _color_frame())
    # 中心 (125, 125) 在框内；中心 (425, 425) 在框外
    fake_predict([_gift(100, 100), _gift(400, 400)])

    detections = auto.find_ego_gifts(my_crop=(0, 0, 200, 200))

    assert len(detections) == 1
    assert detections[0].center == (125, 125)


def test_my_crop_does_not_shift_coordinates(monkeypatch, fake_predict):
    """限定范围时不应该裁剪输入图，因此坐标不能被裁剪偏移污染。"""
    monkeypatch.setattr(auto, "take_screenshot", _Recorder(_color_frame()))
    monkeypatch.setattr(auto, "screenshot", _color_frame())
    fake_predict([_gift(1000, 600)])

    detections = auto.find_ego_gifts(my_crop=(900, 500, 1200, 800))

    assert detections[0].xyxy == (1000.0, 600.0, 1050.0, 650.0)


# ---------------------------------------------------------------------------
# 状态还原
# ---------------------------------------------------------------------------


def test_gray_frame_is_restored_after_detection(monkeypatch, fake_predict):
    """用完必须把缓存帧还原成灰度，否则后续模板匹配会因通道数不一致报错。"""
    monkeypatch.setattr(auto, "take_screenshot", _Recorder(_color_frame()))
    monkeypatch.setattr(auto, "screenshot", _gray_frame())
    fake_predict([_gift()])

    auto.find_ego_gifts()

    assert auto.screenshot.mode == "L"


def test_gray_frame_is_restored_even_when_predict_raises(monkeypatch):
    """推理异常也要走 finally 还原灰度。"""
    monkeypatch.setattr(auto, "take_screenshot", _Recorder(_color_frame()))
    monkeypatch.setattr(auto, "screenshot", _gray_frame())

    def _boom(image, conf=None, iou=None):
        raise RuntimeError("模拟推理失败")

    monkeypatch.setattr(yolo, "predict", _boom)

    assert auto.find_ego_gifts() == []
    assert auto.screenshot.mode == "L"
