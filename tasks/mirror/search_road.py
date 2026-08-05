import heapq
import time
from enum import Enum
from time import sleep

import cv2

from module.automation import auto
from module.config import cfg
from module.logger import log
from module.my_error.my_error import InputAttributeError
from tasks.base.retry import retry

# 图片匹配参数基于 2560×1440 游戏截图标定。
REFERENCE_SCREEN_HEIGHT = 1440
ROAD_ROW_GAP = 437
CONNECTION_X_RADIUS = 150
CONNECTION_Y_RADIUS = 120
CONNECTION_MATCH_THRESHOLD = 0.75
ROAD_TEMPLATE_BY_ROW_DELTA = {1: "up", 0: "mid", -1: "down"}


class MirrorMap:
    def __init__(self, floor=1, hard_mode=False):
        self.floor = floor
        self.floor_map = []
        self.map = {}
        self.hard_mode = hard_mode

    def get_next_step(self):
        re_identify = False
        if len(self.floor_map) > 0:
            next_step = self.floor_map.pop(0)
            if next_step is not None:
                return next_step
            else:
                re_identify = True
        else:
            re_identify = True

        if re_identify is True:
            self.floor_map, self.floor_nodes = search_road_from_road_map(hard_mode=self.hard_mode)
            if self.floor_map is True and self.floor_nodes is True:
                return True
            if not isinstance(self.floor_map, list):
                self.floor_map = list(self.floor_map)
            self.map[f"floor{self.floor}"] = [self.floor_map[:], self.floor_nodes[:]]

        if len(self.floor_map) > 0:
            next_step = self.floor_map.pop(0)
            return next_step
        else:
            return False

    def enter_next_node(self, next_step):
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
            return _keyboard_enter_succeeded()

        if next_position := self._get_next_position(next_step):
            auto.mouse_click(next_position[0], next_position[1])
            sleep(1.25)
            if auto.click_element("mirror/road_in_mir/enter_assets.png", take_screenshot=True):
                return True
        if auto.click_element("mirror/mybus_default_distance.png", take_screenshot=True):
            sleep(1.25)
            if auto.click_element("mirror/road_in_mir/enter_assets.png", take_screenshot=True):
                return True
        return False

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
    elif auto.find_feature_element("mirror/road_in_mir/risky_encounter.png", road_node_bbox):
        return 1
    elif auto.find_feature_element("mirror/road_in_mir/focused_encounter.png", road_node_bbox):
        return 0
    return -5


def _keyboard_enter_succeeded() -> bool:
    """检测键盘寻路按键后是否成功进入下一节点。

    成功条件：点击到"进入"按钮，或地图图例消失（已离开节点选择界面）。
    """
    if auto.click_element("mirror/road_in_mir/enter_assets.png", take_screenshot=True):
        return True
    if not auto.find_element("mirror/road_in_mir/legend_assets.png"):
        return True
    return False


# 简单键盘寻路：始终按↑选择第一个节点，完全避免鼠标拖动
def search_road_simple_keyboard():
    """最简单寻路策略：不进行路线规划/相机对齐/节点识别，仅按↑键选择第一个节点后回车。

    适用于 Steam 环境下鼠标拖动地图导致卡死的场景，依赖 mirror_keyboard_navigation。
    """
    if not cfg.mirror_keyboard_navigation:
        log.warning("简单键盘寻路需要启用键盘寻路模式")
        return False

    auto.mouse_to_blank()
    sleep(0.3)

    for attempt in range(2):
        log.debug(f"简单键盘寻路: 第 {attempt + 1} 次尝试按↑+回车")
        auto.key_press("up")
        sleep(0.5)
        auto.key_press("enter")
        sleep(1.25)

        if _keyboard_enter_succeeded():
            return True

    log.debug("简单键盘寻路失败，需回退到常规寻路")
    return False


