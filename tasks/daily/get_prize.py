from time import sleep, time

import numpy as np

from module.automation import auto
from module.decorator.decorator import begin_and_finish_time_log
from module.logger import log
from tasks.base.retry import check_times, retry
from utils.image_utils import ImageUtils

# Badge regions use the 2560×1440 baseline and scale with the screenshot.
PASS_DAILY_DOT_ROI = (520, 365, 615, 440)
PASS_WEEKLY_DOT_ROI = (520, 650, 615, 735)
PASS_SEASONAL_DOT_ROI = (520, 935, 615, 1020)
PASS_MISSION_PAGE_HEIGHT = 5 * 174
PASS_DOT_MIN_AREA = 500


def pass_red_dot_mask(roi_rgb):
    """提取提示徽章的暗红底色，排除金框、棕色卡面和褪色 CLEAR 印章。"""
    r, g, b = np.moveaxis(roi_rgb.astype(np.int16), -1, 0)
    mask = (r >= 110) & (r - np.maximum(g, b) >= 100) & (g <= 30) & (b <= 30)
    return mask.astype(np.uint8) * 255


def _claim_pass_missions(start_time):
    """领取通行证经验"""
    while True:
        start_time = max(start_time, auto.get_restore_time() or start_time)
        if check_times(start_time):
            log.error("等待通行证任务截图超时")
            return False
        if auto.take_screenshot() is not None:
            break
    positions = auto.find_element("pass/pass_coin.png", find_type="image_with_multiple_targets") or []
    for x, y in sorted(positions, key=lambda position: position[1]):
        auto.mouse_click(x, y)
    return retry() is not False


def _scroll_pass_missions():
    """向上拖动五张卡片的距离，进入赛季第二页。"""
    scale = auto.screenshot.height / 1440
    auto.mouse_swipe_for_scroll(
        round(1000 * scale),
        round(1200 * scale),
        dy=-round(PASS_MISSION_PAGE_HEIGHT * scale),
    )
    sleep(0.5)


@begin_and_finish_time_log(task_name="收取通行证奖励", calculate_time=False)
def get_pass_prize():
    auto.model = "clam"
    start_time = time()
    while True:
        start_time = max(start_time, auto.get_restore_time() or start_time)
        if check_times(start_time):
            log.error("无法收取通行证奖励：进入任务页面超时")
            return False
        if auto.take_screenshot() is None:
            continue
        if auto.click_element("pass/pass_missions_assets.png"):
            break
        if auto.click_element("home/season_assets.png"):
            continue
        season_bbox = ImageUtils.get_bbox(ImageUtils.load_image("home/season_assets.png"))
        if auto.find_text_element(["season", "seasun"], season_bbox):
            auto.mouse_click(
                (season_bbox[0] + season_bbox[2]) / 2,
                (season_bbox[1] + season_bbox[3]) / 2,
            )
            continue
        auto.mouse_to_blank()
    for name, target, tab_roi in (
        ("Daily", "pass/daily_assets.png", PASS_DAILY_DOT_ROI),
        ("Weekly", "pass/weekly_assets.png", PASS_WEEKLY_DOT_ROI),
        ("Seasonal", "pass/seasonal_assets.png", PASS_SEASONAL_DOT_ROI),
    ):
        start_time = time()
        while True:
            start_time = max(start_time, auto.get_restore_time() or start_time)
            if check_times(start_time):
                log.error(f"无法收取{name}：领取超时")
                return False
            tab_dots = auto.find_regions_by_color(tab_roi, pass_red_dot_mask, min_area=PASS_DOT_MIN_AREA, min_dist=80)
            if tab_dots is None:
                continue
            if not tab_dots:
                break
            if name == "Seasonal":
                if not auto.click_element("pass/weekly_assets.png"):
                    continue
            if not auto.click_element(target):
                continue
            if not _claim_pass_missions(start_time):
                return False
            if name == "Seasonal":
                _scroll_pass_missions()
                if not _claim_pass_missions(start_time):
                    return False

    start_time = time()
    for target in ("pass/battle_pass_assets.png", "pass/claim_all_assets.png"):
        while True:
            start_time = max(start_time, auto.get_restore_time() or start_time)
            if check_times(start_time):
                log.error(f"Timed out claiming Battle Pass rewards: {target}")
                return False
            if auto.take_screenshot() is None:
                continue
            if auto.click_element(target):
                break
    return retry() is not False


@begin_and_finish_time_log(task_name="收取邮箱", calculate_time=False)
def get_mail_prize():
    auto.model = "clam"
    start_time = time()
    while True:
        start_time = max(start_time, auto.get_restore_time() or start_time)
        if check_times(start_time):
            log.error("Failed to claim mail rewards: timed out")
            return False
        # 自动截图
        if auto.take_screenshot() is None:
            continue
        if retry() is False:
            return False
        if auto.click_element("mail/get_mail_prize_confirm.png"):
            auto.click_element("mail/close_assets.png")
            break
        if auto.click_element("mail/claim_all_assets.png"):
            sleep(3)
            if retry():
                continue
            auto.click_element("mail/close_assets.png")
            break
        if auto.click_element("home/mail_assets.png"):
            continue
        auto.mouse_to_blank()
