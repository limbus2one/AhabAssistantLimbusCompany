import heapq
from enum import Enum

import cv2

from module.automation import auto
from module.config import cfg
from module.logger import log


class Position(Enum):
    UP = "up"
    MID = "mid"
    DOWN = "down"


X_GAP = 520
Y_GAP = 437
VISIBLE_COLUMN_COUNT = 4
CONNECTION_X_RADIUS = 150
CONNECTION_Y_RADIUS = 120


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
    """镜牢地图中的一个节点。"""

    def __init__(self, coord, node_type, screen_pos, value=None, synthetic=False):
        self.coord = coord
        self.type = node_type
        self.screen_pos = screen_pos
        self.value = NODE_WEIGHT.get(node_type, 999) if value is None else value
        self.next = []
        self.synthetic = synthetic

    def add_next(self, next_node):
        if next_node not in self.next:
            self.next.append(next_node)

    def __repr__(self):
        next_coords = [node.coord for node in self.next]
        return f"Node(coord={self.coord}, type={self.type!r}, value={self.value}, next={next_coords})"


def get_node_direction(current_node, next_node):
    """根据两个相邻节点的行差返回 U/M/D。"""
    row_delta = next_node.coord[1] - current_node.coord[1]
    return {-1: "U", 0: "M", 1: "D"}.get(row_delta)


class MirrorMap:
    """管理当前楼层的 ONNX 路线缓存并进入下一个节点。"""

    def __init__(self, floor=1, hard_mode=False):
        self.floor = floor
        self.hard_mode = hard_mode
        self.floor_route = []
        self.floor_map = {}

    def get_next_node_direction(self):
        """返回最优路线中下一个节点的 U/M/D 方向。"""
        if len(self.floor_route) < 2:
            try:
                floor_route, floor_map = search_road_from_road_map(hard_mode=self.hard_mode)
            except Exception as error:
                log.warning(f"镜牢 ONNX 寻路出错: {error}")
                self._clear_floor_data()
                return self._find_first_node_direction()

            if len(floor_route) < 2 or not floor_map:
                self._clear_floor_data()
                log.warning("镜牢 ONNX 未识别到有效路线，直接识别首个方向")
                return self._find_first_node_direction()
            self.floor_route = list(floor_route)
            self.floor_map = dict(floor_map)

        next_node_direction = get_node_direction(
            self.floor_route[0],
            self.floor_route[1],
        )
        if next_node_direction is None:
            log.warning("缓存路线方向无效，直接识别首个方向")
            self._clear_floor_data()
            return self._find_first_node_direction()
        return next_node_direction

    def enter_next_node(self, next_node_direction):
        """选择、确认并进入下一节点，成功后才消费路线缓存。"""

        # bus 移动到节点
        if cfg.mirror_keyboard_navigation:
            key = {"U": "up", "M": "right", "D": "down"}[next_node_direction]
            auto.key_press(key)
        else:
            next_node_position = self._get_next_node_position(next_node_direction)
            auto.mouse_action_with_pos(next_node_position)
        # 判断该节点是战斗还是事件
        target = auto.wait_page_load(
            [
                "mirror/road_in_mir/enter_assets.png",
                "mirror/road_in_mir/event_in_assets.png"
            ],
        )
        # bus 进入节点内部
        if target == "mirror/road_in_mir/enter_assets.png":
            if cfg.mirror_keyboard_navigation:
                auto.key_press("enter")
            else:
                auto.click_element("mirror/road_in_mir/enter_assets.png")

        # 触发事件或战斗
        if self._get_next_node().type is not None and self._get_next_node().type != "shop":
            auto.wait_page_load(
                [
                    "teams/identify_assets.png",
                    "mirror/road_in_mir/event_in_assets.png",
                ], model="clam"
            )
        self._consume_route_node()
        return True

    def _get_next_node(self):
        """返回路线中的下一个节点；无路线缓存时返回 None。"""
        if len(self.floor_route) < 2:
            return None
        return self.floor_route[1]

    def refresh_floor(self, floor):
        """楼层变化时清空方向缓存。"""
        if self.floor == floor:
            return
        log.debug(f"镜牢地图楼层缓存更新: {self.floor} -> {floor}")
        self.floor = floor
        self._clear_floor_data()

    def _get_next_node_position(self, next_node_direction):
        """返回 bus 右侧节点的屏幕坐标。"""
        scale = cfg.set_win_size / 1440
        offsets = {
            "M": (X_GAP * scale, 0),
            "D": (X_GAP * scale, Y_GAP * scale),
            "U": (X_GAP * scale, -Y_GAP * scale),
        }
        for _ in range(3):
            bus_position = auto.find_element(
                "mirror/mybus_default_distance.png",
                take_screenshot=True,
            )
            if bus_position:
                dx, dy = offsets[next_node_direction]
                return bus_position[0] + dx, bus_position[1] + dy
        return None

    def _consume_route_node(self):
        if self.floor_route:
            self.floor_route.pop(0)

    def _find_first_node_direction(self):
        bus_position = auto.find_element(
            "mirror/mybus_default_distance.png",
            take_screenshot=True,
        )
        if not bus_position:
            return False
        return find_first_direction(bus_position)

    def _clear_floor_data(self):
        self.floor_route = []
        self.floor_map = {}


