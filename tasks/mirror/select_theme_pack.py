from time import sleep

from module.automation import TextMatchResult, auto
from module.config import cfg, theme_list
from module.decorator.decorator import begin_and_finish_time_log
from module.logger import log
from tasks.base.back_init_menu import back_init_menu
from tasks.mirror.theme_pack_images import catalog, recognize_pack
from utils.path_manager import path_manager


def get_theme_pack_difficulty():
    if auto.find_element("mirror/theme_pack/normal_assets.png", take_screenshot=True):
        return "normal"
    if auto.find_element("mirror/theme_pack/hard_assets.png"):
        return "hard"


def switch_theme_pack_difficulty(hard_mode=False):
    target = "hard" if hard_mode else "normal"
    current = get_theme_pack_difficulty()
    if current is None:
        log.info("无法确认当前镜牢主题包难度，继续选择主题包")
        return
    if current == target:
        return
    if not auto.click_element(f"mirror/theme_pack/{current}_assets.png"):
        log.info("无法确认当前镜牢主题包难度，继续选择主题包")
        return
    sleep(2)  # 等待难度切换动画结束及主题包重新加载
    switched = get_theme_pack_difficulty()
    log.info(f"镜牢主题包难度切换{'成功' if switched == target else '失败'}: {current} -> {switched}")


@begin_and_finish_time_log(task_name="选择镜牢主题包")
# 选择镜牢主题包
def select_theme_pack(
    hard_mode=False, floor=None, team_num=None, use_custom_theme_pack_weight=False, expected_pack_id=None
):
    if path_manager.current_language != "zh_cn":
        return select_english_theme_pack(hard_mode, floor, team_num, use_custom_theme_pack_weight, expected_pack_id)
    loop_count = 30
    auto.model = "clam"
    scale = cfg.set_win_size / 1080
    theme_pack_list_zh = theme_list.get_effective_theme_pack_list(
        hard_mode, "zh_cn", team_num, use_custom_theme_pack_weight
    )
    # 游戏更新后新增的主题包尚未收录时的兜底权重，取自「未知 / unknown」配置项
    unknown_weight = int(theme_pack_list_zh.get("未知", -5))
    refresh_times = 3
    if auto.find_element("mirror/road_in_mir/legend_assets.png", take_screenshot=True):
        return
    while True:
        # 自动截图
        if auto.take_screenshot() is None:
            continue

        try:
            if floor == 5 and cfg.select_event_pack:
                if all_theme_pack := auto.find_element(
                    "mirror/theme_pack/theme_pack_features.png",
                    find_type="image_with_multiple_targets",
                ):
                    all_theme_pack.sort(key=lambda pos: (pos[0], pos[1]))
                    auto.mouse_drag_down(all_theme_pack[0][0], all_theme_pack[0][1])
                    log.debug(f"选择卡包: {all_theme_pack[0]}")
                    sleep(3)
                    msg = "此次主题包选择了最左边的（活动）卡包"
                    log.info(msg)
                    return
            weight_list = []
            pack_name = []
            if all_theme_pack := auto.find_element(
                "mirror/theme_pack/theme_pack_features.png",
                find_type="image_with_multiple_targets",
                take_screenshot=True,
            ):
                if floor == 5 and cfg.skip_event_pack:
                    all_theme_pack.sort(key=lambda pos: (pos[0], pos[1]))
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
                    crop = (top_left[0], top_left[1], bottom_right[0], bottom_right[1])
                    result = auto.find_language_text(theme_pack_list_zh, {}, crop)
                    if isinstance(result, TextMatchResult):
                        theme_pack_weight = result.value
                        theme_pack_name = result.text
                    else:
                        theme_pack_weight = unknown_weight
                        theme_pack_name = "unknown"

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
                    sleep(3)
                    msg = f"此次选择卡包关键词：{pack_name[max_index]}"
                    log.info(msg)
                    return

        except Exception as e:
            log.error(f"识别主题包出错:{e}")
            continue

        if refresh_times >= 0 and auto.click_element("mirror/theme_pack/refresh_assets.png"):
            refresh_times -= 1
            auto.mouse_to_blank()
            sleep(1)
            continue
        if refresh_times >= 0 and loop_count < 15:
            auto.mouse_to_blank(move_back=False)

        # 如果多次刷新仍无达到优选阈值的主题包，则选择权重最大的主题包
        if refresh_times <= 0:
            try:
                max_weight = max(weight_list)
                max_index = weight_list.index(max_weight)
                pack = all_theme_pack[max_index]
                auto.mouse_drag_down(pack[0], pack[1])
                log.debug(f"选择卡包: {pack}")
                sleep(3)
                log.debug("无匹配最低阈值的主题包，选择最高权重主题包")
                msg = f"无匹配最低阈值的主题包，选择最高权重主题包\n此次选择卡包关键词：{pack_name[max_index]}"
                log.info(msg)
                return
            except Exception as e:
                log.error(f"选择主题包出错:{e},尝试回到初始界面")
                back_init_menu()
                break

        loop_count -= 1
        if loop_count < 20:
            auto.model = "normal"
        if loop_count < 10:
            auto.model = "aggressive"
        if loop_count < 0:
            log.error("无法选取主题包,尝试回到初始界面")
            back_init_menu()
            break
    log.error("无法选取主题包,尝试回到初始界面")
    back_init_menu()


