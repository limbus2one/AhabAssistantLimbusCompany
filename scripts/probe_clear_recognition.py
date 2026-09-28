# -*- coding: utf-8 -*-
"""连接模拟器截图,对实时画面执行两种 CLEAR 识别并对比:
1. 旧模板方案(yolo4ego 生产路径):clear_floor.png 灰度多目标模板匹配
2. fixfloor 颜色方案:CLEAR 红字母颜色掩码 + 闭运算 + 连通域计数

画面需停在镜牢"楼层设置面板展开"状态(能看到楼层进度条)结果才有意义;
其他画面下两者都会如实输出低/零命中。
识别截图留档 probe_clear_screenshot.png 供后续核对。
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.getLogger("AALC").setLevel(logging.INFO)

import math

import cv2
import numpy as np

from module.automation import auto
from module.config import cfg

# ===== 内联自 origin/fixfloor:tasks/mirror/floor_detect.py =====
FLOOR_BAND_ROI_1440 = (0, 495, 2560, 590)
FLOOR_CLEAR_MIN_AREA = 300


def clear_badge_mask(roi_rgb):
    """CLEAR 红字母掩码:R-max(G,B)>=60 且 R>=190(与 fixfloor 实现一致)。"""
    r = roi_rgb[:, :, 0].astype(np.int16)
    g = roi_rgb[:, :, 1].astype(np.int16)
    b = roi_rgb[:, :, 2].astype(np.int16)
    m = ((r - np.maximum(g, b)) >= 60) & (r >= 190)
    return m.astype(np.uint8) * 255


def match_color_regions(image, mask_fn, roi=None, min_area=300, min_dist=80, close_size=3, close_iter=2):
    """与 fixfloor 的 ImageUtils.match_color_regions 同实现(掩码->闭运算->连通域->NMS)。"""
    scale = image.shape[0] / 1440
    if roi:
        scaled = [int(round(v * scale)) for v in roi]
        x1, y1, x2, y2 = scaled
        offset = (x1, y1)
        work = image[y1:y2, x1:x2]
    else:
        offset = (0, 0)
        work = image
    mask = np.asarray(mask_fn(work), dtype=np.uint8)
    if close_size > 0 and close_iter > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (close_size, close_size))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=close_iter)
    min_a = max(1, int(min_area * scale * scale))
    count, _, stats, centroids = cv2.connectedComponentsWithStats(mask)
    regions = []
    for i in range(1, count):
        area = stats[i, cv2.CC_STAT_AREA]
        if area >= min_a:
            regions.append((centroids[i][0] + offset[0], centroids[i][1] + offset[1], area))
    merged = []
    for cx, cy, area in sorted(regions, key=lambda r: -r[2]):
        if all(math.hypot(cx - mx, cy - my) > min_dist * scale for mx, my, _ in merged):
            merged.append((int(cx), int(cy), area))
    return merged


def connect_simulator():
    """复用 script_task_scheme.init_game 的 MuMu 连接逻辑(仅连接,不启动游戏/不操作界面)。"""
    from utils.path_manager import path_manager

    # 独立运行时生产环境的路径初始化不会执行,需手动补齐(main.py 启动时调用)
    path_manager.initialize_paths()
    path_manager.set_language("en")   # 当前客户端为英文界面(OCR 证实)
    path_manager.set_theme("default")
    if not cfg.simulator:
        print("!! cfg.simulator=False,当前配置非模拟器模式", flush=True)
        return
    if cfg.simulator_type == 0:
        from module.automation.input_handlers.simulator.mumu_control import MumuControl

        mumu_instance_number = 0
        if cfg.simulator_port == 0 and cfg.mumu_instance_number == -1:
            print("== 未设置模拟器端口或实例编号,使用默认mumu模拟器", flush=True)
        elif cfg.simulator_port != 0:
            if cfg.simulator_port == 16384 or (cfg.simulator_port - 16384) % 32 == 0:
                mumu_instance_number = 0 if cfg.simulator_port == 16384 else (cfg.simulator_port - 16384) // 32
        elif cfg.mumu_instance_number != -1:
            mumu_instance_number = cfg.mumu_instance_number
        print(f"== connecting MuMu instance {mumu_instance_number} ...", flush=True)
        MumuControl(instance_number=mumu_instance_number)
        if MumuControl.connection_device is None:
            print("!! MuMu 连接失败(connection_device 未建立)", flush=True)


def main():
    connect_simulator()
    print("== taking color screenshot ...", flush=True)
    shot = None
    for _ in range(30):
        shot = auto.take_screenshot(gray=False)
        if shot is not None:
            break
    if shot is None:
        print("!! take_screenshot failed")
        return
    rgb = np.array(shot)
    out = "probe_clear_screenshot.png"
    cv2.imwrite(out, cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    print(f"== screenshot saved: {out}  size={rgb.shape[1]}x{rgb.shape[0]}", flush=True)

    # 画面状态锚点
    scale = cfg.set_win_size / 1440
    to_window = auto.find_element("mirror/road_in_mir/to_window_assets.png", threshold=0.75, take_screenshot=True)
    print(f"== anchor to_window_assets(设置面板展开): {'FOUND' if to_window else 'NOT FOUND'}", flush=True)
    theme_pack = auto.find_element("mirror/theme_pack/feature_theme_pack_assets.png")
    print(f"== anchor feature_theme_pack(卡包界面): {'FOUND' if theme_pack else 'NOT FOUND'}", flush=True)

    # 方案一:旧模板路径(与 yolo4ego get_which_floor 完全一致)
    tpl_hits = auto.find_element(
        "mirror/road_in_mir/clear_floor.png",
        find_type="image_with_multiple_targets",
        take_screenshot=True,
        min_dist=80 * scale,
    )
    tpl_hits = tpl_hits or []
    if tpl_hits:
        print(f"== [template] hits={len(tpl_hits)} -> floor={len(tpl_hits) + 1}", flush=True)
        print(f"== [template] positions={[(int(x), int(y)) for x, y in tpl_hits]}", flush=True)
    else:
        print("== [template] hits=0 (识别失败,生产代码将走 not_passed 兜底/保留旧值)", flush=True)

    # 方案二:fixfloor 颜色识别(重新彩色截一帧)
    shot2 = auto.take_screenshot(gray=False)
    if shot2 is None:
        print("!! second screenshot failed")
        return
    rgb2 = np.array(shot2)
    regions = match_color_regions(
        rgb2,
        mask_fn=clear_badge_mask,
        roi=FLOOR_BAND_ROI_1440,
        min_area=FLOOR_CLEAR_MIN_AREA,
        min_dist=80,
    )
    print(f"== [color] regions={len(regions)} -> floor={len(regions) + 1}", flush=True)
    for cx, cy, area in sorted(regions):
        print(f"== [color] region @({cx},{cy}) area={area}px", flush=True)

    # 灰度缓存回填,避免彩色帧污染后续模板匹配
    if auto.screenshot is not None and auto.screenshot.mode != "L":
        auto.screenshot = auto.screenshot.convert("L")
    print("== done", flush=True)


if __name__ == "__main__":
    main()
