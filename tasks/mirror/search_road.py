import heapq
from enum import Enum
from functools import lru_cache
from time import sleep

import cv2

from module.automation import auto
from module.config import cfg
from module.logger import log


class Position(Enum):
    # 数值同时表示 Bus 的逻辑行：上、中、下分别为 1、0、-1。
    UP = 1
    MID = 0
    DOWN = -1


# 1440 高度基准下，相邻列和相邻行的节点中心距离；实际使用时按窗口高度缩放。
X_GAP = 520
Y_GAP = 437
# ONNX 画面通常只能完整覆盖四列，普通镜牢缺失的末尾固定节点由代码补齐。
VISIBLE_COLUMN_COUNT = 4
# 连线模板只在两节点中点附近搜索，缩小范围可降低误匹配和模板匹配耗时。
CONNECTION_X_RADIUS = 150
CONNECTION_Y_RADIUS = 120
# 为三个候选画面统一 Bus 的 X 基准和中间行 Y 基准。
BUS_TARGET_X = 120
BUS_TARGET_Y = 700
# 探测顺序固定为上、中、下；并列选择顺序单独由 BUS_TIE_PRIORITY 决定。
BUS_PROBE_ORDER = (Position.UP, Position.MID, Position.DOWN)
BUS_TIE_PRIORITY = {
    Position.MID: 0,
    Position.UP: 1,
    Position.DOWN: 2,
}
VALID_BUS_ROWS = {-1, 0, 1}


# Dijkstra 会累加“进入节点”的代价，因此数值越小越优先选择。
NODE_WEIGHT = {
    "battle": 30,
    "boss_battle": 999,
    "event": 18,
    "hard_battle": 75,
    "hard_battle_2": 100,
    "shop": 1,
    "small_boss_battle": 999,
    "bus": 0,
}


class Node:
    """镜牢地图中的一个节点及其运行上下文快照。

    `coord` 和 `screen_pos` 分别用于逻辑寻路与实际点击；`type` 和 `value`
    用于计算路线权重。`team_number`、`floor`、`theme_pack_name` 是构图当时
    从 `MirrorMap` 复制来的上下文。它们存放在每个节点中，是为了让节点在
    离开当前楼层或 `MirrorMap` 更新后，仍能明确属于哪次编队、楼层和卡包。

    Args:
        coord: 三行地图中的逻辑网格坐标，格式为 `(行, 列)`。
        node_type: ONNX 识别出的节点类型，例如 `event`、`battle` 或 `shop`。
        screen_pos: 节点中心在当前游戏客户区中的像素坐标。
        value: 自定义寻路权重；为 `None` 时从 `NODE_WEIGHT` 读取默认值。
        synthetic: 是否为代码补出的虚拟节点，而不是 ONNX 直接识别的节点。
        team_number: 游戏内编队编号，即 `TeamSetting.team_number`。
        floor: 构建该节点时所在的镜牢楼层。
        theme_pack_name: 构建该节点时当前楼层选择的卡包名称。
    """

    def __init__(
        self,
        coord,
        node_type,
        screen_pos,
        value=None,
        synthetic=False,
        team_number=None,
        floor=None,
        theme_pack_name=None,
    ):
        # 寻路坐标与屏幕坐标用途不同：前者参与连边和方向计算，后者用于点击。
        self.coord = coord
        self.type = node_type
        self.screen_pos = screen_pos
        self.value = NODE_WEIGHT.get(node_type, 999) if value is None else value
        self.next = []
        self.synthetic = synthetic

        # 保存构图时的运行上下文。这里按值复制，不引用 MirrorMap，避免后续换层
        # 或换卡包时，已经生成的历史节点被新的运行状态覆盖。
        self.team_number = team_number
        self.floor = floor
        self.theme_pack_name = theme_pack_name

    def add_next(self, next_node):
        # 同一条连线可能被重复检查；去重可以避免 Dijkstra 重复扩展相同后继。
        if next_node not in self.next:
            self.next.append(next_node)

    def __repr__(self):
        # 只输出后继坐标，不递归输出 Node 对象，防止调试日志形成巨大嵌套结构。
        next_coords = [node.coord for node in self.next]
        return (
            f"Node(coord={self.coord}, type={self.type!r}, value={self.value}, "
            f"team_number={self.team_number}, floor={self.floor}, "
            f"theme_pack_name={self.theme_pack_name!r}, next={next_coords})"
        )


def get_node_direction(current_node, next_node):
    """根据两个相邻节点的行差返回 U/M/D。"""
    # 逻辑坐标为 (行, 列)，行号向上增大。
    row_delta = next_node.coord[0] - current_node.coord[0]
    # 相邻列正常只会出现 -1/0/+1；其他差值说明节点吸附或连边结果无效。
    return {1: "U", 0: "M", -1: "D"}.get(row_delta)


def _require_bus_row(bus_row):
    """返回合法 Bus 行；拒绝 None、布尔值和三行之外的输入。"""
    if type(bus_row) is not int or bus_row not in VALID_BUS_ROWS:
        raise ValueError(f"bus_row 必须是 -1、0、1，实际为 {bus_row!r}")
    return bus_row