# 在默认缩放情况下，进行镜牢寻路
def search_road_default_distance():
    start_time = time.time()
    scale = cfg.set_win_size / 1440
    three_roads = [
        [500 * scale, 50 * scale],
        [500 * scale, 450 * scale],
        [500 * scale, -400 * scale],
    ]

    auto.mouse_to_blank()
    while auto.take_screenshot() is None:
        continue
    if retry() is False:
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
def search_road_farthest_distance():
    scale = cfg.set_win_size / 1440
    auto.mouse_click_blank()
    if not auto.mouse_scroll():
        raise InputAttributeError("后台输入不支持滚轮操作!")
    while auto.take_screenshot() is None:
        continue
    if retry() is False:
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


def search_road_from_road_map(hard_mode=False):
    start_time = time.time()
    scale = cfg.set_win_size / 1440
    bus = None

    if auto.click_element("mirror/mybus_default_distance.png", take_screenshot=True):
        sleep(0.75)
        if auto.click_element("mirror/road_in_mir/enter_assets.png", take_screenshot=True):
            return True, True

    if bus_position := auto.find_element("mirror/mybus_default_distance.png", take_screenshot=True):
        from tasks.base.retry import check_times

        change_times = 5
        while True:
            if auto.get_restore_time() is not None:
                start_time = max(start_time, auto.get_restore_time())
            if check_times(start_time, logs=False):
                from tasks.base.back_init_menu import back_init_menu

                back_init_menu()
                return False, []
            if 675 * scale < bus_position[1] < 700 * scale and 150 * scale > bus_position[0]:
                bus = bus_position
                break
            dx = 80 * scale - bus_position[0]
            dy = 690 * scale - bus_position[1]
            auto.mouse_drag(bus_position[0], bus_position[1], drag_time=1.5, dx=dx, dy=dy)
            sleep(0.5)
            auto.mouse_to_blank()

            bus_position = auto.find_element("mirror/mybus_default_distance.png", take_screenshot=True)
            if bus_position is None:
                break
            change_times -= 1
            if change_times <= 0:
                bus = bus_position
                break

    bus_pos = auto.find_element("mirror/mybus_default_distance.png")
    all_nodes = identify_nodes(bus[0])
    y_area = divide_the_area_by_y(all_nodes)
    reset_position = False
    initial_bus_pos = Position.MID
    if len(y_area) == 2:
        if bus_pos[1] > y_area[0][0][1][1] + 50 * scale:
            reset_position = "Bottom"
            initial_bus_pos = Position.BOTTOM
        else:
            reset_position = "Top"
            initial_bus_pos = Position.TOP
    elif len(y_area) == 1:
        all_nodes_layer = divide_the_area_by_x(all_nodes)
        connections = identify_road(bus, all_nodes_layer[:1], initial_bus_pos)
        if not connections:
            return [], []

        _, source_position, target_position = connections[0]
        row_delta = target_position.value - source_position.value
        return [{1: "U", 0: "M", -1: "D"}[row_delta]], ["unknown"]
    if reset_position is not False:
        if reset_position == "Bottom":
            set_y_position = 1100 * scale
        else:
            set_y_position = 250 * scale
        if bus_position := auto.find_element("mirror/mybus_default_distance.png", take_screenshot=True):
            from tasks.base.retry import check_times

            while True:
                if auto.get_restore_time() is not None:
                    start_time = max(start_time, auto.get_restore_time())
                if check_times(start_time, logs=False):
                    from tasks.base.back_init_menu import back_init_menu

                    back_init_menu()
                    return False, []
                if (
                    set_y_position - 50 * scale < bus_position[1] < set_y_position + 50 * scale
                    and 500 * scale < bus_position[0] < 600 * scale
                ):
                    bus = bus_position
                    break
                dx = 550 * scale - bus_position[0]
                dy = set_y_position - bus_position[1]
                auto.mouse_drag(bus_position[0], bus_position[1], drag_time=1.5, dx=dx, dy=dy)
                sleep(0.5)
                auto.mouse_to_blank()

                bus_position = auto.find_element("mirror/mybus_default_distance.png", take_screenshot=True)
                if bus_position is None:
                    break
        all_nodes = identify_nodes(bus[0])

    all_nodes_layer = divide_the_area_by_x(all_nodes)
    connections = identify_road(bus, all_nodes_layer, initial_bus_pos)

    route_graph = RouteGraph(
        all_nodes_layer,
        initial_bus_pos=initial_bus_pos,
        bus_position=bus,
        hard_mode=hard_mode,
    )
    route_graph.init_road(connections)

    min_weight, path = route_graph.find_min_weight_route()

    if path:
        # 生成方向列表
        directions, road_class_list = route_graph.get_path_directions(path)
        log.debug(f"最小权重: {min_weight}")
        log.debug(f"路径方向: {directions}")
        log.debug(f"行走路径: {road_class_list}")
        return directions, road_class_list
    else:
        log.warning("未能检测到有效路径")

    return [], []