def search_road_from_road_map(hard_mode=False):
    """返回包含 bus 的最优节点路线和当前楼层完整节点图。"""

    onnx_result = onnx()
    if not onnx_result:
        return [], {}

    bus_position, _, points = onnx_result
    if bus_position is None or not points:
        return [], {}

    floor_map = generate_map(points, bus_position, hard_mode=hard_mode)
    min_weight, floor_route = find_min_weight_route(floor_map)
    if not floor_route:
        log.warning("ONNX 未能构建可达路线")
        return [], {}

    directions, node_types = route_to_directions(floor_route)

    log.info(f"镜牢 ONNX 路线: 权重={min_weight}, 方向={directions}, 节点={node_types}")
    return floor_route, floor_map


def find_bus(take_screenshot=True):
    """定位 bus，并根据右侧节点分布判断 bus 所在行。"""
    bus_position = auto.find_element(
        "mirror/mybus_default_distance.png",
        take_screenshot=take_screenshot,
    )
    if bus_position is None:
        return None

    scale = cfg.set_win_size / 1440
    light_positions = (
        auto.find_element(
            "mirror/road_in_mir/light.png",
            find_type="image_with_multiple_targets",
        )
        or []
    )
    event_positions = (
        auto.find_element(
            "mirror/road_in_mir/event.png",
            find_type="image_with_multiple_targets",
        )
        or []
    )
    visible_positions = light_positions + event_positions

    up_exists = any(y < bus_position[1] - Y_GAP * scale / 2 for _, y in visible_positions)
    down_exists = any(y > bus_position[1] + Y_GAP * scale / 2 for _, y in visible_positions)

    if up_exists == down_exists:
        bus_row = Position.MID
    elif up_exists:
        bus_row = Position.DOWN
    elif down_exists:
        bus_row = Position.UP
    else:
        bus_row = None
    return bus_position, bus_row


def find_first_direction(bus_position):
    """按上、中、下顺序识别 bus 到相邻节点的第一条连线。"""
    if not bus_position or auto.take_screenshot() is None:
        return False

    scale = cfg.set_win_size / 1440
    direction_targets = (
        ("U", "up_arr", (500, -400)),
        ("M", "mid_arr", (500, 50)),
        ("D", "down_arr", (500, 450)),
    )
    for direction, template, (node_dx, node_dy) in direction_targets:
        connection_midpoint = (
            bus_position[0] + node_dx * scale / 2,
            bus_position[1] + node_dy * scale / 2,
        )
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
            return direction
    return False