def _wait_page_load(targets, model=None):
    """兼容当前分支自动化层：持续截图，直到任一目标出现。"""
    while True:
        # take_screenshot() 自身遵守配置中的截图间隔；失败时直接进入下一轮重试。
        if auto.take_screenshot() is None:
            continue
        # 多个目标共享同一张截图，避免为每个页面标志分别截图。
        for target in targets:
            if auto.find_element(target, model=model):
                # 返回命中的资源名，让调用者区分“确认按钮”和“事件入口”等页面。
                return target


class MirrorMap:
    """管理当前镜牢运行上下文、ONNX 节点图和最优路线缓存。

    `MirrorMap` 保存的是“当前状态”，而 `Node` 保存的是“构图时的快照”。
    当楼层或卡包变化时，本对象更新对应字段并清空旧路线；下次构图时，再把
    当前 `team_number`、`floor`、`theme_pack_name` 复制到所有新节点。

    Args:
        floor: 当前镜牢楼层；创建 `Mirror` 时为 0，识别楼层后更新为 1—5。
        hard_mode: 是否为困难镜牢，决定是否需要补齐固定商店和 BOSS 节点。
        team_number: 游戏内实际选择的编队编号，不是 AALC 配置方案序号。
        theme_pack_name: 当前楼层已选择的卡包名称；尚未选择时为 `None`。
        bus_row: Bus 当前逻辑行；初始化时为 `None`，识别后为 1、0、-1。
    """

    def __init__(
        self,
        floor=1,
        hard_mode=False,
        team_number=None,
        theme_pack_name=None,
        bus_row=None,
    ):
        # 运行上下文：构图时会原样传给 search_road_from_road_map()。
        self.floor = floor
        self.hard_mode = hard_mode
        self.team_number = team_number
        self.theme_pack_name = theme_pack_name
        self.bus_row = None if bus_row is None else _require_bus_row(bus_row)
        log.info(
            "镜牢寻路状态初始化: "
            f"hard_mode={self.hard_mode}, bus_row={self.bus_row}"
        )

        # floor_route 是按顺序执行的最优节点路线；floor_map 是坐标到节点的完整映射。
        self.floor_route = []
        self.floor_map = {}

    def get_next_node_direction(self):
        """返回最优路线中下一个节点的 U/M/D 方向。

        路线至少需要包含“当前位置 + 下一节点”两个节点。缓存不足时重新识别整张
        地图；ONNX 构图失败时退化为只匹配 Bus 到首列节点的三种连线模板。
        """
        # 困难模式每次决策都重新识别；普通模式只在缓存不足时重建。
        if self.hard_mode or len(self.floor_route) < 2:
            try:
                # 两种模式都使用 MirrorMap 保存的 Bus 行对齐识别画面；已知行会先
                # 把 Bus 拖到对应标准位置，hard mode 每步重建，普通模式复用整层路线。
                known_bus_row = self.bus_row
                if self.hard_mode:
                    log.info(
                        "困难镜牢逐步重识别: "
                        f"bus_row={known_bus_row}, "
                        f"缓存节点={len(self.floor_map)}, "
                        f"缓存路线={len(self.floor_route)}"
                    )
                floor_route, floor_map, bus_row = search_road_from_road_map(
                    hard_mode=self.hard_mode,
                    team_number=self.team_number,
                    floor=self.floor,
                    theme_pack_name=self.theme_pack_name,
                    bus_row=known_bus_row,
                )
            except Exception as error:
                # 模型加载、截图或构图任一阶段异常都不能继续使用可能残缺的缓存。
                log.warning(f"镜牢 ONNX 寻路出错: {error}")
                self._clear_floor_data()
                return self._find_first_node_direction()

            if bus_row in {-1, 0, 1}:
                self.bus_row = bus_row

            if len(floor_route) < 2 or not floor_map:
                # 有效路线必须至少含 Bus 和下一节点；否则只能退化为首列连线识别。
                self._clear_floor_data()
                log.warning("镜牢 ONNX 未识别到有效路线，直接识别首个方向")
                return self._find_first_node_direction()
            # 新识别结果同时整体覆盖地图和路线；困难模式不合并历史局部图。
            self.floor_route = list(floor_route)
            self.floor_map = dict(floor_map)
            if self.hard_mode:
                log.info(
                    "困难镜牢地图已更新: "
                    f"bus_row={self.bus_row}, "
                    f"节点={len(self.floor_map)}, "
                    f"路线={len(self.floor_route)}"
                )
        else:
            log.info(
                "普通镜牢复用路线缓存: "
                f"bus_row={self.bus_row}, "
                f"节点={len(self.floor_map)}, "
                f"剩余路线={len(self.floor_route)}"
            )

        # floor_route[0] 始终代表当前 Bus/已到达节点，[1] 才是本次要进入的节点。
        next_node_direction = get_node_direction(
            self.floor_route[0],
            self.floor_route[1],
        )
        if next_node_direction is None:
            # 无效方向不能继续点击；丢弃整条缓存后只尝试识别当前首列连线。
            log.warning("缓存路线方向无效，直接识别首个方向")
            self._clear_floor_data()
            return self._find_first_node_direction()
        return next_node_direction

    def enter_next_node(self, next_node_direction):
        """按 U/M/D 选择、确认并进入下一节点，成功后才消费路线缓存。

        键盘模式发送方向键；鼠标模式根据 Bus 坐标和固定网格间距计算目标位置。
        点击后等待“进入按钮”或“事件入口”，再等待战斗编队页或事件页完成加载。
        """
        # 在操作画面前保存本次计划进入的节点。后续点击会改变页面状态，但 Bus 行
        # 必须按构图时选中的目标更新；降级路线没有节点缓存时这里允许为 None。
        next_node = self._get_next_node()

        # 第一步只负责在地图上选择目标节点，让 Bus 沿连线移动过去。
        if cfg.mirror_keyboard_navigation:
            # 游戏键位中，中路通过向右键选择，而不是不存在的“middle”键。
            key = {"U": "up", "M": "right", "D": "down"}[next_node_direction]
            auto.key_press(key)
        else:
            # 鼠标模式先重新定位 Bus，避免使用构图时可能已经过期的屏幕坐标。
            next_node_position = self._get_next_node_position(next_node_direction)
            auto.mouse_action_with_pos(next_node_position)

        # 等待目标节点的进入页面；事件节点会直接进入事件页，不显示 enter 按钮。
        sleep(1.25)
        entered_next_node = auto.click_element(
            "mirror/road_in_mir/enter_assets.png",
            take_screenshot=True,
        )
        if not entered_next_node:
            entered_next_node = bool(
                auto.find_element(
                    "mirror/road_in_mir/event_in_assets.png",
                    take_screenshot=True,
                )
            )

        if not entered_next_node:
            # 如果没有enter界面，说明当前bus所在节点还没有完成，进入当前节点
            auto.click_element("mirror/mybus_default_distance.png")
            sleep(1)
            auto.click_element("mirror/road_in_mir/enter_assets.png", take_screenshot=True)
            return True
        # 页面确实进入后才提交 Bus 行与缓存变化，避免失败重试从错误位置开始。
        # 困难模式也保留这张图到下一次识别成功，再由 get_next_node_direction()
        # 整体替换；它不会用于下一步决策，但可保证缓存状态连续且便于排错。
        self._update_bus_row(next_node, next_node_direction)
        self._consume_route_node()
        return True

    def _get_next_node(self):
        """返回路线中的下一个节点；无路线缓存时返回 None。"""
        # 索引 0 是当前位置，所以路线少于两个元素时没有可进入的下一节点。
        if len(self.floor_route) < 2:
            return None
        return self.floor_route[1]

    def refresh_floor(self, floor):
        """只更新当前楼层；保留现有节点图、路线和 Bus 行。"""
        if self.floor == floor:
            return
        log.debug(f"镜牢地图楼层缓存更新: {self.floor} -> {floor}")
        self.floor = floor

    def refresh_theme_pack(self, theme_pack_name):
        """更新卡包名称，并清空属于上一卡包的节点图和路线。

        `None` 或空字符串表示选卡函数没有取得有效结果，此时保留原名称，避免
        因一次临时识别失败而丢失已经确认的卡包上下文。
        """
        # 空名称代表选卡失败；相同名称代表上下文未变化，两种情况都无需重建缓存。
        if not theme_pack_name or self.theme_pack_name == theme_pack_name:
            return
        log.debug(f"镜牢卡包缓存更新: {self.theme_pack_name} -> {theme_pack_name}")
        self.theme_pack_name = theme_pack_name
        self._clear_floor_data()

    def _get_next_node_position(self, next_node_direction):
        """返回 bus 右侧节点的屏幕坐标。"""
        scale = cfg.set_win_size / 1440
        # 下一节点总在右侧一列；U/M/D 只影响相对 Bus 的纵向偏移。
        offsets = {
            "M": (X_GAP * scale, 0),
            "D": (X_GAP * scale, Y_GAP * scale),
            "U": (X_GAP * scale, -Y_GAP * scale),
        }
        # 地图动画期间模板可能短暂消失，最多重新截图定位三次。
        for _ in range(3):
            bus_position = auto.find_element(
                "mirror/mybus_default_distance.png",
                take_screenshot=True,
            )
            if bus_position:
                dx, dy = offsets[next_node_direction]
                # 返回绝对屏幕坐标，交给 automation 的统一鼠标操作接口。
                return bus_position[0] + dx, bus_position[1] + dy
        return None

    def _consume_route_node(self):
        # 移除旧“当前位置”，使原下一节点在下一次调用时成为新的索引 0。
        if self.floor_route:
            self.floor_route.pop(0)

    def _update_bus_row(self, next_node, next_node_direction):
        """成功进入节点后，把 Bus 行更新为实际目标行。"""
        if next_node is not None:
            self.bus_row = next_node.coord[0]
            return
        if self.bus_row not in {-1, 0, 1}:
            return
        row_delta = {"U": 1, "M": 0, "D": -1}.get(next_node_direction)
        if row_delta is not None:
            self.bus_row = max(-1, min(1, self.bus_row + row_delta))

    def _find_first_node_direction(self):
        # 退化策略不构建全图，只需要当前 Bus 坐标和三张首列连线模板。
        bus_position = auto.find_element(
            "mirror/mybus_default_distance.png",
            take_screenshot=True,
        )
        if not bus_position:
            return False
        return find_first_direction(bus_position)

    def _clear_floor_data(self):
        # 节点图和路线必须同时清空，避免路线与另一张地图的 Node 混用。
        self.floor_route = []
        self.floor_map = {}


