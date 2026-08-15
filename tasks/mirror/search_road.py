import heapq
from functools import lru_cache
from pathlib import Path
from time import sleep

import cv2
import numpy as np

from module.automation import auto
from module.config import cfg
from module.logger import log
from module.my_error.my_error import MirrorPathfindingError

REFERENCE_SCREEN_HEIGHT = 1440
X_GAP = 520
Y_GAP = 437
UP = -1
MID = 0
DOWN = 1
ROW_PRIORITY = {MID: 0, UP: 1, DOWN: 2}
CONNECTION_X_RADIUS = 150
CONNECTION_Y_RADIUS = 120
CONNECTION_MATCH_THRESHOLD = 0.75
ONNX_IMAGE_SIZE = 960
ONNX_CONFIDENCE_THRESHOLD = 0.4

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


class Node:
    def __init__(self, coord, node_type, screen_pos, synthetic=False):
        self.coord = coord
        self.type = node_type
        self.screen_pos = screen_pos
        self.value = NODE_WEIGHT.get(node_type, 999)
        self.synthetic = synthetic
        self.next = []

    def add_next(self, node):
        if node not in self.next:
            self.next.append(node)

    def __repr__(self):
        return f"Node(coord={self.coord}, type={self.type!r})"


class MirrorMap:
    """维护 Bus 行以及当前楼层可复用的最低权重路线。"""

    def __init__(self, floor=None, hard_mode=False):
        self.floor = floor
        self.hard_mode = hard_mode
        self.bus_row = None
        self.floor_route = []
        self.floor_map = {}

    def begin_floor(self):
        self.bus_row = MID
        self.floor_route = []
        self.floor_map = {}
        log.debug("选择主题包：Bus 所在行重置为 MID")

    def forget_bus_row(self):
        self.bus_row = None
        self.floor_route = []
        self.floor_map = {}

    def refresh_floor(self, floor):
        self.floor = floor

    def get_next_node_direction(self):
        if self.bus_row is None:
            raise MirrorPathfindingError("镜牢 Bus 所在行未知")

        if len(self.floor_route) < 2:
            route, floor_map = search_road_from_road_map(self.bus_row)
            if self.hard_mode:
                route = route[:2]
            if len(route) < 2:
                raise MirrorPathfindingError("镜牢节点图中不存在可达路线")
            self.floor_route = list(route)
            self.floor_map = floor_map

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

        next_row = self.bus_row + {"U": -1, "M": 0, "D": 1}[direction]
        self.bus_row = next_row if next_row in ROW_PRIORITY else None
        self.floor_route.pop(0)
        return True

    @staticmethod
    def _get_next_node_position(direction):
        bus_position = find_bus()
        if bus_position is None:
            return None
        scale = cfg.set_win_size / REFERENCE_SCREEN_HEIGHT
        dx, dy = {
            "U": (X_GAP * scale, -Y_GAP * scale),
            "M": (X_GAP * scale, 0),
            "D": (X_GAP * scale, Y_GAP * scale),
        }[direction]
        return bus_position[0] + dx, bus_position[1] + dy


def get_node_direction(current_node, next_node):
    return {-1: "U", 0: "M", 1: "D"}.get(next_node.coord[1] - current_node.coord[1])


def enter_current_node():
    """若 Bus 当前节点尚未完成，优先进入该节点。"""
    bus_position = find_bus()
    if bus_position is None:
        return False
    auto.mouse_click(*bus_position)
    sleep(1.25)
    if not auto.click_element("mirror/road_in_mir/enter_assets.png", take_screenshot=True):
        return False
    sleep(1.25)
    if _wait_for_map_exit():
        return True
    raise MirrorPathfindingError("无法确认已进入 Bus 当前节点")


def _enter_succeeded():
    for _ in range(5):
        if auto.click_element("mirror/road_in_mir/enter_assets.png", take_screenshot=True):
            sleep(1.25)
        if not auto.find_element("mirror/road_in_mir/legend_assets.png", take_screenshot=True):
            return True
        sleep(0.5)
    return False


def _wait_for_map_exit():
    for _ in range(5):
        if not auto.find_element("mirror/road_in_mir/legend_assets.png", take_screenshot=True):
            return True
        sleep(0.5)
    return False