def move_bus(bus_position, bus_row):
    """拖动地图，使三行四列节点进入 ONNX 识别区域。"""
    scale = cfg.set_win_size / 1440
    bus_x, bus_y = bus_position
    dx = 120 * scale - bus_x
    if bus_row is Position.UP:
        dy = 700 * scale - bus_y - Y_GAP * scale
    elif bus_row is Position.MID:
        dy = 700 * scale - bus_y
    else:
        dy = 700 * scale - bus_y + Y_GAP * scale

    auto.mouse_drag(bus_x, bus_y, drag_time=0.5, dx=dx, dy=dy)
    moved_bus_position = auto.find_element(
        "mirror/mybus_default_distance.png",
        take_screenshot=True,
    )
    if moved_bus_position is None:
        return None
    return moved_bus_position, bus_row


def onnx():
    """归一化镜牢地图画面并运行 ONNX 节点识别。"""
    bus_result = find_bus()
    if bus_result is None:
        return None

    moved_bus_result = move_bus(*bus_result)
    if moved_bus_result is None:
        return None
    bus_position, bus_row = moved_bus_result

    if auto.take_screenshot(gray=False) is None:
        return None
    points = identify_nodes(bus_position[0], image=auto.screenshot)
    if not points:
        return None
    return bus_position, bus_row, points


def identify_nodes(bus_x, image=None):
    """使用 ONNX 识别 bus 右侧节点。"""
    import numpy as np
    import onnxruntime as ort

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
        if auto.take_screenshot(gray=False) is None:
            return []
        image = auto.screenshot

    original = np.array(image)
    height, width = original.shape[:2]
    length = max(height, width)
    square = np.zeros((length, length, 3), np.uint8)
    square[:height, :width] = original[:, :, :3]
    image_scale = length / 640
    blob = cv2.dnn.blobFromImage(
        square,
        scalefactor=1 / 255,
        size=(640, 640),
        swapRB=False,
    )

    session = ort.InferenceSession("./assets/model/best.onnx")
    outputs = session.run(None, {session.get_inputs()[0].name: blob})[0]
    outputs = cv2.transpose(outputs[0])

    boxes = []
    scores = []
    class_ids = []
    for output in outputs:
        _, max_score, _, (_, class_id) = cv2.minMaxLoc(output[4:])
        if max_score < 0.25:
            continue
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

    result_boxes = cv2.dnn.NMSBoxes(boxes, scores, 0, 0.4, 0.5)
    node_list = []
    min_x = bus_x + 50 * (cfg.set_win_size / 1440)
    for result_index in result_boxes:
        index = int(np.asarray(result_index).reshape(-1)[0])
        x, y, width, height = (float(value) for value in boxes[index])
        center = (
            int((x + width / 2) * image_scale),
            int((y + height / 2) * image_scale),
        )
        if center[0] >= min_x:
            node_list.append([classes[class_ids[index]], center])
    return node_list


def generate_map(points, bus_position, hard_mode=False):
    """把 ONNX 节点转换为带连接关系的逻辑地图。"""
    # ONNX 使用彩色截图；模板连线识别前切回灰度，保证图像类型一致。
    if auto.take_screenshot(gray=True) is None:
        return {}
    nodes = _snap_points_to_grid(points, bus_position)
    _connect_visible_nodes(nodes)
    if not hard_mode:
        _append_shop_and_boss(nodes, bus_position)
    return nodes


def _snap_points_to_grid(points, bus_position):
    """将像素坐标吸附到以 bus 为原点的三行网格。"""
    scale = cfg.set_win_size / 1440
    x_gap = X_GAP * scale
    y_gap = Y_GAP * scale
    nodes = {(0, 0): Node((0, 0), "bus", bus_position, value=0)}

    for node_type, screen_pos in points:
        column = round((screen_pos[0] - bus_position[0]) / x_gap)
        row = round((screen_pos[1] - bus_position[1]) / y_gap)
        coord = (column, row)
        if column > 0 and coord not in nodes:
            nodes[coord] = Node(coord, node_type, screen_pos)
    return nodes