def search_road_from_road_map(
    hard_mode=False,
    team_number=None,
    floor=None,
    theme_pack_name=None,
    bus_row=None,
):
    """识别当前地图、构建带运行上下文的节点图，并计算最低权重路线。

    Args:
        hard_mode: 是否使用困难镜牢构图规则。
        team_number: 游戏内编队编号，写入本次生成的所有节点。
        floor: 当前镜牢楼层，写入本次生成的所有节点。
        theme_pack_name: 当前卡包名称，写入本次生成的所有节点。
        bus_row: 已知的 Bus 逻辑行。为 `None` 时执行三位置探测；否则先移动到该行再识别。

    Returns:
        `(floor_route, floor_map, bus_row)`：前两项分别为从 Bus 开始的最优节点列表
        和完整节点图，最后一项为本次确认的 Bus 行。
    """

    # 先选择 ONNX 可见节点最多的标准画面；返回的 points 与该画面的坐标系一致。
    bus_result = find_bus(bus_row=bus_row)
    if not bus_result:
        # Bus 无法定位或三个候选画面都无节点时，不能建立逻辑原点。
        return [], {}, bus_row

    # 把本次确认的 Bus 行传入构图，使所有节点都使用同一套绝对三行坐标。
    bus_position, detected_bus_row, points = bus_result
    if bus_position is None or not points:
        return [], {}, detected_bus_row

    # 将像素检测结果吸附为逻辑网格，并用模板确认相邻节点间是否真的存在连线。
    floor_map = generate_map(
        points,
        bus_position,
        bus_row=detected_bus_row,
        hard_mode=hard_mode,
        team_number=team_number,
        floor=floor,
        theme_pack_name=theme_pack_name,
    )
    # 节点权重越低越优先；Dijkstra 返回累计代价最低的完整可达路线。
    min_weight, floor_route = find_min_weight_route(floor_map)
    if not floor_route:
        log.warning("ONNX 未能构建可达路线")
        return [], {}, detected_bus_row

    # 日志同时输出操作方向和节点类型，方便对照实际点击结果排查识别错误。
    directions, node_types = route_to_directions(floor_route)

    log.info(f"镜牢 ONNX 路线: 权重={min_weight}, 方向={directions}, 节点={node_types}")
    return floor_route, floor_map, detected_bus_row