# battle 是常规遭遇战，boss_battle 是 Boss 战，event 是事件，focused_encounter 是集中遭遇战（链式战）
# risky_encounter 是精锐遭遇战（链式战），shop 是商店，abnormality_focused_encounter 是异想体集中遭遇战


def identify_nodes(bus_x):
    import numpy as np
    import onnxruntime as ort

    # 定义检测目标的类别标签（与模型训练时的类别一致）
    CLASSES = [
        "battle",
        "boss_battle",
        "event",
        "focused_encounter",
        "risky_encounter",
        "shop",
        "abnormality_focused_encounter",
    ]

    no_flag = False  # 标记是否检测到目标（初始为 False，未检测到时设为 True）

    # 加载 ONNX 格式的目标检测模型
    session = ort.InferenceSession("./assets/model/best.onnx")

    # 读取原始图像（BGR 格式，由 OpenCV 读取）
    auto.take_screenshot(gray=False)
    auto.screenshot.save("onnx_node_screenshot.png")
    original_image: np.ndarray = np.array(auto.screenshot)
    [height, width, _] = original_image.shape  # 获取原始图像的高、宽、通道数

    # 创建正方形空白图像（边长为原始图像的最大边），用于保持图像比例并避免变形
    length = max((height, width))  # 正方形边长取原始图像的高或宽的最大值
    image = np.zeros((length, length, 3), np.uint8)  # 初始化全黑正方形图像
    image[0:height, 0:width] = original_image  # 将原始图像粘贴到正方形的左上角区域

    # 计算缩放比例（正方形边长 → 模型输入尺寸 640 的缩放因子）
    scale = length / 640

    # 将图像转换为模型所需的输入格式（blob）
    # blobFromImage 参数说明：
    # - image: 输入图像（正方形）
    # - scalefactor=1/255: 像素值归一化（0-255 → 0-1）
    # - size=(640, 640): 模型输入的尺寸（宽高均为 640）
    # - swapRB=True: 交换 RGB 通道（OpenCV 读取的是 BGR，模型可能需要 RGB）
    blob = cv2.dnn.blobFromImage(image, scalefactor=1 / 255, size=(640, 640), swapRB=True)

    # 执行模型推理（输入为 blob）
    outputs = session.run(None, {session.get_inputs()[0].name: blob})  # 输出为模型预测结果

    outputs = outputs[0]  # 提取第一个输出（YOLO 通常输出一个包含所有检测结果的数组）
    outputs = np.array([cv2.transpose(outputs[0])])  # 转置维度（适配后续处理逻辑）
    rows = outputs.shape[1]  # 获取检测结果的数量（每行对应一个目标的预测信息）

    boxes = []  # 存储边界框坐标（格式：[x_center, y_center, width, height]）
    scores = []  # 存储检测置信度
    class_ids = []  # 存储类别 ID

    # 遍历所有检测结果（每行对应一个目标的预测信息）
    for i in range(rows):
        # 提取类别置信度（前 4 列是边界框坐标，第 5 列及之后是各分类得分）
        classes_scores = outputs[0][i][4:]

        # 找到当前目标的最大类别置信度及其对应的类别索引
        (minScore, maxScore, minClassLoc, (x, maxClassIndex)) = cv2.minMaxLoc(classes_scores)

        # 若最大置信度超过阈值（0.25），则保留该检测结果
        if maxScore >= 0.25:
            # 计算边界框的左上角坐标和宽高（YOLO 输出为中心点坐标 + 宽高，需转换）
            box = [
                outputs[0][i][0] - (0.5 * outputs[0][i][2]),  # 左上角 x = 中心点 x - 半宽
                outputs[0][i][1] - (0.5 * outputs[0][i][3]),  # 左上角 y = 中心点 y - 半高
                outputs[0][i][2],  # 宽度（中心点 x 到右边界点的距离）
                outputs[0][i][3],  # 高度（中心点 y 到下边界点的距离）
            ]
            boxes.append(box)  # 保存边界框
            scores.append(maxScore)  # 保存置信度
            class_ids.append(maxClassIndex)  # 保存类别 ID

    # 使用 NMS 抑制重叠的边界框（保留置信度高的框）
    # 参数说明：
    # - boxes: 边界框列表（格式：[x1, y1, w, h]）
    # - scores: 置信度列表
    # - score_threshold=0: 置信度阈值（此处未过滤低分，因前面已过滤）
    # - nms_threshold=0.4: 重叠框的交并比（IoU）阈值（>0.4 则抑制）
    result_boxes = cv2.dnn.NMSBoxes(boxes, scores, 0, 0.4, 0.5)

    detections = []  # 存储最终的检测结果（字典列表）

    if len(result_boxes) > 0:  # 若有有效检测结果
        for i in range(len(result_boxes)):
            index = result_boxes[i]  # 获取当前框在原始列表中的索引（NMS 输出为二维数组）
            box = boxes[index]  # 获取对应的边界框

            # 构造检测结果字典（包含类别、置信度、边界框等信息）
            detection = {
                "class_id": class_ids[index],
                "class_name": CLASSES[class_ids[index]],
                "confidence": scores[index],
                "box": box,  # 原始边界框（基于 640x640 输入尺寸）
                "scale": scale,  # 缩放比例（用于还原到原始图像尺寸）
            }
            detections.append(detection)  # 添加到结果列表
    else:
        no_flag = True  # 无检测结果时标记为 True

    if no_flag:
        return None

    node_list = []

    # 遍历每个字典并处理
    for d in detections:
        # 提取class_name
        class_name = d["class_name"]

        # 提取box并计算中心点（转换为Python浮点数）
        box = d["box"]
        x1 = box[0].item()  # 左上角x（转换为Python float）
        y1 = box[1].item()  # 左上角y（转换为Python float）
        w = box[2].item()  # 宽度（转换为Python float）
        h = box[3].item()  # 高度（转换为Python float）
        center_x = int((x1 + w / 2) * scale)
        center_y = int((y1 + h / 2) * scale)

        if center_x < bus_x + 50:
            continue

        # 组成子列表并添加到节点总列表
        node_list.append([class_name, (center_x, center_y)])  # 中心点用元组存储，也可改为列表

    return node_list


