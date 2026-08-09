import heapq
import json
import time
from enum import Enum
from pathlib import Path
from time import sleep

import cv2

from module.automation import auto
from module.config import cfg
from module.logger import log
from module.my_error.my_error import MirrorPathfindingError

REFERENCE_SCREEN_HEIGHT = 1440
X_GAP = 520
Y_GAP = 437
CONNECTION_X_RADIUS = 150
CONNECTION_Y_RADIUS = 120
CONNECTION_MATCH_THRESHOLD = 0.75

NODE_WEIGHT = {
    "battle": 4,
    "boss_battle": 6,
    "event": 1,
    "focused_encounter": 6,
    "risky_encounter": 7,
    "shop": 2,
    "abnormality_focused_encounter": 6,
    "bus": 0,
}


class Position(Enum):
    UP = "up"
    MID = "mid"
    DOWN = "down"


class Node:
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
        return f"Node(coord={self.coord}, type={self.type!r}, next={[node.coord for node in self.next]})"


def get_node_direction(current_node, next_node):
    return {-1: "U", 0: "M", 1: "D"}.get(next_node.coord[1] - current_node.coord[1])


class MirrorMap:
    """缓存当前楼层的节点图和最低权重路线。"""

    def __init__(self, floor=1, hard_mode=False):
        self.floor = floor
        self.hard_mode = hard_mode
        self.floor_route = []
        self.floor_map = {}
        self.save_shifted_images = False
        self.bus_row = None

    def reset_bus_row(self):
        self.bus_row = Position.MID
        log.debug("镜牢 Bus 所在行已由主题包流程重置为 MID")

    def forget_bus_row(self):
        self.bus_row = None

    def get_next_node_direction(self):
        if len(self.floor_route) < 2:
            if self.bus_row is None:
                raise MirrorPathfindingError("镜牢 Bus 所在行未知")
            save_shifted_images = self.save_shifted_images
            self.save_shifted_images = False
            floor_route, floor_map = search_road_from_road_map(
                hard_mode=self.hard_mode,
                save_shifted_images=save_shifted_images,
                bus_row=self.bus_row,
            )
            if self.hard_mode:
                floor_route = floor_route[:2]
            if len(floor_route) < 2:
                raise MirrorPathfindingError("镜牢路线不足两个节点")
            self.floor_route = list(floor_route)
            self.floor_map = dict(floor_map)

        direction = get_node_direction(self.floor_route[0], self.floor_route[1])
        if direction is None:
            raise MirrorPathfindingError("缓存路线包含非相邻节点")
        return direction

    def enter_next_node(self, direction):
        if cfg.mirror_keyboard_navigation:
            auto.key_press({"U": "up", "M": "right", "D": "down"}[direction])
            sleep(1.25)
            auto.key_press("enter")
        else:
            position = self._get_next_node_position(direction)
            if position is None:
                raise MirrorPathfindingError("无法定位下一个镜牢节点")
            auto.mouse_click(*position)

        sleep(1.25)
        if not _enter_succeeded():
            raise MirrorPathfindingError("无法确认已进入镜牢节点")

        rows = [Position.UP, Position.MID, Position.DOWN]
        if self.bus_row in rows:
            row_index = rows.index(self.bus_row) + {"U": -1, "M": 0, "D": 1}[direction]
            self.bus_row = rows[row_index] if 0 <= row_index < len(rows) else None
        self.floor_route.pop(0)
        return True

    def refresh_floor(self, floor):
        if self.floor == floor:
            return
        log.debug(f"镜牢地图楼层缓存更新: {self.floor} -> {floor}")
        self.floor = floor
        self.floor_route = []
        self.floor_map = {}

    def _get_next_node_position(self, direction):
        scale = cfg.set_win_size / REFERENCE_SCREEN_HEIGHT
        offsets = {
            "U": (X_GAP * scale, -Y_GAP * scale),
            "M": (X_GAP * scale, 0),
            "D": (X_GAP * scale, Y_GAP * scale),
        }
        for _ in range(3):
            bus_position = auto.find_element(
                "mirror/mybus_default_distance.png",
                take_screenshot=True,
            )
            if bus_position:
                dx, dy = offsets[direction]
                return bus_position[0] + dx, bus_position[1] + dy
            sleep(0.5)
        return None


def _enter_succeeded():
    if auto.click_element("mirror/road_in_mir/enter_assets.png", take_screenshot=True):
        sleep(0.75)
    for _ in range(3):
        if not auto.find_element("mirror/road_in_mir/legend_assets.png", take_screenshot=True):
            return True
        sleep(0.5)
    return False