def find_bus(bus_row=None, take_screenshot=True):
    """定位 Bus 并识别节点；Bus 行未知时才执行三位置探测。

    `bus_row` 为 `None` 时，依次拖到 UP/MID/DOWN 并选择节点最多的画面；传入
    1、0、-1 时先把 Bus 拖到对应标准位置，再识别对齐后的当前画面。

    Returns:
        `(bus_position, bus_row, points)`。Bus 丢失或未识别到节点时返回 `None`。
    """
    if bus_row is not None:
        bus_row = _require_bus_row(bus_row)

    # 初次定位允许由调用者决定是否截图；之后每次移动都会强制取得新截图。
    bus_position = auto.find_element(
        "mirror/mybus_default_distance.png",
        take_screenshot=take_screenshot,
    )
    if bus_position is None:
        return None

    if bus_row is not None:
        # 已知逻辑行仍需先把地图对齐到标准位置，确保节点吸附和连线模板使用的
        # 屏幕坐标系与该逻辑行一致。
        moved_bus_position = move_bus(bus_position, Position(bus_row))
        if moved_bus_position is None:
            log.warning(f"bus 无法移动到已知逻辑行 {bus_row} 位置")
            return None
        onnx_result = onnx(moved_bus_position)
        points = onnx_result[1] if onnx_result else []
        if not points:
            log.warning(f"bus 已知逻辑行 {bus_row} 未识别到节点")
            return None
        log.info(f"bus 移动到已知逻辑行 {bus_row}，识别到 {len(points)} 个节点")
        return moved_bus_position, bus_row, points

    # candidates 保存每个成功探测位置的“标准行、Bus 坐标、节点列表”。
    candidates = []
    current_bus_position = bus_position
    for probe_row in BUS_PROBE_ORDER:
        # 每次都从上一次实际匹配到的 Bus 坐标开始拖动，避免累计理论坐标误差。
        moved_bus_position = move_bus(current_bus_position, probe_row)
        if moved_bus_position is None:
            log.warning(f"bus 无法移动到 {probe_row.value} 位置")
            # 拖动可能成功但动画导致首次复查失败；再单独截图尝试恢复当前位置。
            current_bus_position = auto.find_element(
                "mirror/mybus_default_distance.png",
                take_screenshot=True,
            )
            if current_bus_position is None:
                # Bus 已完全丢失，后续候选也没有可靠拖动起点，只能终止探测。
                break
            continue
        current_bus_position = moved_bus_position

        # onnx() 不执行拖动，只对当前候选画面截图并调用 identify_nodes()。
        onnx_result = onnx(current_bus_position)
        points = onnx_result[1] if onnx_result else []
        candidates.append((probe_row, current_bus_position, points))
        log.debug(f"bus {probe_row.value} 位置识别到 {len(points)} 个节点")

    if not candidates:
        # 三次移动全部失败，没有任何可用于构图的候选画面。
        return None

    # max() 先比较节点数量；数量相同则使用负优先级，使 MID(0) 优先于 UP/DOWN。
    best_row, best_bus_position, best_points = max(
        candidates,
        key=lambda candidate: (
            len(candidate[2]),
            -BUS_TIE_PRIORITY[candidate[0]],
        ),
    )
    if not best_points:
        # 候选存在但节点数都为 0；仅有 Bus 无法计算下一步路线。
        log.warning("bus 三个位置均未识别到节点")
        return None

    # 探测结束时画面停在最后一个候选位置；回到胜出位置，确保节点坐标与后续连线截图一致。
    if current_bus_position != best_bus_position:
        # 回位使用当前实际 Bus 坐标，而不是最初坐标，避免重复拖动造成偏移。
        best_bus_position = move_bus(current_bus_position, best_row)
        if best_bus_position is None:
            return None
        # 回位后的截图可能与首次探测存在动画差异，因此以重新识别结果为准。
        final_result = onnx(best_bus_position)
        if final_result and final_result[1]:
            best_points = final_result[1]

    log.info(f"bus 选择逻辑行 {best_row.value}，识别到 {len(best_points)} 个节点")
    return best_bus_position, best_row.value, best_points


