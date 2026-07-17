import time
from time import sleep

from module.automation import auto
from module.config import cfg
from module.logger import log
from module.my_error.my_error import InputAttributeError
from tasks.mirror import road_map


class MirrorMap:
    def __init__(self, floor=1, hard_mode=False):
        self.floor = floor
        self.floor_map = []
        self.floor_nodes = []
        self.map = {}
        self.hard_mode = hard_mode
        self.theme_pack = ""
        self.team_number = None
        self.pending_node = None
        self.current_node = None
        self.current_node_started_at = None
        self.node_history = []

    def set_node_context(self, theme_pack, team_number, floor):
        """更新生成路线节点时需要的镜牢上下文。"""
        self.theme_pack = theme_pack
        self.team_number = team_number
        self.floor = floor

    def get_next_step(self, flow_watchdog=None):
        """获取下一步寻路方向，必要时重新识别当前楼层路线。"""
        # 标记是否需要重新截图并识别路线。
        re_identify = False

        # 识别 floor_map 中的路线。
        if len(self.floor_map) > 0:
            next_step = self.floor_map.pop(0)
            if next_step is not None:
                self.pending_node = self.floor_nodes.pop(0) if self.floor_nodes else None
                return next_step
            else:
                re_identify = True
        else:
            # 没有缓存路线时，直接进行路线识别。
            re_identify = True

        if re_identify is True:
            # 重新识别路线，并把 watchdog 传入识别过程以检测长时间无进展。
            self.floor_map, self.floor_nodes = search_road_from_road_map(
                hard_mode=self.hard_mode,
                flow_watchdog=flow_watchdog,
                theme_pack=self.theme_pack,
                team_number=self.team_number,
                floor=self.floor,
            )
            # (True, True) 表示识别过程中已经直接点击并进入了下一个节点。
            if self.floor_map is True and self.floor_nodes is True:
                return True
            # 统一转换为列表，便于后续按顺序弹出路线步骤和复制缓存。
            if not isinstance(self.floor_map, list):
                self.floor_map = list(self.floor_map)
            # 保存本楼层的完整路线和节点信息；切片避免后续 pop 修改缓存。
            self.map[f"floor{self.floor}"] = [self.floor_map[:], self.floor_nodes[:]]

        # 返回新识别路线中的第一步，并从待执行路线中移除。
        if len(self.floor_map) > 0:
            next_step = self.floor_map.pop(0)
            self.pending_node = self.floor_nodes.pop(0) if self.floor_nodes else None
            return next_step
        else:
            # 识别后仍没有可走路线。
            return False

    def enter_next_node(self, next_step):
        self._log_current_node_before_next_entry()
        if cfg.mirror_keyboard_navigation:
            log.debug(f"通过键盘按键寻路: {next_step}")
            if next_step == "U":
                auto.key_press("up")
            elif next_step == "D":
                auto.key_press("down")
            elif next_step == "M":
                auto.key_press("right")
            sleep(0.5)
            auto.key_press("enter")
            sleep(1.25)
            if auto.click_element("mirror/road_in_mir/enter_assets.png", take_screenshot=True):
                self._record_pending_node_entry()
                return True
            self._record_pending_node_entry()
            return True

        if next_position := self._get_next_position(next_step):
            auto.mouse_click(next_position[0], next_position[1])
            sleep(1.25)
            if auto.click_element("mirror/road_in_mir/enter_assets.png", take_screenshot=True):
                self._record_pending_node_entry()
                return True
        if auto.click_element("mirror/mybus_default_distance.png", take_screenshot=True):
            sleep(1.25)
            if auto.click_element("mirror/road_in_mir/enter_assets.png", take_screenshot=True):
                return True
        return False

    def _log_current_node_before_next_entry(self):
        """进入下一节点前，输出当前节点的上下文和已停留时间。"""
        if self.current_node is None or self.current_node_started_at is None:
            return

        self.current_node.node_time = time.monotonic() - self.current_node_started_at
        log.info(
            "当前节点: "
            f"team_number={self.current_node.team_number}, "
            f"package={self.current_node.theme_pack}, "
            f"time_cost={self.current_node.node_time:.2f}秒, "
            f"node_type={self.current_node.type}, "
            f"floor={self.current_node.floor}"
        )

    def _record_pending_node_entry(self):
        """完成上一个节点计时，并从当前成功进入的节点重新开始计时。"""
        entered_node = self.pending_node
        self.pending_node = None
        if entered_node is None:
            return

        entered_at = time.monotonic()
        if self.current_node is not None and self.current_node_started_at is not None:
            self.current_node.node_time = entered_at - self.current_node_started_at

        self.current_node = entered_node
        self.current_node_started_at = entered_at
        self.node_history.append(entered_node)

    def _get_next_position(self, direction):
        scale = cfg.set_win_size / 1440
        three_roads = [
            [500 * scale, 50 * scale],
            [500 * scale, 450 * scale],
            [500 * scale, -400 * scale],
        ]
        if direction == "M":
            position = 0
        elif direction == "D":
            position = 1
        elif direction == "U":
            position = 2
        for _ in range(3):
            if bus_position := auto.find_element("mirror/mybus_default_distance.png", take_screenshot=True):
                return [
                    bus_position[0] + three_roads[position][0],
                    bus_position[1] + three_roads[position][1],
                ]
            sleep(1)
        return None

    def refresh_floor(self, floor):
        if self.floor == floor:
            return
        log.debug(f"镜牢地图楼层缓存更新: {self.floor} -> {floor}")
        self.floor = floor
        self.floor_map = []


