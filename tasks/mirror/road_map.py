import heapq
import re
from datetime import datetime
from enum import Enum
from pathlib import Path
from time import sleep

import cv2

from module.automation import auto
from module.config import cfg
from module.logger import log

X_GAP = 520
Y_GAP = 437
VISIBLE_COLUMN_COUNT = 4
CONNECTION_X_RADIUS = 150
CONNECTION_Y_RADIUS = 120
ONNX_SCREENSHOT_DIR = Path("logs/mirror_analysis/screenshots")

NODE_WEIGHT = {
    "battle": 30,
    "boss_battle": 1,
    "event": 18,
    "hard_battle": 75,
    "hard_battle_2": 100,
    "shop": 1,
    "small_boss_battle": 999,
    "bus": 0,
}


class Position(Enum):
    UP = "up"
    MID = "mid"
    DOWN = "down"


class Node:
    """镜牢地图节点，保存逻辑坐标、节点类型、屏幕坐标、权重和后继节点。"""

    def __init__(
        self,
        coord,
        node_type,
        screen_pos,
        value=None,
        synthetic=False,
        theme_pack="",
        team_number=None,
        floor=None,
    ):
        self.coord = coord
        self.type = node_type
        self.screen_pos = screen_pos
        self.value = NODE_WEIGHT.get(node_type, 999) if value is None else value
        self.next = []
        self.synthetic = synthetic
        self.theme_pack = theme_pack
        self.team_number = team_number
        self.floor = floor
        # 从进入当前节点到进入下一个节点前所花费的时间。
        # 在下一个节点成功进入前，该值保持为 0。
        self.node_time = 0

    def add_next(self, next_node):
        if next_node not in self.next:
            self.next.append(next_node)

    def __repr__(self):
        next_coords = [node.coord for node in self.next]
        return (
            f"Node(coord={self.coord}, type={self.type!r}, "
            f"screen_pos={self.screen_pos}, value={self.value}, next={next_coords})"
        )


def _crop_around(position, x_radius, y_radius):
    """生成限制在游戏画面内的搜索区域。"""
    width = cfg.set_win_size * 16 / 9
    height = cfg.set_win_size
    return (
        max(0, position[0] - x_radius),
        max(0, position[1] - y_radius),
        min(width, position[0] + x_radius),
        min(height, position[1] + y_radius),
    )


def _node_marker_exists(position):
    """判断预期节点位置是否存在发光节点或事件节点。"""
    scale = cfg.set_win_size / 1440
    width = cfg.set_win_size * 16 / 9
    height = cfg.set_win_size
    if not (0 < position[0] < width and 0 < position[1] < height):
        return False
    crop = _crop_around(position, 125 * scale, 125 * scale)
    for target in ("mirror/road_in_mir/light.png", "mirror/road_in_mir/event.png"):
        if auto.find_element(target, threshold=0.75, my_crop=crop, model="aggressive"):
            return True
    return False


def find_bus(take_screenshot=True):
    """
    查找 bus 并判断其所在行。

    右上、右下节点同时存在或同时不存在时为中行；只有右上存在时
    为下行；只有右下存在时为上行。
    """

    bus_position = auto.find_element("mirror/mybus_default_distance.png", take_screenshot=True)
    if bus_position is None:
        log.warning("未找到镜牢 bus")
        return None

    scale = cfg.set_win_size / 1440
    light_positions = auto.find_element(
    "mirror/road_in_mir/light.png",
    find_type="image_with_multiple_targets",
    )

    event_positions = auto.find_element(
        "mirror/road_in_mir/event.png",
        find_type="image_with_multiple_targets",
    )
    up_exists = any(y < bus_position[1] - Y_GAP * scale / 2 for x, y in light_positions + event_positions)
    down_exists = any(y > bus_position[1] + Y_GAP * scale / 2 for x, y in light_positions + event_positions)

    if up_exists == down_exists:
        bus_row = Position.MID
    elif up_exists:
        bus_row = Position.DOWN
    else:
        bus_row = Position.UP

    log.info(
        f"bus 位置: {bus_position}, 所在行: {bus_row.value}, "
        f"右上节点: {up_exists}, 右下节点: {down_exists}"
    )
    return bus_position, bus_row


