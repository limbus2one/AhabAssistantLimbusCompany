# -*- coding: utf-8 -*-
"""
镜牢楼层识别（颜色计数）测试

match_color_regions 的纯函数行为 + 标定参数对真实截图样本的识别结果。
样本图命名约定：floor{N}_<语言>_<宽>x<高>.png（或 floor{N}_map_... 表示地图页状态），
N 为截图时的真实楼层（= 进度条上 CLEAR 红字母标记数 + 1），断言识别层数与文件名一致。
改动 tasks/mirror/floor_detect.py 的标定参数后，若与样本不符本测试会失败提醒。
样本采集方式见 tests/fixtures/mirror_floor/README.md。
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from tasks.mirror.floor_detect import (
    FLOOR_BAND_ROI_1440,
    FLOOR_CLEAR_MIN_AREA,
    clear_badge_mask,
)
from utils.image_utils import ImageUtils

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "mirror_floor"

# 合成颜色取自实况样本实测：
# CLEAR 红字母 R-max(G,B) p95≈190；金色当前标记 p95≈40；木纹 p95≈21
BADGE_RED_BGR = (40, 40, 230)  # 红色字母：R-max(G,B)=190，R=230
GOLD_MARKER_BGR = (82, 195, 223)  # 金色当前位置标记：R-max(G,B)=28，应排除
DIM_WOOD_BGR = (116, 137, 160)  # 木纹底色：R-max(G,B)=23，应排除


def _count_floor(image_bgr):
    """按 get_which_floor 的生产参数识别层数（ROI 为 1440 基准，函数内部按图像高度缩放）。"""
    regions = ImageUtils.match_color_regions(
        image_bgr,
        roi=FLOOR_BAND_ROI_1440,
        min_area=FLOOR_CLEAR_MIN_AREA,
        min_dist=80,
        mask_fn=clear_badge_mask,
    )
    return len(regions) + 1


def test_synthetic_blobs():
    """合成图像：N 个 CLEAR 红块应数出 N 个区域，层数 = N + 1。"""
    for n in range(0, 6):
        img = np.full((1440, 2560, 3), DIM_WOOD_BGR, dtype=np.uint8)
        for i in range(n):
            x = 400 + i * 400
            img[520:570, x : x + 60] = BADGE_RED_BGR
        assert _count_floor(img) == n + 1, f"n={n} 时层数识别错误"


def test_synthetic_ignores_dim_red():
    """暗红底色不应被误计入（模拟木纹 R-max(G,B)=23、R=160）。"""
    img = np.full((1440, 2560, 3), DIM_WOOD_BGR, dtype=np.uint8)
    img[520:570, 400:460] = BADGE_RED_BGR
    assert _count_floor(img) == 2


def test_synthetic_ignores_gold_current_marker():
    """金色"当前位置"菱形（地图页/进度条上的非红色亮块）不应被计入 CLEAR 计数。"""
    img = np.full((1440, 2560, 3), DIM_WOOD_BGR, dtype=np.uint8)
    img[520:570, 400:460] = BADGE_RED_BGR  # 1 枚 CLEAR
    img[520:570, 800:860] = GOLD_MARKER_BGR  # 金色当前标记（无红字母）
    assert _count_floor(img) == 2


@pytest.mark.parametrize(
    "fixture", sorted(FIXTURE_DIR.glob("floor*_*.png")), ids=lambda p: p.name
)
def test_real_screenshots(fixture):
    """真实截图样本：识别层数必须等于文件名标注的楼层。"""
    expected = int(fixture.name.split("_")[0].replace("floor", ""))
    image = cv2.imread(str(fixture))
    assert image is not None, f"样本读取失败: {fixture}"
    assert _count_floor(image) == expected, f"样本 {fixture.name} 楼层识别错误"