def get_node_weight(x, y):
    scale = cfg.set_win_size / 1440
    road_node_bbox = (
        x - 125 * scale,
        y - 125 * scale,
        x + 125 * scale,
        y + 125 * scale,
    )
    if auto.find_feature_element("mirror/road_in_mir/shop.png", road_node_bbox, 50):
        return 3
    elif auto.find_feature_element("mirror/road_in_mir/event.png", road_node_bbox):
        return 3
    elif auto.find_feature_element(
        "mirror/road_in_mir/battle.png",
        road_node_bbox,
    ):
        return 2
    elif auto.find_feature_element("mirror/road_in_mir/hard_battle.png", road_node_bbox):
        return 1
    elif auto.find_feature_element("mirror/road_in_mir/hard_battle2.png", road_node_bbox):
        return 0
    return -5


# 在默认缩放情况下，进行镜牢寻路
def search_road_default_distance(flow_watchdog=None):
    start_time = time.time()
    scale = cfg.set_win_size / 1440
    three_roads = [
        [500 * scale, 50 * scale],
        [500 * scale, 450 * scale],
        [500 * scale, -400 * scale],
    ]

    auto.mouse_to_blank()
    while auto.take_screenshot() is None:
        if flow_watchdog is not None and not flow_watchdog.check():
            return False
    # 判断中、下两个节点是否有权重3的节点，有的话直接选择进入
    node_weight = {}
    if bus_position := auto.find_element("mirror/mybus_default_distance.png", take_screenshot=True):
        for road in three_roads[:2]:
            node_x = bus_position[0] + road[0]
            node_y = bus_position[1] + road[1]
            weight = get_node_weight(node_x, node_y)
            node_weight[(node_x, node_y)] = weight
        max_weight = max(node_weight.values())
        if max_weight == 3:
            road_list = sorted(node_weight, key=node_weight.get, reverse=True)
            road = road_list[0]
            if 0 < road[0] < cfg.set_win_size * 16 / 9 and 0 < road[1] < cfg.set_win_size:
                auto.mouse_click(road[0], road[1])
                sleep(0.75)
                if auto.click_element("mirror/road_in_mir/enter_assets.png", take_screenshot=True):
                    return True
    # 如果中、下两个节点没有权重3的节点，查看所有节点的权重，选择权重最大的节点进入
    if bus_position := auto.find_element("mirror/mybus_default_distance.png", take_screenshot=True):
        from tasks.base.retry import check_times

        while True:
            if flow_watchdog is not None and not flow_watchdog.check():
                return False
            if auto.get_restore_time() is not None:
                start_time = max(start_time, auto.get_restore_time())
            if check_times(start_time, logs=False):
                from tasks.base.back_init_menu import back_init_menu

                back_init_menu()
                return False
            if 600 * scale < bus_position[1] < 700 * scale:
                break
            dy = 650 * scale - bus_position[1]
            auto.mouse_drag(bus_position[0], bus_position[1], drag_time=1.5, dx=0, dy=dy)
            sleep(1)
            auto.mouse_to_blank()

            bus_position = auto.find_element("mirror/mybus_default_distance.png", take_screenshot=True)
            if bus_position is None:
                break

    node_list = []
    if bus_position := auto.find_element("mirror/mybus_default_distance.png", take_screenshot=True):
        for road in three_roads[:2]:
            node_x = bus_position[0] + road[0]
            node_y = bus_position[1] + road[1]
            node_list.append((node_x, node_y))
        old_weight = node_weight.values()
        all_node_weight = dict(zip(node_list, old_weight))
        for road in three_roads[2:]:
            node_x = bus_position[0] + road[0]
            node_y = bus_position[1] + road[1]
            weight = get_node_weight(node_x, node_y)
            all_node_weight[(node_x, node_y)] = weight
        all_node_weight[bus_position[0], bus_position[1]] = -6
        # 根据all_node_weight，按照各个键的值，从大到小以生成只有键的新的列表
        road_list = sorted(all_node_weight, key=all_node_weight.get, reverse=True)
        for road in road_list:
            if 0 < road[0] < cfg.set_win_size * 16 / 9 and 0 < road[1] < cfg.set_win_size:
                auto.mouse_click(road[0], road[1])
                sleep(0.75)
                if auto.click_element("mirror/road_in_mir/enter_assets.png", take_screenshot=True):
                    return True
    return False


