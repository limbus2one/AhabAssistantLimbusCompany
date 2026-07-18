from time import sleep

from module.automation import TextMatchResult, auto
from module.config import cfg, theme_list
from module.decorator.decorator import begin_and_finish_time_log
from module.logger import log
from tasks.base.back_init_menu import back_init_menu
from utils.image_utils import ImageUtils
from utils.path_manager import path_manager


def identify_theme_pack(pack, scale, theme_pack_list_zh, theme_pack_list_en):
    """识别单张卡包的匹配名称和配置权重。

    Args:
        pack: 卡包特征点的屏幕坐标，由多目标模板匹配得到。
        scale: 当前窗口相对 1080 高度的缩放比例。
        theme_pack_list_zh: 中文卡包关键词到权重的映射。
        theme_pack_list_en: 英文卡包关键词到权重的映射。

    Returns:
        `(weight, name)`。匹配成功时，`name` 是实际命中的卡包关键词；匹配失败
        时返回 `(-5, "unknown")`，使未知卡包保持最低选择优先级。
    """
    # 特征点位于卡面内部；向左上、右下扩展为包含卡包标题的 OCR 区域。
    top_left = (
        max(pack[0] - 210 * scale, 0),
        max(pack[1] - 60 * scale, 0),
    )
    bottom_right = (
        min(pack[0] + 60 * scale, cfg.set_win_size * 16 / 9),
        min(pack[1] + 390 * scale, cfg.set_win_size),
    )
    # 裁剪坐标已经限制在 16:9 客户区内，可直接交给多语言文字匹配。
    crop = (top_left[0], top_left[1], bottom_right[0], bottom_right[1])
    # find_language_text() 会在给定中英文关键词字典内匹配，并携带对应权重返回。
    result = auto.find_language_text(theme_pack_list_zh, theme_pack_list_en, crop)
    if isinstance(result, TextMatchResult):
        # text 是实际命中的关键词，后续既用于日志，也作为 MirrorMap 的卡包名称。
        return result.value, result.text
    # 未知卡包保留在候选列表中，但给予低权重，避免识别失败直接打断选卡流程。
    return -5, "unknown"