def _connect_visible_nodes(nodes):
    """连接相邻列中确实存在路线模板的节点。"""
    for (column, row), source in sorted(nodes.items()):
        for next_row in (row - 1, row, row + 1):
            target = nodes.get((column + 1, next_row))
            if target is not None and _connection_exists(source, target):
                source.add_next(target)


def _connection_exists(source, target):
    """识别两个节点之间的上、中、下路线。"""
    template = {-1: "up", 0: "mid", 1: "down"}.get(target.coord[1] - source.coord[1])
    if template is None:
        return False

    scale = cfg.set_win_size / 1440
    midpoint = (
        (source.screen_pos[0] + target.screen_pos[0]) / 2,
        (source.screen_pos[1] + target.screen_pos[1]) / 2,
    )
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
    width = cfg.set_win_size * 16 / 9
    height = cfg.set_win_size
    return (
        max(0, position[0] - x_radius),
        max(0, position[1] - y_radius),
        min(width, position[0] + x_radius),
        min(height, position[1] + y_radius),
    )


def _append_shop_and_boss(nodes, bus_position):
    """普通镜牢地图末尾缺少商店或 BOSS 时补齐固定中线节点。"""
    columns = sorted({coord[0] for coord in nodes})
    if len(columns) < VISIBLE_COLUMN_COUNT:
        return

    scale = cfg.set_win_size / 1440
    last_column = columns[-1]
    last_nodes = [node for (column, _), node in nodes.items() if column == last_column]
    last_types = {node.type for node in last_nodes}

    if "boss_battle" in last_types:
        return

    if "shop" in last_types:
        shop_nodes = [node for node in last_nodes if node.type == "shop"]
        boss_column = last_column + 1
    else:
        shop_column = last_column + 1
        shop_position = (
            bus_position[0] + shop_column * X_GAP * scale,
            bus_position[1],
        )
        shop = Node((shop_column, 0), "shop", shop_position, synthetic=True)
        nodes[shop.coord] = shop
        for node in last_nodes:
            node.add_next(shop)
        shop_nodes = [shop]
        boss_column = shop_column + 1

    boss_position = (
        bus_position[0] + boss_column * X_GAP * scale,
        bus_position[1],
    )
    boss = Node((boss_column, 0), "boss_battle", boss_position, synthetic=True)
    nodes[boss.coord] = boss
    for shop in shop_nodes:
        shop.add_next(boss)


def find_min_weight_route(floor_map):
    """使用 Dijkstra 计算从 bus 到终点的最低权重路线。"""
    start = floor_map.get((0, 0))
    if start is None:
        return float("inf"), []

    column_node_counts = {}
    for column, _ in floor_map:
        column_node_counts[column] = column_node_counts.get(column, 0) + 1

    targets = {
        node
        for (column, _), node in floor_map.items()
        if node.type == "boss_battle" and column_node_counts[column] == 1
    }
    if not targets:
        furthest_column = max(coord[0] for coord in floor_map)
        if furthest_column == 0:
            return float("inf"), []
        targets = {node for coord, node in floor_map.items() if coord[0] == furthest_column}

    distances = {start: start.value}
    queue = [(start.value, id(start), start, [start])]
    while queue:
        total, _, current, route = heapq.heappop(queue)
        if total != distances.get(current):
            continue
        if current in targets:
            return total, route
        for next_node in current.next:
            new_total = total + next_node.value
            if new_total < distances.get(next_node, float("inf")):
                distances[next_node] = new_total
                heapq.heappush(
                    queue,
                    (new_total, id(next_node), next_node, route + [next_node]),
                )
    return float("inf"), []


def route_to_directions(floor_route):
    """把节点路线转换为 U/M/D 方向和节点类型。"""
    directions = []
    for current_node, next_node in zip(floor_route, floor_route[1:]):
        direction = get_node_direction(current_node, next_node)
        if direction is None:
            return [], [node.type for node in floor_route]
        directions.append(direction)
    return directions, [node.type for node in floor_route]