def identify_road(bus_position, all_nodes_layer, initial_bus_pos):
    """返回相邻节点列中经模板确认的连接。"""
    if not all_nodes_layer or auto.take_screenshot() is None:
        return []

    scale = cfg.set_win_size / REFERENCE_SCREEN_HEIGHT
    source_layers = [[["bus", bus_position]], *all_nodes_layer[:-1]]
    connections = []

    for layer_number, (source_nodes, target_nodes) in enumerate(
        zip(source_layers, all_nodes_layer),
        start=1,
    ):
        for source in source_nodes:
            source_position = (
                initial_bus_pos
                if layer_number == 1
                else _position_from_y(source[1][1], bus_position, initial_bus_pos)
            )

            for target in target_nodes:
                target_position = _position_from_y(target[1][1], bus_position, initial_bus_pos)
                if source_position is None or target_position is None:
                    continue

                row_delta = target_position.value - source_position.value
                template = ROAD_TEMPLATE_BY_ROW_DELTA.get(row_delta)
                if template is None:
                    continue

                midpoint = (
                    (source[1][0] + target[1][0]) / 2,
                    (source[1][1] + target[1][1]) / 2,
                )

                crop = (
                    midpoint[0] - CONNECTION_X_RADIUS * scale,
                    midpoint[1] - CONNECTION_Y_RADIUS * scale,
                    midpoint[0] + CONNECTION_X_RADIUS * scale,
                    midpoint[1] + CONNECTION_Y_RADIUS * scale,
                )

                if auto.find_element(
                    f"mirror/road_in_mir/{template}.png",
                    threshold=CONNECTION_MATCH_THRESHOLD,
                    my_crop=crop,
                    model="aggressive",
                ):
                    connections.append((layer_number, source_position, target_position))

    return connections


