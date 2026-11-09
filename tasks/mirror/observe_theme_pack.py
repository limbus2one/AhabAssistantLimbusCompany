"""英文卡包观测：按固定卡位点击，再复核封面和选中状态。"""

from time import sleep

import cv2
import numpy as np

from module.automation import auto
from module.logger import log
from tasks.mirror.select_theme_pack import get_theme_pack_difficulty
from tasks.mirror.theme_pack_images import catalog, find_pack, find_selected_pack, observe_positions
from utils.path_manager import path_manager

UI = "mirror/observe_theme_pack/"


def _scroll_grid(image, scale, direction):
    start_y = 560 if direction > 0 else 990
    auto.mouse_swipe_for_scroll(round(2060 * scale), round(start_y * scale), duration=1, dy=round(200 * direction * scale))
    sleep(0.5)
    scrolled = auto.take_screenshot()
    if scrolled is None or scrolled.size != image.size:
        return None, 0

    # 用相邻截图的一小段列表纹理测量实际位移，搜索范围小于一个行距。
    left, right = round(1175 * scale), round(1495 * scale)
    sample_y = round((465 if direction > 0 else 825) * scale)
    height = round(80 * scale)
    limit = sample_y + direction * round(360 * scale)
    top, bottom = sorted((sample_y, limit))
    sample = np.asarray(image.crop((left, sample_y, right, sample_y + height)))
    search = np.asarray(scrolled.crop((left, top, right, bottom + height)))
    _, score, _, (_, offset) = cv2.minMaxLoc(cv2.matchTemplate(search, sample, cv2.TM_CCOEFF_NORMED))
    if score >= 0.90:
        return scrolled, top + offset - sample_y
    return None, 0


def _scroll_to_pack(y, scale, max_swipes):
    image = auto.take_screenshot()
    if image is None:
        return None
    # 恢复到顶部，卡位表的纵坐标以此为原点。
    for _ in range(max_swipes):
        image, moved = _scroll_grid(image, scale, 1)
        if image is None:
            return None
        if not moved:
            break
    else:
        return None

    for _ in range(max_swipes):
        if y <= round(870 * scale):
            break
        image, moved = _scroll_grid(image, scale, -1)
        if image is None or not moved:
            return None
        y += moved
    return y if round(530 * scale) <= y <= round(870 * scale) else None


def observe_theme_pack(pack_id, hard_mode, floor):
    mode = "hard" if hard_mode else "normal"
    positions = observe_positions()
    rows = positions["floors"][mode].get(str(floor), [])
    slot = next(((row, cards.index(pack_id)) for row, cards in enumerate(rows) if pack_id in cards), None)
    if path_manager.current_language == "zh_cn" or slot is None:
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
        row, column = slot
        x = round(positions["column_x"][column] * scale)
        y = round((positions["first_row_y"] + row * positions["row_height"]) * scale)
        y = _scroll_to_pack(y, scale, len(rows) * 3)
        if y is None:
            log.warning(f"未能将卡包 {pack_id} 的固定卡位滚动到可见范围")
            return False
        auto.mouse_click(x, y)
        sleep(0.7)

        # 只复核点击的卡位，避免把相邻卡包的选中状态当作目标。
        box = (
            x - round(110 * scale),
            max(round(435 * scale), y - round(210 * scale)),
            x + round(110 * scale),
            min(round(1055 * scale), y + round(210 * scale)),
        )
        width = round(192 * scale)
        image = auto.take_screenshot(gray=False)
        selected = find_selected_pack(image.crop(box), pack_id, width) if image else None
        if selected is None:
            log.warning(f"固定卡位未通过卡包 {pack_id} 的图片及选中边框复核")
            return False
        _, width = selected
        # 灰色的 Confirm 形状与可用按钮相同；颜色核验避免不可选/星光不足时确认。
        button = np.asarray(image.crop(tuple(round(p * scale) for p in (1472, 1137, 1696, 1182))), dtype=float)
        r, g, b = button.transpose(2, 0, 1)
        enabled = ((r > 160) & (r > g * 1.05) & (g > b * 1.15)).sum() > 100 * scale * scale
        image = auto.take_screenshot()  # 恢复灰度缓存，并在确认前再次核验目标。
        fresh = find_pack(image.crop(box), pack_id, width) if image else None
        if not enabled or fresh is None or not auto.click_element(UI + "confirm_assets.png", threshold=0.9):
            log.warning("观测卡包不可确认或确认前图片复核失败")
            return False
        sleep(0.7)
        if not auto.click_element(UI + "replace_confirm_assets.png", threshold=0.9, take_screenshot=True):
            log.warning("未匹配到卡包替换的二次确认按钮")
            return False
        for _ in range(10):
            sleep(1)
            image = auto.take_screenshot()
            if image is not None and not auto.find_element(UI + "header_assets.png", threshold=0.9):
                if find_pack(image, pack_id, round(356 * scale)):
                    log.info(f"卡包观测成功: {pack_id} {catalog()['packs'][pack_id]['en']}")
                    return True
        log.warning(f"卡包观测未通过返回页复核: {pack_id}")
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