def search_road_simple_keyboard():
    """默认寻路：始终通过键盘选择上方可用节点。"""
    auto.mouse_to_blank()
    sleep(0.3)
    for attempt in range(2):
        log.debug(f"默认键盘寻路：第 {attempt + 1} 次尝试")
        auto.key_press("up")
        sleep(1.25)
        auto.key_press("enter")
        sleep(1.25)
        if _enter_succeeded():
            return True
    return False


def search_road_from_road_map(bus_row):
    try:
        bus_position, points = onnx(bus_row)
        floor_map = generate_map(points, bus_position, bus_row)
        weight, route = find_min_weight_route(floor_map)
    except MirrorPathfindingError:
        raise
    except Exception as error:
        raise MirrorPathfindingError(f"镜牢智能建图失败: {error}") from error

    if len(route) < 2:
        raise MirrorPathfindingError("镜牢节点图中不存在可达路线")
    directions = [get_node_direction(current, target) for current, target in zip(route, route[1:])]
    log.info(f"镜牢智能路线：权重={weight}，方向={directions}，节点={[node.type for node in route]}")
    return route, floor_map


def find_bus(take_screenshot=True):
    for _ in range(3):
        position = auto.find_element(
            "mirror/mybus_default_distance.png",
            take_screenshot=take_screenshot,
        )
        if position:
            return position
        sleep(0.5)
    return None


def move_bus(bus_position):
    """把 Bus 移到左侧固定位置；mouse_drag 已在终点按住 0.5 秒再松开。"""
    scale = cfg.set_win_size / REFERENCE_SCREEN_HEIGHT
    target = (120 * scale, 700 * scale)
    dx = target[0] - bus_position[0]
    dy = target[1] - bus_position[1]
    if abs(dx) > 10 * scale or abs(dy) > 10 * scale:
        auto.mouse_drag(bus_position[0], bus_position[1], drag_time=0.5, dx=dx, dy=dy)
    return find_bus()


def onnx(bus_row):
    bus_position = find_bus()
    if bus_position is None:
        raise MirrorPathfindingError("无法定位镜牢 Bus")
    bus_position = move_bus(bus_position)
    if bus_position is None or auto.take_screenshot(gray=False) is None:
        raise MirrorPathfindingError("镜牢 ONNX 节点识别失败")
    return bus_position, identify_nodes(bus_position[0], auto.screenshot)


@lru_cache(maxsize=1)
def _get_onnx_session():
    import onnxruntime as ort

    return ort.InferenceSession(str(Path("assets/model/best.onnx")))


def identify_nodes(bus_x, image=None):
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
    image_scale = min(ONNX_IMAGE_SIZE / width, ONNX_IMAGE_SIZE / height)
    resized_width = round(width * image_scale)
    resized_height = round(height * image_scale)
    resized = cv2.resize(original[:, :, :3], (resized_width, resized_height))
    pad_x = (ONNX_IMAGE_SIZE - resized_width) // 2
    pad_y = (ONNX_IMAGE_SIZE - resized_height) // 2
    model_input = np.full((ONNX_IMAGE_SIZE, ONNX_IMAGE_SIZE, 3), 114, np.uint8)
    model_input[pad_y : pad_y + resized_height, pad_x : pad_x + resized_width] = resized
    blob = cv2.dnn.blobFromImage(
        model_input,
        scalefactor=1 / 255,
        size=(ONNX_IMAGE_SIZE, ONNX_IMAGE_SIZE),
        swapRB=False,
    )

    session = _get_onnx_session()
    outputs = session.run(None, {session.get_inputs()[0].name: blob})[0]
    outputs = cv2.transpose(outputs[0])

    boxes = []
    scores = []
    class_ids = []
    for output in outputs:
        _, max_score, _, (_, class_id) = cv2.minMaxLoc(output[4:])
        if max_score < ONNX_CONFIDENCE_THRESHOLD:
            continue
        boxes.append([output[0] - output[2] / 2, output[1] - output[3] / 2, output[2], output[3]])
        scores.append(float(max_score))
        class_ids.append(class_id)

    result = []
    min_x = bus_x + 50 * (cfg.set_win_size / REFERENCE_SCREEN_HEIGHT)
    for result_index in cv2.dnn.NMSBoxes(
        boxes,
        scores,
        ONNX_CONFIDENCE_THRESHOLD,
        0.4,
        0.5,
    ):
        index = int(np.asarray(result_index).reshape(-1)[0])
        x, y, width, height = (float(value) for value in boxes[index])
        center = (
            int((x + width / 2 - pad_x) / image_scale),
            int((y + height / 2 - pad_y) / image_scale),
        )
        if min_x <= center[0] < original.shape[1] and 0 <= center[1] < original.shape[0]:
            result.append([classes[class_ids[index]], center])
    return result