def _position_from_y(y, bus_position, initial_bus_pos):
    """把屏幕 Y 坐标映射到相对 Bus 的逻辑行。"""
    y_gap = ROAD_ROW_GAP * cfg.set_win_size / REFERENCE_SCREEN_HEIGHT
    position_value = initial_bus_pos.value + round((bus_position[1] - y) / y_gap)

    try:
        return Position(position_value)
    except ValueError:
        return None


def divide_the_area_by_y(data):
    # 步骤1：按y坐标从小到大排序（确保相近的y相邻）
    sorted_by_y = sorted(data, key=lambda item: item[1][1])  # item[1]是坐标元组，item[1][1]是y值

    # 步骤2：分组（y相近的归为一组，阈值可根据需求调整）
    tolerance = 20  # y差值小于等于20视为相近（可根据实际数据调整）
    groups = []
    for item in sorted_by_y:
        current_y = item[1][1]
        if not groups:
            # 第一个元素，新建组
            groups.append([item])
        else:
            # 检查当前元素与最后一个组的最后一个元素的y差值
            last_group_last_y = groups[-1][-1][1][1]
            if current_y - last_group_last_y <= tolerance:
                # 加入最后一个组
                groups[-1].append(item)
            else:
                # 新建组
                groups.append([item])
    return groups


def divide_the_area_by_x(data):
    # 步骤1：按x坐标从小到大排序（确保相近的x相邻）
    sorted_by_x = sorted(data, key=lambda item: item[1][0])

    # 步骤2：分组（x相近的归为一组，阈值可根据需求调整）
    tolerance = 80  # x差值小于等于tolerance视为相近
    groups = []
    for item in sorted_by_x:
        current_x = item[1][0]
        if not groups:
            # 第一个元素，新建组
            groups.append([item])
        else:
            # 检查当前元素与最后一个组的最后一个元素的x差值
            last_group_last_x = groups[-1][-1][1][0]
            if current_x - last_group_last_x <= tolerance:
                # 加入最后一个组
                groups[-1].append(item)
            else:
                # 新建组
                groups.append([item])

    # 步骤3：每个组内按y坐标从小到大排序
    for group in groups:
        group.sort(key=lambda item: item[1][1])

    log.debug(f"识别到的节点/线段分组后：{groups}")

    return groups


all_node_weight = {
    "battle": 4,
    "boss_battle": 6,
    "event": 1,
    "focused_encounter": 6,
    "risky_encounter": 7,
    "shop": 2,
    "abnormality_focused_encounter": 6,
}

DEFAULT_WEIGHT = 999  # 默认不可达权重


class Position(Enum):
    TOP = 1
    MID = 0
    BOTTOM = -1


class Node:
    def __init__(self, node_class: str = None, weight: float = DEFAULT_WEIGHT):
        self.node_class = node_class  # 节点标识
        self.weight = weight  # 节点权重
        self.next_nodes = []  # 指向的下一层节点列表（Node对象）

    def add_next_node(self, next_node) -> None:
        """添加下一层节点（自动去重）"""
        if next_node not in self.next_nodes:
            self.next_nodes.append(next_node)

    def __repr__(self):
        return f"Node({self.node_class}, 权重={self.weight}, 指向={self.next_nodes})"