def move_bus(bus_position, bus_row):
    """按 bus 所在行拖动地图，使三行四列进入 ONNX 识别区域。"""
    scale = cfg.set_win_size / 1440
    bus_x, bus_y = bus_position
    dx = 120 * scale - bus_x
    if bus_row is Position.UP:
        dy = 780 * scale - bus_y - Y_GAP * scale
    elif bus_row is Position.MID:
        dy = 780 * scale - bus_y
    else:
        dy = 780 * scale - bus_y + Y_GAP * scale
    target = (bus_x + dx, bus_y + dy)

    if abs(dx) > 20 * scale or abs(dy) > 20 * scale:
        log.info(f"拖动 bus: {bus_position} -> {target}, 所在行: {bus_row.value}")
        auto.mouse_drag(bus_position[0], bus_position[1], drag_time=1.5, dx=dx, dy=dy)
        sleep(0.75)
        auto.mouse_to_blank()

    bus_position = auto.find_element("mirror/mybus_default_distance.png",take_screenshot=True)
    if bus_position is None:
        log.warning("拖动后无法重新定位 bus")
        return None
    return [bus_position, bus_row]


def _safe_filename_component(value, fallback="unknown"):
    """将镜牢上下文转换为可用于 Windows 文件名的文本。"""
    text = str(value).strip() if value is not None else ""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .")
    return text or fallback


def _save_onnx_screenshot(image, theme_pack="", floor=None):
    """保存实际送入 ONNX 的截图；失败时不影响寻路。"""
    theme_name = _safe_filename_component(theme_pack)
    floor_name = _safe_filename_component(floor)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output_path = ONNX_SCREENSHOT_DIR / (
        f"mirror_{theme_name}_floor_{floor_name}_{timestamp}.png"
    )
    try:
        ONNX_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        image.save(output_path, format="PNG")
        log.info(f"ONNX 输入截图已保存: {output_path}")
        return output_path
    except Exception as error:
        log.warning(f"保存 ONNX 输入截图失败: {error}")
        return None


def onnx(flow_watchdog=None, theme_pack="", floor=None):
    """完成 bus 定位和画面归一化，然后运行 ONNX 节点识别。"""
    bus_position, bus_row = find_bus()
    if bus_position is None or bus_row is None:
        return None
    if flow_watchdog is not None and not flow_watchdog.check():
        return None

    bus_position, bus_row = move_bus(bus_position, bus_row)

    if bus_position is None:
        return None
    if flow_watchdog is not None and not flow_watchdog.check():
        return None

    if auto.take_screenshot(gray=False) is None:
        log.warning("拖动 bus 后截图失败")
        return None
    _save_onnx_screenshot(auto.screenshot, theme_pack=theme_pack, floor=floor)
    points = identify_nodes(bus_position[0], image=auto.screenshot)
    if not points:
        log.warning("ONNX 未识别到镜牢节点")
        return None
    log.info(f"ONNX 节点识别结果（{len(points)}个）: {points}")
    return bus_position, bus_row, points


def identify_nodes(bus_x, image=None):
    """使用 ONNX 识别 bus 右侧节点，返回 [(类型, 屏幕中心坐标), ...]。"""
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
        square, scalefactor=1 / 255, size=(640, 640), swapRB=False
    )

    session = ort.InferenceSession("./assets/model/best.onnx")
    outputs = session.run(None, {session.get_inputs()[0].name: blob})[0]
    outputs = cv2.transpose(outputs[0])

    boxes = []
    scores = []
    class_ids = []
    raw_candidates = []
    for output in outputs:
        _, max_score, _, (_, class_id) = cv2.minMaxLoc(output[4:])
        raw_box = [
            output[0] - output[2] / 2,
            output[1] - output[3] / 2,
            output[2],
            output[3],
        ]
        raw_center = (
            int((raw_box[0] + raw_box[2] / 2) * image_scale),
            int((raw_box[1] + raw_box[3] / 2) * image_scale),
        )
        screen_box = tuple(int(value * image_scale) for value in raw_box)
        raw_candidates.append(
            (float(max_score), classes[class_id], raw_center, screen_box)
        )
        if max_score < 0.25:
            continue
        boxes.append(raw_box)
        scores.append(float(max_score))
        class_ids.append(class_id)

    top_candidates = [
        [node_type, round(score, 4), center, box]
        for score, node_type, center, box in sorted(
            raw_candidates, key=lambda candidate: candidate[0], reverse=True
        )[:50]
    ]
    log.info(
        "ONNX 过滤前置信度前%d候选框（类别, 置信度, 中心坐标, 边框x/y/w/h）: %s",
        len(top_candidates),
        top_candidates,
    )

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


