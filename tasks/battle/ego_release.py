"""指定黑兽浮士德 E.G.O. 的排入与行动槽复核（英文素材）。"""

from time import sleep

import cv2
import numpy as np

from module.automation import auto
from module.config import cfg
from module.logger import log
from utils.image_utils import ImageUtils
from utils.path_manager import path_manager

UI = "battle/ego_release/"


def _assigned_enemy_target(scale):
    """沿胜率分配的青色连线取敌方端点，避开右侧资源栏与下方技能栏。"""
    if auto.take_screenshot(gray=False) is None:
        return None
    rgb = np.array(auto.screenshot)
    auto.screenshot = auto.screenshot.convert("L")
    x1, y1, x2, y2 = (round(v * scale) for v in (200, 140, 2350, 990))
    roi = rgb[y1:y2, x1:x2]
    mask = ((roi[:, :, 0] < 50) & (roi[:, :, 1] > 200) & (roi[:, :, 2] > 220)).astype("uint8")
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    candidates = [
        i
        for i in range(1, count)
        if stats[i, cv2.CC_STAT_WIDTH] > 150 * scale and stats[i, cv2.CC_STAT_AREA] > 300 * scale * scale
    ]
    if not candidates:
        return None
    component = max(candidates, key=lambda i: stats[i, cv2.CC_STAT_AREA])
    ys, xs = np.where(labels == component)
    end_x = xs.max()
    if x1 + end_x < 1200 * scale:
        return None
    return (int(x1 + end_x + 6 * scale), int(y1 + np.median(ys[xs >= end_x - 1])))


def prepare_faust_ego(corrosion=False):
    """成功排入技能才返回 True；确认后的状态不明时中止战斗，避免误放。"""
    required = (
        "faust_avatar.png",
        "ego_name.png",
        "ego_corrosion_name.png",
        "ego_skill.png",
        "ego_skill_center.png",
        "overclock_slot.png",
        "details.png",
    )
    if path_manager.current_language == "zh_cn" or any(
        not ImageUtils.existing_image_paths(UI + name) for name in required
    ):
        log.warning("指定浮士德 E.G.O. 的英文素材尚不完整，跳过释放")
        return False
    if auto.take_screenshot() is None:
        return False
    scale = cfg.set_win_size / 1440
    portraits = tuple(round(v * scale) for v in (200, 1240, 2350, 1370))
    cards = tuple(round(v * scale) for v in (150, 200, 2350, 1030))

    def find(name, box, fresh=False):
        box = tuple(round(v) for v in box)
        position = auto.find_element(UI + name, threshold=0.90, model="aggressive", my_crop=box, take_screenshot=fresh)
        # 单图裁剪匹配返回裁剪内坐标，输入必须换算回完整截图。
        return (position[0] + box[0], position[1] + box[1]) if position else None

    avatar = find("faust_avatar.png", portraits)
    if not avatar:
        return False
    target = _assigned_enemy_target(scale)
    if target is None:
        log.info("无法确认胜率分配的敌方目标，本回合跳过指定 E.G.O.")
        return False
    avatar = find("faust_avatar.png", portraits, fresh=True)
    if not avatar:
        return False
    auto.mouse_drag(*avatar, drag_time=1.2)
    sleep(0.5)
    try:
        name = find("ego_name.png", cards, fresh=True) or find("ego_corrosion_name.png", cards)
        if not name:
            return False
        # 卡名与卡头的固定间距；长按卡头下方切换 OVERCLOCK。
        header = (name[0], name[1] - 330 * scale)
        card_box = (name[0] - 210 * scale, 200 * scale, name[0] + 210 * scale, 1030 * scale)
        overclock = bool(find("ego_corrosion_name.png", card_box))
        if overclock != corrosion:
            auto.mouse_drag(*header, drag_time=1.2)
            sleep(0.5)
            if bool(find("ego_corrosion_name.png", card_box, fresh=True)) != corrosion:
                return False
        # 点卡面确认；卡头仅切换预览。已展开的预览只需确认一次。
        expected_name = "ego_corrosion_name.png" if corrosion else "ego_name.png"
        for _ in range(2):
            if not find(expected_name, card_box, fresh=True):
                raise RuntimeError("E.G.O. 确认前卡名复核失败，停止当前战斗操作")
            auto.mouse_click(name[0], name[1] - 170 * scale)
            sleep(0.5)
            auto.take_screenshot()
            if not find("details.png", cards):
                break
        else:
            log.info("指定 E.G.O. 当前无法排入行动槽，后续回合再试")
            return False
        slot = (avatar[0] - 100 * scale, 1000 * scale, avatar[0] + 80 * scale, 1230 * scale)
        # 先展开技能预览再收起，让聚焦战斗的镜头移动完成后再拖动。
        source_y = 1145 * scale
        auto.mouse_click(avatar[0], source_y)
        sleep(0.5)
        auto.mouse_click(2390 * scale, 1050 * scale)
        sleep(0.5)
        # 沿其余胜率连线重新确认镜头移动后的敌方端点。
        target = _assigned_enemy_target(scale)
        if target is None:
            raise RuntimeError("E.G.O. 确认后无法复核敌方目标，停止当前战斗操作")
        # 卡片确认只替换备选技能；拖至已确认的敌方目标才实际分配行动。
        auto.mouse_drag(avatar[0], source_y, dx=target[0] - avatar[0], dy=target[1] - source_y, drag_time=0.6)
        sleep(0.5)
        auto.mouse_click_blank()
        sleep(0.3)
        auto.take_screenshot()
        marker = "overclock_slot.png" if corrosion else "ego_skill.png"
        verified = find(marker, slot)
        if not corrosion and not verified:
            verified = find("ego_skill_center.png", slot)
        if not verified:
            raise RuntimeError("E.G.O. 已确认但行动槽复核失败，停止当前战斗操作")
        log.info(f"指定浮士德 E.G.O. 已排入行动槽：{'侵蚀' if corrosion else '普通'}")
        return True
    finally:
        if path_manager.current_language != "en" or find("details.png", cards, fresh=True):
            auto.mouse_click(2390 * scale, 1050 * scale)
            sleep(0.3)
            auto.take_screenshot()