class RouteGraph:
    def __init__(
        self,
        all_nodes: list,
        initial_bus_pos,
        bus_position,
        hard_mode=False,
    ):
        """初始化三行路线图；连线由 init_road() 写入。"""
        self.initial_bus_pos = initial_bus_pos
        self.layer_nums = 0
        self.layers = {}
        self._add_new_layer()
        self._set_node(1, initial_bus_pos, "bus", 1)
        self.hard_mode = hard_mode
        self._init_node(all_nodes, bus_position)

    def _add_new_layer(self):
        self.layers[f"layer{self.layer_nums + 1}"] = {
            Position.TOP: Node(),
            Position.MID: Node(),
            Position.BOTTOM: Node(),
        }
        self.layer_nums += 1

    def _set_node(self, layer_nums, position, class_name, weight):
        this_layer = self.layers[f"layer{layer_nums}"]
        this_layer[position].node_class = class_name
        this_layer[position].weight = weight

    def _init_node(self, all_nodes, bus_position):
        for layer_data in all_nodes:
            self._add_new_layer()
            for node_entry in layer_data:
                vertical_pos = _position_from_y(node_entry[1][1], bus_position, self.initial_bus_pos)
                if vertical_pos is None:
                    continue
                self._set_node(
                    self.layer_nums,
                    vertical_pos,
                    node_entry[0],
                    all_node_weight[node_entry[0]],
                )

        if self.hard_mode is False:
            exit_flag = False
            for j in [Position.TOP, Position.MID, Position.BOTTOM]:
                if self.layers[f"layer{self.layer_nums}"][j].node_class in [
                    "shop",
                    "boss_battle",
                ]:
                    exit_flag = True
                    break
            if exit_flag is False:
                self._add_new_layer()
                self._set_node(self.layer_nums, Position.MID, "shop", 1)
                for j in [Position.TOP, Position.MID, Position.BOTTOM]:
                    self.layers[f"layer{self.layer_nums - 1}"][j].add_next_node(
                        self.layers[f"layer{self.layer_nums}"][Position.MID]
                    )

            exit_flag = False
            for j in [Position.TOP, Position.MID, Position.BOTTOM]:
                if self.layers[f"layer{self.layer_nums}"][j].node_class in ["boss_battle"]:
                    exit_flag = True
                    break
            if exit_flag is False:
                self._add_new_layer()
                self._set_node(self.layer_nums, Position.MID, "boss_battle", 1)
                for j in [Position.TOP, Position.MID, Position.BOTTOM]:
                    self.layers[f"layer{self.layer_nums - 1}"][j].add_next_node(
                        self.layers[f"layer{self.layer_nums}"][Position.MID]
                    )

    def init_road(self, connections):
        """写入图片匹配确认的连线。"""
        for layer_number, source_position, target_position in connections:
            if self.hard_mode and layer_number > 2:
                continue
            source = self.layers[f"layer{layer_number}"][source_position]
            target = self.layers[f"layer{layer_number + 1}"][target_position]
            source.add_next_node(target)

    def get_node_layer_info(self, node: Node) -> tuple:
        """辅助方法：获取节点所在的层号、层内位置"""
        for layer_key, layer_nodes in self.layers.items():
            for pos, n in layer_nodes.items():
                if n == node:
                    layer_number = int(layer_key.replace("layer", ""))
                    return layer_key, layer_number, pos
        return None, None, None

    def find_min_weight_route(self) -> tuple[float, list[Node]]:
        """
        使用Dijkstra算法计算从入口到出口的最小权重路径
        返回：(最小总权重, 路径节点列表)
        """
        # 确定起点节点（layer1的初始公交位置）
        start_node = self.layers["layer1"][self.initial_bus_pos]

        # 收集所有终点节点（boss_battle）
        end_nodes = []
        for layer in self.layers.values():
            for pos_node in layer.values():
                if pos_node.node_class in ["boss_battle"]:
                    end_nodes.append(pos_node)

        if not end_nodes:
            # 确定目标层：至多三层，取当前最大层（不超过3）
            current_max_layer = self.layer_nums
            target_layer_num = min(current_max_layer, 3)
            target_layer = f"layer{target_layer_num}"

            # 检查目标层是否存在
            if target_layer not in self.layers:
                return float("inf"), []  # 目标层不存在，无法到达

            # 收集目标层的所有节点
            target_nodes = list(self.layers[target_layer].values())
            if not target_nodes:
                return float("inf"), []  # 目标层无节点，无法到达

            # 初始化距离字典，所有节点初始距离为无穷大，起点距离为自身权重
            distances = {
                node: float("inf")
                for layer in self.layers.values()
                for pos_node in layer.values()
                for node in [pos_node]
            }
            distances[start_node] = start_node.weight

            # 优先队列：(当前总权重, 节点唯一标识（避免比较Node）, 当前节点, 路径列表)
            heap = []
            heapq.heappush(heap, (start_node.weight, id(start_node), start_node, [start_node]))

            # 记录已处理的节点
            processed = set()

            min_total = float("inf")
            min_path = []

            while heap:
                current_total, _, current_node, current_path = heapq.heappop(heap)

                if current_node in processed:
                    continue
                processed.add(current_node)

                # 检查是否是目标节点（目标层的节点）
                if current_node in target_nodes:
                    # 更新最小路径
                    if current_total < min_total:
                        min_total = current_total
                        min_path = current_path.copy()

                # 遍历所有邻接节点
                for next_node in current_node.next_nodes:
                    if next_node in processed:
                        continue  # 已处理过，跳过

                    new_total = current_total + next_node.weight
                    new_path = current_path + [next_node]

                    # 如果找到更短路径，更新距离并加入队列
                    if new_total < distances[next_node]:
                        distances[next_node] = new_total
                        heapq.heappush(heap, (new_total, id(next_node), next_node, new_path))

            # 返回找到的最小路径，若没有则返回无穷大和空列表
            return (min_total, min_path) if min_total != float("inf") else (float("inf"), [])

        # 初始化距离字典，所有节点初始距离为无穷大，起点距离为自身权重
        distances = {
            node: float("inf") for layer in self.layers.values() for pos_node in layer.values() for node in [pos_node]
        }
        distances[start_node] = start_node.weight

        # 优先队列：(当前总权重, 节点唯一标识（避免比较Node）, 当前节点, 路径列表)
        heap = []
        heapq.heappush(heap, (start_node.weight, id(start_node), start_node, [start_node]))

        # 记录已处理的节点（优化：当节点第一次弹出时，已找到最短路径）
        processed = set()

        while heap:
            current_total, _, current_node, current_path = heapq.heappop(heap)  # 忽略辅助标识

            if current_node in processed:
                continue
            processed.add(current_node)

            # 到达终点，返回结果
            if current_node in end_nodes:
                return current_total, current_path

            # 遍历所有邻接节点
            for next_node in current_node.next_nodes:
                if next_node in processed:
                    continue  # 已处理过，跳过

                new_total = current_total + next_node.weight
                new_path = current_path + [next_node]

                # 如果找到更短路径，更新并加入队列
                if new_total < distances[next_node]:
                    distances[next_node] = new_total
                    # 添加辅助标识（id(next_node)）确保堆能正确排序
                    heapq.heappush(heap, (new_total, id(next_node), next_node, new_path))

        # 无可达路径
        return float("inf"), []

    def get_path_directions(self, path: list[Node]) -> tuple[list[str], list[str]]:
        """
        根据路径节点列表生成移动方向列表（U/D/M）和节点类别列表
        U: 下一个节点在当前节点上方，D: 下方，M: 同一层
        返回：(方向列表, 节点类别列表)
        """
        directions = []
        # 提取路径中所有节点的类别
        class_list = [node.node_class for node in path]

        if len(path) < 2:
            return directions, class_list  # 路径长度不足，无方向，但仍返回类别列表

        for i in range(len(path) - 1):
            current_node = path[i]
            next_node = path[i + 1]

            # 获取当前节点和下一个节点的层内位置
            _, _, current_pos = self.get_node_layer_info(current_node)
            _, _, next_pos = self.get_node_layer_info(next_node)

            if next_pos.value > current_pos.value:
                directions.append("U")  # 下一层更上层
            elif next_pos.value < current_pos.value:
                directions.append("D")  # 下一层更下层
            else:
                directions.append("M")  # 同一层

        return directions, class_list