def find_first_direction(bus_position):
    """按上、中、下顺序识别 bus 到相邻节点的第一条连线。"""
    # 退化策略仍需要一张当前截图；Bus 或截图无效时无法计算连线搜索区域。
    if not bus_position or auto.take_screenshot() is None:
        return False

    scale = cfg.set_win_size / 1440
    # 元组内容依次为：返回方向、模板名、下一节点相对 Bus 的近似像素偏移。
    direction_targets = (
        ("U", "up_arr", (500, -400)),
        ("M", "mid_arr", (500, 50)),
        ("D", "down_arr", (500, 450)),
    )
    for direction, template, (node_dx, node_dy) in direction_targets:
        # 连线模板位于 Bus 和下一节点之间，所以搜索中心取相对偏移的一半。
        connection_midpoint = (
            bus_position[0] + node_dx * scale / 2,
            bus_position[1] + node_dy * scale / 2,
        )
        # 只搜索连线中点附近的小 ROI，避免其他列的相似箭头造成误判。
        crop = _crop_around(
            connection_midpoint,
            CONNECTION_X_RADIUS * scale,
            CONNECTION_Y_RADIUS * scale,
        )
        if auto.find_element(
            f"mirror/road_in_mir/{template}.png",
            threshold=0.75,
            my_crop=crop,
            model="aggressive",
        ):
            # 按 U/M/D 顺序返回第一条匹配连线；该策略仅在全图构建失败时使用。
            return direction
    return False


def move_bus(bus_position, bus_row):
    """将 Bus 拖到指定的上、中、下标准屏幕位置。

    标准 X 为 120；标准 MID Y 为 700，UP/DOWN 分别相差一个 `Y_GAP`。
    所有基准值按窗口高度缩放。拖动后重新截图匹配 Bus，返回其实际落点，而不是
    直接假定拖动终点准确，从而吸收游戏动画或输入误差。
    """
    # 所有标准坐标以 1440 高度为基准，兼容其他窗口高度时统一缩放。
    scale = cfg.set_win_size / 1440
    bus_x, bus_y = bus_position
    # 枚举代表 Bus 最终所在行；上、下位置分别相对中线偏移一个逻辑行距。
    target_y = {
        Position.UP: BUS_TARGET_Y - Y_GAP,
        Position.MID: BUS_TARGET_Y,
        Position.DOWN: BUS_TARGET_Y + Y_GAP,
    }[bus_row]
    # mouse_drag() 接收相对位移，因此用目标绝对坐标减去当前实际坐标。
    dx = BUS_TARGET_X * scale - bus_x
    dy = target_y * scale - bus_y

    # darg_time 太短会影响节点识别（确信
    auto.mouse_drag(bus_x, bus_y, drag_time=0.5, dx=dx, dy=dy)
    # 拖动完成后立即重新截图匹配，以下一步实际位置作为返回值。
    moved_bus_position = auto.find_element(
        "mirror/mybus_default_distance.png",
        take_screenshot=True,
    )
    if moved_bus_position is None:
        return None
    return moved_bus_position


def onnx(bus_position=None):
    """直接截取当前地图画面并运行 ONNX；本函数绝不移动 Bus。

    `find_bus()` 负责画面位置枚举，本函数只负责“彩色截图 -> 确认 Bus 坐标 ->
    identify_nodes()”。传入 Bus 坐标可避免在同一画面重复模板匹配；未传入时会
    在刚取得的彩色截图上查找 Bus。
    """
    # ONNX 模型需要彩色三通道图像，不能复用连线匹配使用的灰度截图。
    if auto.take_screenshot(gray=False) is None:
        return None
    if bus_position is None:
        # 独立调用 onnx() 时，在刚取得的同一张彩色截图上补充 Bus 定位。
        bus_position = auto.find_element("mirror/mybus_default_distance.png")
        if bus_position is None:
            return None
    # 传入当前截图避免 identify_nodes() 再次截图，并用 Bus X 过滤已走过的左侧节点。
    points = identify_nodes(bus_position[0], image=auto.screenshot)
    return bus_position, points