def _snap_points_to_grid(points, bus_position):
    """将 ONNX 像素坐标归一化到以 bus 为原点的四列网格。"""
    scale = cfg.set_win_size / 1440
    x_gap = X_GAP * scale
    y_gap = Y_GAP * scale
    bus_position, bus_row = find_bus()
    nodes = {(0, 0): Node((0, 0), "bus", bus_position, value=0)}

    for node_type, screen_pos in points:
        column = round((screen_pos[0] - bus_position[0]) / x_gap)
        row = round((screen_pos[1] - bus_position[1]) / y_gap)

        coord = (column, row)
        if coord not in nodes:
            nodes[coord] = Node(coord, node_type, screen_pos)
    return nodes


def _connection_exists(source, target):
    """在两节点中点附近匹配与目标行差对应的 up/mid/down 模板。"""
    template = {-1: "up", 0: "mid", 1: "down"}.get(
        target.coord[1] - source.coord[1]
    )
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
            take_screenshot=True,
        )
    )


def _connect_visible_nodes(nodes):
    """只检查 (x+1, y-1/y/y+1) 三种可能的下一列节点。"""
    for (column, row), source in sorted(nodes.items()):
        for next_row in (row - 1, row, row + 1):
            target = nodes.get((column + 1, next_row))
            if target is not None and _connection_exists(source, target):
                source.add_next(target)


def _append_shop_and_boss(nodes, bus_position):
    """四列地图后按固定中线规则补齐 shop 和 boss。"""
    columns = sorted({coord[0] for coord in nodes})
    if len(columns) < VISIBLE_COLUMN_COUNT:
        log.warning(f"当前地图只有 {len(columns)} 列，不自动补 shop/boss")
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


def _log_map(nodes):
    node_output = [
        {
            "coord": node.coord,
            "type": node.type,
            "screen_pos": tuple(round(value, 1) for value in node.screen_pos),
            "value": node.value,
            "theme_pack": node.theme_pack,
            "team_number": node.team_number,
            "floor": node.floor,
            "node_time": node.node_time,
            "next": [next_node.coord for next_node in node.next],
        }
        for _, node in sorted(nodes.items())
    ]
    route_output = [
        {"source": node.coord, "target": next_node.coord}
        for _, node in sorted(nodes.items())
        for next_node in node.next
    ]
    log.info(f"镜牢完整节点图（{len(node_output)}个）: {node_output}")
    log.info(f"镜牢完整路线（{len(route_output)}条）: {route_output}")


def generate_map(points, bus_position, theme_pack="", team_number=None, floor=None):
    """根据 ONNX 节点生成包含所有节点及连线的地图。"""
    nodes = _snap_points_to_grid(points, bus_position)
    _connect_visible_nodes(nodes)
    _append_shop_and_boss(nodes, bus_position)
    for node in nodes.values():
        node.theme_pack = theme_pack
        node.team_number = team_number
        node.floor = floor
    _log_map(nodes)
    return nodes


def path(nodes):
    """计算从 bus 到 boss（或最远可见列）的最低总权重路径。"""
    start = nodes.get((0, 0))
    if start is None:
        return float("inf"), []

    targets = {node for node in nodes.values() if node.type == "boss_battle"}
    if not targets:
        furthest_column = max(coord[0] for coord in nodes)
        targets = {node for coord, node in nodes.items() if coord[0] == furthest_column}

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


def path_to_result(route):
    directions = []
    for current, next_node in zip(route, route[1:]):
        row_delta = next_node.coord[1] - current.coord[1]
        directions.append({-1: "U", 0: "M", 1: "D"}[row_delta])
    return directions, [node.type for node in route]
