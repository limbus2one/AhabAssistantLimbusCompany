from time import sleep

from module.automation import TextMatchResult, auto
from module.config import cfg, theme_list
from module.decorator.decorator import begin_and_finish_time_log
from module.logger import log
from tasks.base.back_init_menu import back_init_menu
from utils.image_utils import ImageUtils
from utils.path_manager import path_manager


@begin_and_finish_time_log(task_name="选择镜牢主题包")
# 选择镜牢主题包
def select_theme_pack(hard_switch=False, floor=None, team_num=None, use_custom_theme_pack_weight=False):
    loop_count = 30
    auto.model = "clam"
    scale = cfg.set_win_size / 1080
    if path_manager.current_language == "zh_cn":
        theme_pack_list_zh = theme_list.get_effective_theme_pack_list(
            hard_switch, "zh_cn", team_num, use_custom_theme_pack_weight
        )
        theme_pack_list_en = {}
    elif path_manager.current_language == "en":
        theme_pack_list_zh = {}
        theme_pack_list_en = theme_list.get_effective_theme_pack_list(
            hard_switch, "en", team_num, use_custom_theme_pack_weight
        )
    else:
        theme_pack_list_zh = theme_list.get_effective_theme_pack_list(
            hard_switch, "zh_cn", team_num, use_custom_theme_pack_weight
        )
        theme_pack_list_en = theme_list.get_effective_theme_pack_list(
            hard_switch, "en", team_num, use_custom_theme_pack_weight
        )
    if auto.find_element("mirror/theme_pack/normal_assets.png"):
        difficulty = "normal"
    elif auto.find_element("mirror/theme_pack/hard_assets.png"):
        difficulty = "hard"
    else:
        raise Exception("无法识别当前主题包难度")
    all_theme_pack = auto.find_element(
        "mirror/theme_pack/theme_pack_features.png", find_type="image_with_multiple_targets")
    all_theme_pack.sort(key=lambda pos: (pos[0], pos[1]))
    if floor == 4 and cfg.select_event_pack:
        auto.mouse_drag_down(all_theme_pack[0][0], all_theme_pack[0][1])
        log.debug(f"选择卡包: {all_theme_pack[0]}")
        msg = "此次主题包选择了最左边的（活动）卡包"
        log.info(msg)
        auto.wait_page_load("mirror/road_in_mir/legend_assets.png")
        return

    if floor == 4 and cfg.skip_event_pack:
        all_theme_pack.pop(0)  # 删除最左边的卡包

    for pack in all_theme_pack:
        top_left = (
            max(pack[0] - 210 * scale, 0),
            max(pack[1] - 60 * scale, 0),
        )
        bottom_right = (
            min(pack[0] + 60 * scale, cfg.set_win_size * 16 / 9),
            min(pack[1] + 390 * scale, cfg.set_win_size),
        )
        crop = (top_left[0], top_left[1],
                bottom_right[0], bottom_right[1])
    for i in range(3):
        result = auto.find_language_text(
            theme_pack_list_zh, theme_pack_list_en, crop)
        if isinstance(result, TextMatchResult):
            theme_pack_weight = result.value
            theme_pack_name = result.text
        else:
            theme_pack_weight = -5
            theme_pack_name = "unknown"

        weight_list = []
        pack_name = []
        weight_list.append(theme_pack_weight)  # 采用最大值的形式，权重越大，优先级越高
        pack_name.append(theme_pack_name)

        # 选择权重最大的主题包
        max_weight = max(weight_list)
        log.debug(f"当前主题包权重列表：{list(zip(pack_name, weight_list))}")
        # 如果存在权重最大值大于等于优选阈值的主题包，则选择该主题包
        if max_weight >= int(theme_list.preferred_thresholds):
            max_index = weight_list.index(max_weight)
            pack = all_theme_pack[max_index]
            auto.mouse_drag_down(pack[0], pack[1])
            log.debug(f"选择卡包: {pack}")
            msg = f"此次选择卡包关键词：{pack_name[max_index]}"
            log.info(msg)
            auto.wait_page_load("mirror/road_in_mir/legend_assets.png")
            return

        # 刷新卡包
        auto.click_element("mirror/theme_pack/refresh_assets.png")
        auto.wait_page_load(["mirror/theme_pack/pack_info_35_assets.png",
                            "mirror/theme_pack/pack_info_4_assets.png"])



    max_weight = max(weight_list)
    max_index = weight_list.index(max_weight)
    pack = all_theme_pack[max_index]
    auto.mouse_drag_down(pack[0], pack[1])
    log.debug(f"选择卡包: {pack}")
    log.debug("无匹配最低阈值的主题包，选择最高权重主题包")
    msg = f"无匹配最低阈值的主题包，选择最高权重主题包\n此次选择卡包关键词：{pack_name[max_index]}"
    log.info(msg)
    auto.wait_page_load("mirror/road_in_mir/legend_assets.png")
    return