def search_road_simple_keyboard():
    """始终按上，不进入 ONNX 寻路。"""
    auto.mouse_to_blank()
    sleep(0.3)
    for attempt in range(2):
        log.debug(f"简单键盘寻路: 第 {attempt + 1} 次尝试按↑+回车")
        auto.key_press("up")
        sleep(0.5)
        auto.key_press("enter")
        sleep(1.25)
        if _enter_succeeded():
            return True
    return False


def search_road_from_road_map(hard_mode=False, save_shifted_images=False, bus_row=Position.MID):
    onnx_result = onnx(bus_row=bus_row, save_shifted_images=save_shifted_images)
    if onnx_result is None:
        raise MirrorPathfindingError("镜牢 ONNX 节点识别失败")

    bus_position, points, capture_id = onnx_result
    floor_map = generate_map(points, bus_position)
    if not floor_map:
        raise MirrorPathfindingError("镜牢节点图生成失败")

    min_weight, floor_route = find_min_weight_route(floor_map)
    route_for_log = floor_route[:2] if hard_mode else floor_route
    _save_pathfinding_logs(capture_id, floor_map, route_for_log, min_weight, hard_mode)
    if len(floor_route) < 2:
        raise MirrorPathfindingError("镜牢节点图中不存在可达路线")

    directions, node_types = route_to_directions(floor_route)
    log.info(f"镜牢 ONNX 路线: 权重={min_weight}, 方向={directions}, 节点={node_types}")
    return floor_route, floor_map


def find_bus(take_screenshot=True):
    """定位 Bus；所在行由主题包流程和已选路线维护。"""
    bus_position = None
    for _ in range(3):
        bus_position = auto.find_element(
            "mirror/mybus_default_distance.png",
            take_screenshot=take_screenshot,
        )
        if bus_position:
            break
        sleep(0.5)
    if bus_position is None:
        return None
    return bus_position


def move_bus(bus_position, bus_row):
    """拖动地图，使三行节点进入稳定的识别区域。"""
    scale = cfg.set_win_size / REFERENCE_SCREEN_HEIGHT
    bus_x, bus_y = bus_position
    dx = 120 * scale - bus_x
    if bus_row is Position.UP:
        dy = 700 * scale - bus_y - Y_GAP * scale
    elif bus_row is Position.DOWN:
        dy = 700 * scale - bus_y + Y_GAP * scale
    else:
        dy = 700 * scale - bus_y

    auto.mouse_drag(bus_x, bus_y, drag_time=0.5, dx=dx, dy=dy)
    sleep(0.5)
    moved_bus_position = auto.find_element(
        "mirror/mybus_default_distance.png",
        take_screenshot=True,
    )
    if moved_bus_position is None:
        return None
    return moved_bus_position


def _save_shifted_onnx_images(capture_id):
    scale = cfg.set_win_size / REFERENCE_SCREEN_HEIGHT
    shifted = 0
    try:
        for index in range(1, 7):
            auto.mouse_drag(
                1600 * scale,
                700 * scale,
                drag_time=0.5,
                dx=-X_GAP * scale,
            )
            shifted += 1
            sleep(0.5)
            if auto.take_screenshot(gray=False) is None:
                raise MirrorPathfindingError("镜牢 ONNX 日志截图失败")
            auto.screenshot.save(Path("logs") / f"onnx_nodes_{capture_id}_shift_{index}.png")
    finally:
        for gap_count in (min(3, shifted), max(0, shifted - 3)):
            if not gap_count:
                continue
            auto.mouse_drag(
                400 * scale,
                700 * scale,
                drag_time=0.5,
                dx=gap_count * X_GAP * scale,
            )
            sleep(0.5)

        if auto.find_element(
            "mirror/mybus_default_distance.png",
            take_screenshot=True,
        ) is None:
            raise MirrorPathfindingError("镜牢 ONNX 日志采集后未找到 Bus")


def onnx(bus_row=Position.MID, save_shifted_images=False):
    bus_position = find_bus()
    if bus_position is None:
        return None

    bus_position = move_bus(bus_position, bus_row)
    if bus_position is None or auto.take_screenshot(gray=False) is None:
        return None

    capture_id = time.time_ns()
    Path("logs").mkdir(exist_ok=True)
    auto.screenshot.save(Path("logs") / f"onnx_nodes_{capture_id}.png")
    points = identify_nodes(bus_position[0], image=auto.screenshot)
    if save_shifted_images:
        _save_shifted_onnx_images(capture_id)
    return bus_position, points, capture_id