def generate_map(points, bus_position, bus_row):
    if auto.take_screenshot(gray=True) is None:
        raise MirrorPathfindingError("镜牢连线截图失败")
    nodes = _snap_points_to_grid(points, bus_position, bus_row)
    _connect_visible_nodes(nodes)
    _append_missing_terminal_nodes(nodes, bus_position)
    return nodes


def _snap_points_to_grid(points, bus_position, bus_row):
    scale = cfg.set_win_size / REFERENCE_SCREEN_HEIGHT
    x_gap = X_GAP * scale
    y_gap = Y_GAP * scale
    nodes = {(0, bus_row): Node((0, bus_row), "bus", bus_position)}

    for node_type, screen_pos in points:
        column = round((screen_pos[0] - bus_position[0]) / x_gap)
        row = bus_row + round((screen_pos[1] - bus_position[1]) / y_gap)
        if node_type in {"shop", "boss_battle"}:
            row = MID
        if column <= 0 or row not in ROW_PRIORITY:
            continue
        coord = (column, row)
        if coord not in nodes or node_type in {"shop", "boss_battle"}:
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
    crop = _crop_around(midpoint, CONNECTION_X_RADIUS * scale, CONNECTION_Y_RADIUS * scale)
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


def _append_missing_terminal_nodes(nodes, bus_position):
    """没有 Shop 时补 Shop+Boss；有 Shop 时只补缺少的 Boss。"""
    detected_shop = sorted(
        (node for node in nodes.values() if node.type == "shop"),
        key=lambda node: node.coord[0],
    )
    detected_boss = sorted(
        (node for node in nodes.values() if node.type == "boss_battle"),
        key=lambda node: node.coord[0],
    )
    scale = cfg.set_win_size / REFERENCE_SCREEN_HEIGHT

    if detected_shop:
        shop = detected_shop[-1]
        if any(boss.coord[0] > shop.coord[0] for boss in detected_boss):
            return
        boss = _synthetic_node("boss_battle", shop.coord[0] + 1, bus_position, scale)
        nodes[boss.coord] = boss
        shop.add_next(boss)
        return

    last_column = max(column for column, _ in nodes)
    if last_column == 0:
        return
    previous_nodes = [node for (column, _), node in nodes.items() if column == last_column]
    shop = _synthetic_node("shop", last_column + 1, bus_position, scale)
    boss = _synthetic_node("boss_battle", last_column + 2, bus_position, scale)
    nodes[shop.coord] = shop
    nodes[boss.coord] = boss
    for node in previous_nodes:
        node.add_next(shop)
    shop.add_next(boss)


def _synthetic_node(node_type, column, bus_position, scale):
    return Node(
        (column, MID),
        node_type,
        (bus_position[0] + column * X_GAP * scale, bus_position[1]),
        synthetic=True,
    )


def find_min_weight_route(floor_map):
    start = next((node for node in floor_map.values() if node.type == "bus"), None)
    bosses = [node for node in floor_map.values() if node.type == "boss_battle"]
    if start is None or not bosses:
        return float("inf"), []
    target_column = max(node.coord[0] for node in bosses)

    start_priority = (ROW_PRIORITY[start.coord[1]],)
    best = {start: (start.value, start_priority)}
    queue = [(start.value, start_priority, start.coord, start, [start])]
    while queue:
        total, priority, _, current, route = heapq.heappop(queue)
        if (total, priority) != best.get(current):
            continue
        if current.type == "boss_battle" and current.coord[0] == target_column:
            return total, route
        for target in sorted(current.next, key=lambda node: ROW_PRIORITY[node.coord[1]]):
            candidate = (
                total + target.value,
                priority + (ROW_PRIORITY[target.coord[1]],),
            )
            if candidate < best.get(target, (float("inf"), ())):
                best[target] = candidate
                heapq.heappush(queue, (*candidate, target.coord, target, route + [target]))
    return float("inf"), []