@begin_and_finish_time_log(task_name="选择镜牢主题包")
# 选择镜牢主题包
def select_theme_pack(hard_switch=False, floor=None, team_num=None, use_custom_theme_pack_weight=False):
    """按配置权重选择楼层卡包，并返回最终选择的卡包名称。

    Args:
        hard_switch: 是否选择困难模式卡包。
        floor: 进入选卡页面前记录的楼层编号，用于第五层活动卡包规则。
        team_num: AALC 编队配置方案序号，仅用于读取该方案的自定义卡包权重；
            它不是写入 `MirrorMap`/`Node` 的游戏内 `team_number`。
        use_custom_theme_pack_weight: 是否叠加当前配置方案的自定义卡包权重。

    Returns:
        str: 实际选中的卡包名称或命中的卡包关键词。活动卡包 OCR 失败时返回
        `"活动卡包"` 作为明确的兜底名称；未完成选择时返回 `None`。
    """
    # loop_count 限制整个页面识别次数；refresh_times 单独限制主动刷新卡包的次数。
    loop_count = 30
    # 初期使用卡包模板原始位置附近的小范围匹配，速度快且误匹配少。
    auto.model = "clam"
    # 卡包页面坐标最初按 1080 高度标定，与寻路地图的 1440 基准不同。
    scale = cfg.set_win_size / 1080
    # 只加载当前语言的关键词可减少 OCR 匹配量；语言未确认时同时加载中英文兜底。
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
    refresh_times = 3
    # difficulty 记录当前页面实际显示的难度；None 表示模板和 OCR 都尚未确认。
    difficulty = None
    # legend 标志表示已经不在普通选卡页面，无需继续选择，也没有新卡包名可返回。
    if auto.find_element("mirror/road_in_mir/legend_assets.png", take_screenshot=True):
        return
    while True:
        # 每轮所有模板/OCR 判断共享同一张截图，截图失败则不消耗循环次数。
        if auto.take_screenshot() is None:
            continue

        if (
            difficulty is None
            and auto.find_element("mirror/theme_pack/normal_assets.png") is None
            and auto.find_element("mirror/theme_pack/hard_assets.png") is None
        ):
            # 普通/困难按钮都未匹配到，通常是页面仍在动画或当前模板主题不匹配。
            if loop_count < 0:
                break
            if loop_count < 5:
                # 临近超时时改用 OCR 读取两个按钮覆盖区域，避免完全依赖按钮模板。
                normal_bbox = ImageUtils.get_bbox(ImageUtils.load_image("mirror/theme_pack/normal_assets.png"))
                hard_bbox = ImageUtils.get_bbox(ImageUtils.load_image("mirror/theme_pack/hard_assets.png"))
                # 合并两个模板 bbox，得到同时覆盖 normal/hard 文本的最小矩形。
                difficulty_bbox = [
                    min(normal_bbox[0], hard_bbox[0]),
                    min(normal_bbox[1], hard_bbox[1]),
                    max(normal_bbox[2], hard_bbox[2]),
                    max(normal_bbox[3], hard_bbox[3]),
                ]
                ocr_result = auto.find_text_element(None, my_crop=difficulty_bbox, only_text=True)
                if not isinstance(ocr_result, str):
                    # OCR 也失败时检查是否已经离开选卡页；否则保留次数继续等待动画。
                    if auto.find_element("mirror/road_in_mir/legend_assets.png", take_screenshot=True):
                        return
                    continue
                if "normal" in ocr_result:
                    difficulty = "normal"
                elif "hard" in ocr_result:
                    difficulty = "hard"
            # 页面尚未稳定时每秒重试一次，防止无 sleep 的高频空转。
            loop_count -= 1
            sleep(1)
            continue

        # 将页面难度切换到配置目标。能直接匹配“另一难度”按钮时优先点击模板。
        if hard_switch:
            if auto.click_element("mirror/theme_pack/normal_assets.png"):
                # 点击后页面会刷新卡包，下一轮重新截图再识别候选。
                continue
            elif difficulty == "normal":
                # OCR 已确认当前为 normal，但按钮模板没匹配到时按模板 bbox 中心点击。
                normal_bbox = ImageUtils.get_bbox(ImageUtils.load_image("mirror/theme_pack/normal_assets.png"))
                auto.mouse_click(
                    (normal_bbox[0] + normal_bbox[2]) // 2,
                    (normal_bbox[1] + normal_bbox[3]) // 2,
                )
        else:
            if auto.click_element("mirror/theme_pack/hard_assets.png"):
                continue
            elif difficulty == "hard":
                # 与上面的困难切换相同，这是模板点击失败时的固定坐标兜底。
                hard_bbox = ImageUtils.get_bbox(ImageUtils.load_image("mirror/theme_pack/hard_assets.png"))
                auto.mouse_click(
                    (hard_bbox[0] + hard_bbox[2]) // 2,
                    (hard_bbox[1] + hard_bbox[3]) // 2,
                )

        try:
            # floor==4 代表即将选择第五层卡包；配置开启时固定选择最左活动卡包。
            if floor == 4 and cfg.select_event_pack:
                if all_theme_pack := auto.find_element(
                    "mirror/theme_pack/theme_pack_features.png",
                    find_type="image_with_multiple_targets",
                ):
                    # 先按 X、再按 Y 排序，确保不同分辨率下“最左卡包”选择稳定。
                    all_theme_pack.sort(key=lambda pos: (pos[0], pos[1]))
                    pack = all_theme_pack[0]
                    # 活动规则固定选择最左卡包，但仍先 OCR 名称，使 MirrorMap 和
                    # Node 尽量保存真实卡包名，而不是只记录“活动卡包”类别。
                    _, theme_pack_name = identify_theme_pack(
                        pack,
                        scale,
                        theme_pack_list_zh,
                        theme_pack_list_en,
                    )
                    if theme_pack_name == "unknown":
                        # 固定活动卡包无法识别时仍返回稳定的非空名称，避免沿用上一层卡包。
                        theme_pack_name = "活动卡包"
                    # mouse_drag_down() 模拟游戏要求的向下拖卡动作，而不是普通单击。
                    auto.mouse_drag_down(pack[0], pack[1])
                    log.debug(f"选择卡包: {pack}")
                    sleep(3)
                    msg = f"此次主题包选择了最左边的活动卡包：{theme_pack_name}"
                    log.info(msg)
                    return theme_pack_name
            # 两个并行列表与 all_theme_pack 使用相同索引，便于从最大权重反查坐标和名称。
            weight_list = []
            pack_name = []
            if all_theme_pack := auto.find_element(
                "mirror/theme_pack/theme_pack_features.png",
                find_type="image_with_multiple_targets",
                take_screenshot=True,
            ):
                if floor == 4 and cfg.skip_event_pack:
                    # 跳过活动卡包时，删除按 X 排序后的最左候选，再参与正常权重比较。
                    all_theme_pack.sort(key=lambda pos: (pos[0], pos[1]))
                    all_theme_pack.pop(0)  # 删除最左边的卡包
                for pack in all_theme_pack:
                    # 识别结果同时用于卡包选择权重和选择成功后的上下文名称。
                    theme_pack_weight, theme_pack_name = identify_theme_pack(
                        pack,
                        scale,
                        theme_pack_list_zh,
                        theme_pack_list_en,
                    )

                    weight_list.append(theme_pack_weight)  # 采用最大值的形式，权重越大，优先级越高
                    pack_name.append(theme_pack_name)

                # 选择权重最大的主题包；相同权重时 list.index() 保留界面中的第一个。
                max_weight = max(weight_list)
                log.debug(f"当前主题包权重列表：{list(zip(pack_name, weight_list))}")
                # 如果存在权重最大值大于等于优选阈值的主题包，则选择该主题包
                if max_weight >= int(theme_list.preferred_thresholds):
                    # 达到优选阈值即可立即选择，不再浪费刷新次数寻找更高权重卡包。
                    max_index = weight_list.index(max_weight)
                    pack = all_theme_pack[max_index]
                    auto.mouse_drag_down(pack[0], pack[1])
                    log.debug(f"选择卡包: {pack}")
                    sleep(3)
                    msg = f"此次选择卡包关键词：{pack_name[max_index]}"
                    log.info(msg)
                    # 返回值由 Mirror.road_to_mir() 写入 MirrorMap.theme_pack_name。
                    return pack_name[max_index]

        except Exception as e:
            # 单轮 OCR/模板异常不终止任务，保留外层循环让页面或截图恢复后继续。
            log.error(f"识别主题包出错:{e}")
            continue

        if refresh_times >= 0 and auto.click_element("mirror/theme_pack/refresh_assets.png"):
            # 当前候选均未达到阈值时刷新；点击成功才扣减刷新次数。
            refresh_times -= 1
            # 鼠标移开按钮，避免悬停高亮改变下一轮模板或 OCR 外观。
            auto.mouse_to_blank()
            sleep(1)
            continue
        if refresh_times >= 0 and loop_count < 15:
            # 刷新按钮暂时没匹配到时也移开鼠标，但不强制移动回原位置。
            auto.mouse_to_blank(move_back=False)

        # 如果多次刷新仍无达到优选阈值的主题包，则选择权重最大的主题包
        if refresh_times <= 0:
            try:
                # 刷新预算耗尽后不再要求达到阈值，直接使用最后一批候选中的最高权重。
                max_weight = max(weight_list)
                max_index = weight_list.index(max_weight)
                pack = all_theme_pack[max_index]
                auto.mouse_drag_down(pack[0], pack[1])
                log.debug(f"选择卡包: {pack}")
                sleep(3)
                log.debug("无匹配最低阈值的主题包，选择最高权重主题包")
                msg = f"无匹配最低阈值的主题包，选择最高权重主题包\n此次选择卡包关键词：{pack_name[max_index]}"
                log.info(msg)
                # 与优选分支相同，返回名称供 MirrorMap 更新本楼层上下文。
                return pack_name[max_index]
            except Exception as e:
                # 连候选列表都不可用时无法安全选择，回到主界面交由上层恢复。
                log.error(f"选择主题包出错:{e},尝试回到初始界面")
                back_init_menu()
                break

        # 未选择、未成功刷新时消耗一次总循环预算，并逐步扩大模板搜索范围。
        loop_count -= 1
        if loop_count < 20:
            # normal 在模板原 bbox 周围扩大搜索，兼顾速度和页面轻微位移。
            auto.model = "normal"
        if loop_count < 10:
            # aggressive 搜索整个客户区，只在临近超时时启用以控制性能开销。
            auto.model = "aggressive"
        if loop_count < 0:
            log.error("无法选取主题包,尝试回到初始界面")
            back_init_menu()
            break
    log.error("无法选取主题包,尝试回到初始界面")
    back_init_menu()