def identify_nodes(bus_x, image=None):
    """使用 ONNX 识别 Bus 右侧的地图节点。

    图像先补成正方形并缩放到 640×640，推理结果经过置信度过滤和 NMS 去重，
    最后再换算回原截图坐标。Bus 左侧的识别框会被过滤，避免把已经走过的节点
    加入当前楼层路线。
    """
    import numpy as np

    # 模型类别索引必须与 best.onnx 训练时的类别顺序完全一致。
    classes = [
        "battle",
        "boss_battle",
        "event",
        "hard_battle",
        "hard_battle_2",
        "shop",
        "small_boss_battle",
    ]
    if image is None:
        # 允许 identify_nodes() 独立使用；正常三位置探测会由 onnx() 传入现成截图。
        if auto.take_screenshot(gray=False) is None:
            return []
        image = auto.screenshot

    # PIL 图像转换为 NumPy，保留前三个颜色通道，丢弃可能存在的 Alpha 通道。
    original = np.array(image)
    height, width = original.shape[:2]
    # 模型使用方形输入。只在右侧或下方补黑边，不拉伸原图，避免节点形状变形。
    length = max(height, width)
    square = np.zeros((length, length, 3), np.uint8)
    square[:height, :width] = original[:, :, :3]
    # 推理输出坐标基于 640 输入，乘以该比例可还原到原截图像素坐标。
    image_scale = length / 640
    # blobFromImage 同时完成归一化和 640×640 缩放；模型训练颜色顺序不需要交换。
    blob = cv2.dnn.blobFromImage(
        square,
        scalefactor=1 / 255,
        size=(640, 640),
        swapRB=False,
    )

    # 三个候选位置共用同一个缓存会话，避免每次探测重新从磁盘加载模型。
    session = _get_onnx_session()
    outputs = session.run(None, {session.get_inputs()[0].name: blob})[0]
    # 原始输出通常是 [属性, 候选框]，转置后按候选框逐个处理更直观。
    outputs = cv2.transpose(outputs[0])

    # 分别保存候选框、最高类别置信度和类别索引，供 OpenCV NMS 使用。
    boxes = []
    scores = []
    class_ids = []
    for output in outputs:
        # 前四项为中心点和宽高，output[4:] 为各类别置信度。
        _, max_score, _, (_, class_id) = cv2.minMaxLoc(output[4:])
        # 先过滤模型置信度过低的候选，减少后续 NMS 数量和误识别。
        if max_score < 0.25:
            continue
        # NMSBoxes 需要左上角坐标和宽高，因此从模型中心点格式转换。
        boxes.append(
            [
                output[0] - output[2] / 2,
                output[1] - output[3] / 2,
                output[2],
                output[3],
            ]
        )
        scores.append(float(max_score))
        class_ids.append(class_id)

    # 抑制高度重叠的重复框，同一个游戏节点最终只保留最高置信度检测。
    result_boxes = cv2.dnn.NMSBoxes(boxes, scores, 0, 0.4, 0.5)
    node_list = []
    # 给 Bus 右侧留 50px 容差；中心落在更左侧的框视为已经走过或无关节点。
    min_x = bus_x + 50 * (cfg.set_win_size / 1440)
    for result_index in result_boxes:
        # 不同 OpenCV 版本可能返回标量或单元素数组，统一展平后取整数索引。
        index = int(np.asarray(result_index).reshape(-1)[0])
        x, y, width, height = (float(value) for value in boxes[index])
        # 将 640 输入空间的框中心还原到原截图坐标，供网格吸附和实际点击使用。
        center = (
            int((x + width / 2) * image_scale),
            int((y + height / 2) * image_scale),
        )
        if center[0] >= min_x:
            # 列表格式保持与后续 _snap_points_to_grid() 的输入约定一致。
            node_list.append([classes[class_ids[index]], center])
    return node_list


@lru_cache(maxsize=1)
def _get_onnx_session():
    """复用 ONNX 会话，避免三位置探测时重复加载模型。"""
    import onnxruntime as ort

    # lru_cache(maxsize=1) 使模型在进程内只初始化一次，后续直接复用推理会话。
    return ort.InferenceSession("./assets/model/best.onnx")


def generate_map(
    points,
    bus_position,
    bus_row=0,
    hard_mode=False,
    team_number=None,
    floor=None,
    theme_pack_name=None,
):
    """把 ONNX 像素节点转换为带连接关系和运行上下文的逻辑地图。

    传入的三个上下文字段不会影响路线计算，只作为节点来源信息保存。普通节点、
    Bus 节点，以及普通镜牢中自动补齐的商店/BOSS 节点都携带相同上下文。
    """
    # ONNX 使用彩色截图；模板连线资源按灰度匹配，因此构图前刷新为灰度截图。
    if auto.take_screenshot(gray=True) is None:
        return {}
    # 第一步只把检测中心吸附到规则网格，还没有建立节点之间的可达关系。
    nodes = _snap_points_to_grid(
        points,
        bus_position,
        bus_row=bus_row,
        team_number=team_number,
        floor=floor,
        theme_pack_name=theme_pack_name,
    )
    # 第二步逐对匹配连线模板，只有画面上真实存在连线的相邻节点才建立有向边。
    _connect_visible_nodes(nodes)
    if not hard_mode:
        # 普通镜牢末端布局固定，可补齐 ONNX 画面外的商店和 BOSS；困难模式不假设该布局。
        _append_shop_and_boss(nodes, bus_position)
    return nodes


