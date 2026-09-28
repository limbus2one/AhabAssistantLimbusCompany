# -*- coding: utf-8 -*-
"""诊断当前模拟器画面:OCR 文本 + 关键锚点 + 颜色命中区域的颜色采样。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import logging

logging.getLogger("AALC").setLevel(logging.INFO)

import cv2
import numpy as np

from module.automation import auto
from module.config import cfg
from module.ocr import ocr


def anchor(name, path, threshold=0.75, **kw):
    pos = auto.find_element(path, threshold=threshold, **kw)
    print(f"  {name:28s} {'FOUND' if pos else '-'}")
    return pos


def main():
    from module.automation.input_handlers.simulator.mumu_control import MumuControl

    if MumuControl.connection_device is None:
        MumuControl(instance_number=0)

    shot = None
    for _ in range(30):
        shot = auto.take_screenshot(gray=False)
        if shot is not None:
            break
    rgb = np.array(shot)
    print(f"== screenshot {rgb.shape[1]}x{rgb.shape[0]}")

    print("== anchors:")
    anchor("battle/in_mirror(战斗中)", "battle/in_mirror_assets.png", 0.7)
    anchor("battle/pause(暂停菜单)", "battle/pause_assets.png", 0.7)
    anchor("road_in_mir/legend(镜牢地图页)", "mirror/road_in_mir/legend_assets.png", 0.7)
    anchor("road_in_mir/enter(进入节点)", "mirror/road_in_mir/enter_assets.png", 0.7)
    anchor("theme_pack/feature(卡包界面)", "mirror/theme_pack/feature_theme_pack_assets.png", 0.7)
    anchor("road_in_mir/to_window(设置面板)", "mirror/road_in_mir/to_window_assets.png", 0.75)
    anchor("claim_reward(结算)", "mirror/claim_reward/claim_rewards_assets.png", 0.7)
    anchor("select_team(选队)", "mirror/road_to_mir/select_team_stars_assets.png", 0.7)

    print("== OCR (top band y0-200):")
    top = rgb[0:200, :, :]
    result = ocr.run(top)
    if result is not None and getattr(result, "txts", None):
        print(f"  texts: {list(result.txts)}")
    else:
        print("  (no text)")

    print("== OCR (full):")
    result = ocr.run(rgb)
    if result is not None and getattr(result, "txts", None):
        print(f"  texts: {list(result.txts)[:15]}")
    else:
        print("  (no text)")

    # 两个颜色命中区域的中心颜色采样(RGB 9x9 均值)
    for cx, cy in [(747, 408), (853, 408)]:
        patch = rgb[cy - 4 : cy + 5, cx - 4 : cx + 5]
        mean = patch.reshape(-1, 3).mean(axis=0)
        print(f"== region @({cx},{cy}) mean RGB = ({mean[0]:.0f}, {mean[1]:.0f}, {mean[2]:.0f})")

    # ROI 横带裁剪留档
    scale = rgb.shape[0] / 1440
    x1, y1, x2, y2 = [int(round(v * scale)) for v in (0, 495, 2560, 590)]
    cv2.imwrite("probe_roi_band.png", cv2.cvtColor(rgb[y1:y2, x1:x2], cv2.COLOR_RGB2BGR))
    print("== ROI band saved: probe_roi_band.png")
    if auto.screenshot is not None and auto.screenshot.mode != "L":
        auto.screenshot = auto.screenshot.convert("L")


if __name__ == "__main__":
    main()