# 如果默认缩放无法镜牢寻路，进行滚轮缩放后继续寻路
def search_road_farthest_distance(flow_watchdog=None):
    scale = cfg.set_win_size / 1440
    auto.mouse_click_blank()
    if not auto.mouse_scroll():
        raise InputAttributeError("后台输入不支持滚轮操作!")
    while auto.take_screenshot() is None:
        if flow_watchdog is not None and not flow_watchdog.check():
            return False
    three_roads = [
        [250 * scale, -200 * scale],
        [250 * scale, 0],
        [250 * scale, 225 * scale],
    ]
    if bus_position := auto.find_element("mirror/mybus_maximum_distance.png"):
        for road in three_roads:
            road[0] += bus_position[0]
            road[1] += bus_position[1]
            if 0 < road[0] < cfg.set_win_size * 16 / 9 and 0 < road[1] < cfg.set_win_size:
                auto.mouse_click(road[0], road[1])
                sleep(0.75)
                if auto.click_element("mirror/road_in_mir/enter_assets.png", take_screenshot=True):
                    return True
        auto.mouse_click(bus_position[0], bus_position[1])
        if auto.click_element("mirror/road_in_mir/enter_assets.png", take_screenshot=True):
            return True
    return False


def search_road_from_road_map(
    hard_mode=False,
    flow_watchdog=None,
    theme_pack="",
    team_number=None,
    floor=None,
):
    """执行 bus 归一化、ONNX 识别、建图和最低权重寻路。"""
    del hard_mode

    if auto.click_element("mirror/mybus_default_distance.png", take_screenshot=True):
        sleep(0.75)
        if auto.click_element(
            "mirror/road_in_mir/enter_assets.png",
            take_screenshot=True,
        ):
            return True, True

    result = road_map.onnx(
        flow_watchdog,
        theme_pack=theme_pack,
        floor=floor,
    )
    if result is None:
        return [], []

    bus_position, _, points = result
    nodes = road_map.generate_map(
        points,
        bus_position,
        theme_pack=theme_pack,
        team_number=team_number,
        floor=floor,
    )
    min_weight, route = road_map.path(nodes)
    if not route:
        log.warning("未能检测到可达路径")
        return [], []

    directions, node_types = road_map.path_to_result(route)
    log.info(f"最低路径权重: {min_weight}")
    log.info(f"路径方向: {directions}")
    log.info(f"行走节点: {node_types}")
    # bus 是当前所在位置；每个方向对应 route 中 bus 之后的一个目标节点。
    return directions, route[1:]