def _snap_points_to_grid(
    points,
    bus_position,
    bus_row,
    team_number=None,
    floor=None,
    theme_pack_name=None,
):
    """将像素坐标吸附到 `(行, 列)` 三行网格，并创建上下文快照。"""
    bus_row = _require_bus_row(bus_row)
    scale = cfg.set_win_size / 1440
    # 逻辑网格间距需要与截图分辨率同步缩放，否则 round() 会吸附到错误列/行。
    x_gap = X_GAP * scale
    y_gap = Y_GAP * scale
    # 集中构造一次上下文字典，确保 Bus 和全部 ONNX 节点使用完全相同的元数据。
    node_context = {
        "team_number": team_number,
        "floor": floor,
        "theme_pack_name": theme_pack_name,
    }
    # Bus 位于当前实际行和第 0 列，权重固定为 0，不参与路径代价。
    bus_coord = (bus_row, 0)
    nodes = {bus_coord: Node(bus_coord, "bus", bus_position, value=0, **node_context)}

    for node_type, screen_pos in points:
        # 屏幕 Y 向下增加而逻辑行向上增加，因此行偏移需要反号。
        column = round((screen_pos[0] - bus_position[0]) / x_gap)
        row = bus_row + round((bus_position[1] - screen_pos[1]) / y_gap)
        coord = (row, column)
        # 仅保留三行范围内、Bus 右侧尚未经过的节点；同一格多个检测保留第一个。
        if row in {-1, 0, 1} and column > 0 and coord not in nodes:
            nodes[coord] = Node(coord, node_type, screen_pos, **node_context)
    return nodes


def _connect_visible_nodes(nodes):
    """连接相邻列中确实存在路线模板的节点。"""
    # sorted() 让调试和测试中的遍历顺序稳定，不影响最终 Dijkstra 结果。
    for (row, column), source in sorted(nodes.items()):
        # 游戏路线只可能通向右侧相邻列的上一行、同一行或下一行。
        for next_row in (row + 1, row, row - 1):
            target = nodes.get((next_row, column + 1))
            # ONNX 识别到两个节点不代表它们相连，必须再用路线模板确认。
            if target is not None and _connection_exists(source, target):
                source.add_next(target)


def _connection_exists(source, target):
    """识别两个节点之间的上、中、下路线。"""
    # 目标行减来源行决定需要匹配上斜线、水平线还是下斜线模板。
    template = {1: "up", 0: "mid", -1: "down"}.get(target.coord[0] - source.coord[0])
    if template is None:
        return False

    scale = cfg.set_win_size / 1440
    # 路线主体位于两节点中心之间，搜索中点能避开节点图标本身。
    midpoint = (
        (source.screen_pos[0] + target.screen_pos[0]) / 2,
        (source.screen_pos[1] + target.screen_pos[1]) / 2,
    )
    # ROI 半径同样按分辨率缩放，并由 _crop_around() 限制在客户区内。
    crop = _crop_around(
        midpoint,
        CONNECTION_X_RADIUS * scale,
        CONNECTION_Y_RADIUS * scale,
    )
    return bool(
        auto.find_element(
            f"mirror/road_in_mir/{template}.png",
            threshold=0.75,
            my_crop=crop,
            model="aggressive",
        )
    )


def _crop_around(position, x_radius, y_radius):
    """生成限制在游戏客户区内的搜索区域。"""
    # 游戏采用 16:9 客户区，cfg.set_win_size 表示高度。
    width = cfg.set_win_size * 16 / 9
    height = cfg.set_win_size
    # 左上角用 max 防止负数，右下角用 min 防止超出截图边界。
    return (
        max(0, position[0] - x_radius),
        max(0, position[1] - y_radius),
        min(width, position[0] + x_radius),
        min(height, position[1] + y_radius),
    )