def identify_nodes(bus_x, image=None):
    """使用 ONNX 识别 Bus 右侧节点。"""
    import numpy as np
    import onnxruntime as ort

    classes = [
        "battle",
        "boss_battle",
        "event",
        "focused_encounter",
        "risky_encounter",
        "shop",
        "abnormality_focused_encounter",
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
        swapRB=True,
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
    min_x = bus_x + 50 * (cfg.set_win_size / REFERENCE_SCREEN_HEIGHT)
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


def generate_map(points, bus_position):
    """把 ONNX 节点转换为带连接关系的逻辑地图。"""
    if auto.take_screenshot(gray=True) is None:
        return {}
    nodes = _snap_points_to_grid(points, bus_position)
    _connect_visible_nodes(nodes)
    _append_shop_and_boss(nodes, bus_position)
    return nodes


def _snap_points_to_grid(points, bus_position):
    scale = cfg.set_win_size / REFERENCE_SCREEN_HEIGHT
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
    for (column, row), source in sorted(nodes.items()):
        for next_row in (row - 1, row, row + 1):
            target = nodes.get((column + 1, next_row))
            if target is not None and _connection_exists(source, target):
                source.add_next(target)


def _connection_exists(source, target):
    template = {-1: "up", 0: "mid", 1: "down"}.get(target.coord[1] - source.coord[1])
    if template is None:
        return False

    scale = cfg.set_win_size / REFERENCE_SCREEN_HEIGHT
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
            threshold=CONNECTION_MATCH_THRESHOLD,
            my_crop=crop,
            model="aggressive",
        )
    )


def _crop_around(position, x_radius, y_radius):
    width = cfg.set_win_size * 16 / 9
    height = cfg.set_win_size
    return (
        max(0, position[0] - x_radius),
        max(0, position[1] - y_radius),
        min(width, position[0] + x_radius),
        min(height, position[1] + y_radius),
    )


def _append_shop_and_boss(nodes, bus_position):
    """按镜牢固定末端结构补齐 Shop 和 Boss。"""
    last_column = max(column for column, _ in nodes)
    if last_column == 0:
        return
    last_nodes = [node for (column, _), node in nodes.items() if column == last_column]
    last_types = {node.type for node in last_nodes}
    scale = cfg.set_win_size / REFERENCE_SCREEN_HEIGHT

    if "boss_battle" in last_types:
        return
    if "shop" in last_types:
        shop_nodes = [node for node in last_nodes if node.type == "shop"]
        boss_column = last_column + 1
    else:
        shop_column = last_column + 1
        shop = Node(
            (shop_column, 0),
            "shop",
            (bus_position[0] + shop_column * X_GAP * scale, bus_position[1]),
            synthetic=True,
        )
        nodes[shop.coord] = shop
        for node in last_nodes:
            node.add_next(shop)
        shop_nodes = [shop]
        boss_column = shop_column + 1

    boss = Node(
        (boss_column, 0),
        "boss_battle",
        (bus_position[0] + boss_column * X_GAP * scale, bus_position[1]),
        synthetic=True,
    )
    nodes[boss.coord] = boss
    for shop in shop_nodes:
        shop.add_next(boss)


def find_min_weight_route(floor_map):
    start = floor_map.get((0, 0))
    if start is None:
        return float("inf"), []

    furthest_column = max(column for column, _ in floor_map)
    targets = {node for (column, _), node in floor_map.items() if column == furthest_column}
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
                heapq.heappush(queue, (new_total, id(next_node), next_node, route + [next_node]))
    return float("inf"), []


def route_to_directions(floor_route):
    directions = []
    for current_node, next_node in zip(floor_route, floor_route[1:]):
        direction = get_node_direction(current_node, next_node)
        if direction is None:
            return [], [node.type for node in floor_route]
        directions.append(direction)
    return directions, [node.type for node in floor_route]


def _save_pathfinding_logs(capture_id, floor_map, floor_route, min_weight, hard_mode):
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    nodes = [
        {
            "coord": list(node.coord),
            "type": node.type,
            "value": node.value,
            "screen_pos": list(node.screen_pos),
            "synthetic": node.synthetic,
        }
        for _, node in sorted(floor_map.items())
    ]
    connections = [
        {"from": list(source.coord), "to": list(target.coord)}
        for _, source in sorted(floor_map.items())
        for target in source.next
    ]
    route = {
        "hard_mode": hard_mode,
        "weight": None if min_weight == float("inf") else min_weight,
        "directions": route_to_directions(floor_route)[0],
        "nodes": [{"coord": list(node.coord), "type": node.type} for node in floor_route],
    }
    (log_dir / f"onnx_map_{capture_id}.json").write_text(
        json.dumps({"nodes": nodes, "connections": connections}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (log_dir / f"onnx_route_{capture_id}.json").write_text(
        json.dumps(route, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
