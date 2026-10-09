"""英文卡包观测：按图鉴地图滚动，匹配封面并复核选中状态。"""

from time import sleep

import numpy as np

from module.automation import auto
from module.logger import log
from tasks.mirror.select_theme_pack import get_theme_pack_difficulty
from tasks.mirror.theme_pack_images import catalog, find_pack, find_selected_pack
from utils.path_manager import path_manager

UI = "mirror/observe_theme_pack/"


def observe_theme_pack(pack_id, hard_mode, floor):
    mode = "hard" if hard_mode else "normal"
    ids = catalog()["maps"].get(mode, {}).get(str(floor), [])
    if path_manager.current_language == "zh_cn" or pack_id not in ids:
        log.warning(f"观测卡包不支持当前语言、难度或楼层: {pack_id}, {mode}, {floor}")
        return False
    if get_theme_pack_difficulty() != mode:
        log.warning("当前选包难度未通过图片核验，取消卡包观测")
        return False
    image = auto.take_screenshot()
    if image is None:
        return False
    scale = image.height / 1440
    if find_pack(image, pack_id, round(356 * scale)):
        log.info(f"指定卡包已在选包页: {catalog()['packs'][pack_id]['en']}")
        return True
    try:
        # 上一次中断留下的二次确认必须先取消，重新从目标图片核验开始。
        if auto.click_element(UI + "replace_cancel_assets.png", threshold=0.9):
            sleep(0.5)
            auto.take_screenshot()
        if not auto.find_element(UI + "header_assets.png", threshold=0.9):
            if not auto.find_element(UI + "search_slot_assets.png", threshold=0.9, model="aggressive"):
                if not auto.click_element(UI + "pack_search_assets.png", threshold=0.9):
                    log.warning("未匹配到英文 Pack Search 按钮")
                    return False
                sleep(0.7)
            slots = auto.find_element(
                UI + "search_slot_assets.png",
                threshold=0.9,
                find_type="image_with_multiple_targets",
                take_screenshot=True,
            )
            if not slots:
                log.warning("未匹配到可观测的卡包槽位")
                return False
            auto.mouse_click(*min(slots))
        for _ in range(5):
            if auto.find_element(UI + "header_assets.png", threshold=0.9, take_screenshot=True):
                break
            sleep(1)
        else:
            log.warning("观测列表未打开，取消观测")
            return False
        # 地图为五列、按编号排列；先回到列表顶部，再向目标所在行导航。
        auto.mouse_swipe_for_scroll(2060 * scale, 560 * scale, duration=1, dy=470 * scale)
        sleep(0.5)
        box = tuple(round(p * scale) for p in (1140, 435, 2340, 1055))
        width = round(192 * scale)
        previous = None
        for _ in range((ids.index(pack_id) // 5 + 1) * 3 + 2):
            image = auto.take_screenshot()
            if image is None:
                return False
            region = image.crop(box)
            if match := find_pack(region, pack_id, width):
                # 点击前使用独立的新截图复核，不按地图坐标盲点。
                image = auto.take_screenshot()
                match = find_pack(image.crop(box), pack_id, width) if image else None
                if match is None:
                    log.warning("观测卡包在点击前的图片复核失败")
                    return False
                auto.mouse_click(match.position[0] + box[0], match.position[1] + box[1])
                sleep(0.7)
                image = auto.take_screenshot(gray=False)
                selected = find_selected_pack(image.crop(box), pack_id, width) if image else None
                if selected is None:
                    log.warning("观测卡包未通过选中边框复核")
                    return False
                match, width = selected
                # 灰色的 Confirm 形状与可用按钮相同；颜色核验避免不可选/星光不足时确认。
                button = np.asarray(image.crop(tuple(round(p * scale) for p in (1472, 1137, 1696, 1182))), dtype=float)
                r, g, b = button.transpose(2, 0, 1)
                enabled = ((r > 160) & (r > g * 1.05) & (g > b * 1.15)).sum() > 100 * scale * scale
                auto.take_screenshot()  # 恢复项目要求的灰度缓存，并在确认前再次核验目标。
                fresh = find_pack(auto.screenshot.crop(box), pack_id, width)
                if not enabled or fresh is None or not auto.click_element(UI + "confirm_assets.png", threshold=0.9):
                    log.warning("观测卡包不可确认或确认前图片复核失败")
                    return False
                sleep(0.7)
                if not auto.click_element(UI + "replace_confirm_assets.png", threshold=0.9, take_screenshot=True):
                    log.warning("未匹配到卡包替换的二次确认按钮")
                    return False
                result = False
                for _ in range(10):
                    sleep(1)
                    image = auto.take_screenshot()
                    if image is not None and not auto.find_element(UI + "header_assets.png", threshold=0.9):
                        result = find_pack(image, pack_id, round(356 * scale)) is not None
                        if result:
                            break
                log.info(f"卡包观测{'成功' if result else '未通过复核'}: {pack_id} {catalog()['packs'][pack_id]['en']}")
                return result
            current = np.asarray(region, dtype=float)
            if previous is not None and np.abs(current - previous).mean() < 0.7:
                break
            previous = current
            auto.mouse_swipe_for_scroll(2060 * scale, 990 * scale, duration=1, dy=-200 * scale)
            sleep(0.5)
        log.warning(f"未在观测列表中匹配到卡包 {pack_id}")
        return False
    finally:
        auto.take_screenshot()
        if auto.click_element(UI + "replace_cancel_assets.png", threshold=0.9):
            sleep(0.5)
            auto.take_screenshot()
        if auto.click_element(UI + "cancel_assets.png", threshold=0.9):
            sleep(0.5)
            auto.take_screenshot()
        if auto.find_element(UI + "search_slot_assets.png", threshold=0.9, model="aggressive"):
            auto.click_element(UI + "pack_search_assets.png", threshold=0.9)
            sleep(0.5)
            auto.take_screenshot()