def _append_shop_and_boss(nodes, bus_position):
    """普通镜牢地图末尾缺少商店或 BOSS 时补齐末端节点。

    合成节点不是 ONNX 输出，因此无法直接获得运行上下文。这里以 Bus 节点为
    当前地图上下文的唯一来源，保证补出的商店和 BOSS 与已识别节点属于同一地图。
    BOSS 始终继承对应商店的 row，确保最终一步固定为 M。
    """
    # 用不同列数判断 ONNX 是否已看到足够多的地图；画面过少时不做末端布局假设。
    columns = sorted({coord[1] for coord in nodes})
    if len(columns) < VISIBLE_COLUMN_COUNT:
        return

    scale = cfg.set_win_size / 1440
    # Bus 总在第 0 列；从它复制上下文比额外传递三个参数更不容易出现不一致。
    bus = next((node for node in nodes.values() if node.type == "bus"), None)
    node_context = {
        "team_number": bus.team_number if bus else None,
        "floor": bus.floor if bus else None,
        "theme_pack_name": bus.theme_pack_name if bus else None,
    }
    # 找出当前识别范围最右列及其全部节点类型，判断商店/BOSS 是否已真实出现。
    last_column = columns[-1]
    last_nodes = [node for (_, column), node in nodes.items() if column == last_column]
    last_types = {node.type for node in last_nodes}

    if "boss_battle" in last_types:
        # BOSS 已由 ONNX 识别，继续补齐会制造重复终点和错误路线。
        return

    if "shop" in last_types:
        # 商店已存在时，只需在其右侧补齐同一行的 BOSS。
        shop_nodes = [node for node in last_nodes if node.type == "shop"]
        boss_column = last_column + 1
    else:
        # 商店也在屏幕外时，先在最右列后一列的中线创建固定商店。
        shop_column = last_column + 1
        shop_position = (
            bus_position[0] + shop_column * X_GAP * scale,
            bus_position[1],
        )
        shop_row = bus.coord[0] if bus else 0
        shop = Node((shop_row, shop_column), "shop", shop_position, synthetic=True, **node_context)
        nodes[shop.coord] = shop
        # 当前最右列的所有出口都汇入固定商店，这是普通镜牢末端的确定布局。
        for node in last_nodes:
            node.add_next(shop)
        shop_nodes = [shop]
        boss_column = shop_column + 1

    # BOSS 固定在商店右侧一列，但 row 必须继承商店，而不能强制写成 0。
    # 当 Bus 被放在 UP/DOWN 位置识别时，固定中线商店相对 Bus 可能是 row=1/-1；
    # 若仍把 BOSS 放到 row=0，就会错误生成 shop -> boss 的 U/D 方向。
    for shop in shop_nodes:
        boss_row = shop.coord[0]
        boss_coord = (boss_row, boss_column)

        # 异常情况下同一行可能识别出重复商店；复用已创建的同行 BOSS，避免覆盖节点。
        boss = nodes.get(boss_coord)
        if boss is None:
            boss_position = (
                bus_position[0] + boss_column * X_GAP * scale,
                shop.screen_pos[1],
            )
            boss = Node(
                boss_coord,
                "boss_battle",
                boss_position,
                synthetic=True,
                **node_context,
            )
            nodes[boss.coord] = boss

        # shop 与其对应 BOSS 的 row 完全相同，因此该边恒定转换为 M。
        shop.add_next(boss)


def find_min_weight_route(floor_map):
    """使用 Dijkstra 计算从 bus 到终点的最低权重路线。"""
    # Bus 的行由实际位置决定，因此按节点类型寻找第 0 列起点。
    start = next(
        (
            node
            for (_, column), node in floor_map.items()
            if column == 0 and node.type == "bus"
        ),
        None,
    )
    if start is None:
        return float("inf"), []

    # 统计每列节点数，用来排除“中间列误识别成 BOSS”的检测结果。
    column_node_counts = {}
    for _, column in floor_map:
        column_node_counts[column] = column_node_counts.get(column, 0) + 1

    # 只有独占一整列的 BOSS 才视为真正终点；正常 BOSS 列不会同时存在其他节点。
    targets = {
        node
        for (_, column), node in floor_map.items()
        if node.type == "boss_battle" and column_node_counts[column] == 1
    }
    if not targets:
        # 困难模式或识别范围不足时可能没有 BOSS，此时以最右可见列作为临时终点。
        furthest_column = max(coord[1] for coord in floor_map)
        if furthest_column == 0:
            # 地图只有 Bus，没有任何可执行步骤。
            return float("inf"), []
        targets = {node for coord, node in floor_map.items() if coord[1] == furthest_column}

    # distances 保存目前已知的最低累计代价；Bus 权重通常为 0。
    distances = {start: start.value}
    # 堆元素加入 id(node) 作为稳定的并列项，避免 Python 在同权重时直接比较 Node。
    queue = [(start.value, id(start), start, [start])]
    while queue:
        # 每次取出当前累计代价最低的候选路线。
        total, _, current, route = heapq.heappop(queue)
        # 同一节点可能以旧的较高代价留在堆中，发现过期项后直接跳过。
        if total != distances.get(current):
            continue
        if current in targets:
            # Dijkstra 首次弹出的终点即为全局最低代价路线，可以立即返回。
            return total, route
        for next_node in current.next:
            # 节点代价在“进入该节点”时累加，边本身没有额外权重。
            new_total = total + next_node.value
            if new_total < distances.get(next_node, float("inf")):
                # 找到更低代价后更新距离，并把包含完整节点序列的新路线压入堆。
                distances[next_node] = new_total
                heapq.heappush(
                    queue,
                    (new_total, id(next_node), next_node, route + [next_node]),
                )
    # 队列耗尽仍未到达任何终点，说明连线模板构出的图不连通。
    return float("inf"), []


def route_to_directions(floor_route):
    """把节点路线转换为 U/M/D 方向和节点类型。"""
    directions = []
    # zip(route, route[1:]) 逐对遍历“当前节点 -> 下一节点”。
    for current_node, next_node in zip(floor_route, floor_route[1:]):
        direction = get_node_direction(current_node, next_node)
        if direction is None:
            # 任一节点对无法转换方向时，整条操作序列都不应继续执行。
            return [], [node.type for node in floor_route]
        directions.append(direction)
    # 节点类型主要用于日志和调试，不参与后续点击。
    return directions, [node.type for node in floor_route]