def read_english_theme_packs():
    """沿用选包页的图标定位，卡包身份由封面和英文全名模板确定。"""
    image = auto.take_screenshot()
    if image is None:
        return []
    scale = image.height / 1080
    positions = auto.find_element(
        "mirror/theme_pack/theme_pack_features.png", find_type="image_with_multiple_targets"
    ) or []
    choices = []
    for x, y in sorted(positions):
        box = (
            max(0, round(x - 240 * scale)), max(0, round(y - 70 * scale)),
            min(image.width, round(x + 70 * scale)), min(image.height, round(y + 460 * scale)),
        )
        match = recognize_pack(image.crop(box), round(267 * scale))
        choices.append((match, (x, y), box))
    return choices


def select_english_theme_pack(
    hard_mode=False, floor=None, team_num=None, use_custom_theme_pack_weight=False, expected_pack_id=None
):
    if get_theme_pack_difficulty() != ("hard" if hard_mode else "normal"):
        log.error("英文选包难度未通过图片核验，停止选择")
        return False
    theme_pack_list_en = theme_list.get_effective_theme_pack_list(
        hard_mode, "en", team_num, use_custom_theme_pack_weight
    )
    packs = catalog()["packs"]
    for refresh in range(4):
        choices = read_english_theme_packs()
        if not choices:
            log.error("未找到英文选包页的卡包位置")
            return False
        ranked = []
        for index, (match, position, box) in enumerate(choices):
            if match is None:
                log.warning(f"卡包图片匹配未通过，拒绝选择位置 {position}")
                continue
            if expected_pack_id and match.pack_id != expected_pack_id:
                continue
            if not expected_pack_id and floor == 5:
                if cfg.select_event_pack and index != 0:
                    continue
                if not cfg.select_event_pack and cfg.skip_event_pack and index == 0:
                    continue
            weight = int(theme_pack_list_en.get(packs[match.pack_id]["weight_key"], theme_pack_list_en.get("unknown", -5)))
            ranked.append((weight, -index, match, position, box))
        log.debug(f"英文卡包图片匹配: {[(m.pack_id if m else 'unknown', round(m.score, 3) if m else None) for m, _, _ in choices]}")
        if ranked:
            weight, _, selected, position, box = max(ranked, key=lambda item: item[:2])
            if (
                expected_pack_id or (floor == 5 and cfg.select_event_pack)
                or weight >= int(theme_list.preferred_thresholds) or refresh == 3
            ):
                image = auto.take_screenshot()
                verified = recognize_pack(image.crop(box), round(267 * image.height / 1080)) if image else None
                if verified is None or verified.pack_id != selected.pack_id:
                    log.error("选择前卡包图片复核失败，停止选择")
                    return False
                auto.mouse_drag_down(*position)
                sleep(3)
                for _ in range(10):
                    if auto.find_element("mirror/road_in_mir/legend_assets.png", take_screenshot=True):
                        log.info(f"英文卡包图片匹配选择成功: {selected.pack_id} {packs[selected.pack_id]['en']}, score={verified.score:.3f}")
                        return True
                    sleep(1)
                log.error("选包后未到达镜牢地图，停止并保留当前界面")
                return False
        if expected_pack_id:
            log.error(f"观测后的指定卡包 {expected_pack_id} 未通过选包图片复核")
            return False
        if refresh < 3 and auto.click_element("mirror/theme_pack/refresh_assets.png"):
            sleep(1)
        else:
            break
    log.error("没有通过图片匹配的英文卡包，停止选择")
    return False
