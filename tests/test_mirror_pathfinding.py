from tasks.mirror import mirror as mirror_module
from tasks.mirror.mirror import Mirror
from tasks.mirror.search_road import (
    MID,
    ONNX_CONFIDENCE_THRESHOLD,
    ONNX_IMAGE_SIZE,
    MirrorMap,
    Node,
    _append_missing_terminal_nodes,
    _get_onnx_session,
    find_min_weight_route,
)


def test_floor_detection_reclicks_setting_until_page_opens(monkeypatch):
    mirror = Mirror.__new__(Mirror)
    mirror.floor = None
    mirror.get_floor_num = True
    mirror.floor_times = [-9999.0] * 5
    refreshed_floors = []
    mirror.mirror_map = type(
        "MirrorMapStub",
        (),
        {"refresh_floor": lambda _, floor: refreshed_floors.append(floor)},
    )()
    to_window_results = iter((None, None, (1000, 500)))
    setting_clicks = []

    def find_element(target, **_kwargs):
        if target.endswith("to_window_assets.png"):
            return next(to_window_results)
        if target.endswith("not_passed_floor.png"):
            return [(0, 0), (1, 0)]
        raise AssertionError(target)

    monkeypatch.setattr(mirror_module.auto, "find_element", find_element)
    monkeypatch.setattr(
        mirror_module.auto,
        "click_element",
        lambda target, **_kwargs: setting_clicks.append(target) or True,
    )
    monkeypatch.setattr(mirror_module.auto, "mouse_action_with_pos", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(mirror_module, "sleep", lambda _seconds: None)

    assert mirror.get_which_floor() is True
    assert len(setting_clicks) == 2
    assert mirror.floor == 3
    assert mirror.get_floor_num is False
    assert refreshed_floors == [3]


def test_floor_detection_failure_is_retried_later(monkeypatch):
    mirror = Mirror.__new__(Mirror)
    mirror.floor = None
    mirror.get_floor_num = True
    setting_clicks = []

    monkeypatch.setattr(mirror_module.auto, "find_element", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        mirror_module.auto,
        "click_element",
        lambda target, **_kwargs: setting_clicks.append(target) or True,
    )
    monkeypatch.setattr(mirror_module, "sleep", lambda _seconds: None)

    assert mirror.get_which_floor() is False
    assert len(setting_clicks) == 8
    assert mirror.floor is None
    assert mirror.get_floor_num is True


def test_equal_weight_route_prefers_mid_then_up_then_down():
    bus = Node((0, MID), "bus", (0, 0))
    up = Node((1, -1), "event", (1, -1))
    mid = Node((1, MID), "event", (1, 0))
    down = Node((1, 1), "event", (1, 1))
    boss = Node((2, MID), "boss_battle", (2, 0))
    for node in (up, mid, down):
        bus.add_next(node)
        node.add_next(boss)

    _, route = find_min_weight_route(
        {node.coord: node for node in (bus, up, mid, down, boss)}
    )

    assert [node.coord for node in route] == [(0, 0), (1, 0), (2, 0)]


def test_missing_shop_adds_new_shop_and_boss_after_detected_nodes():
    bus = Node((0, MID), "bus", (100, 700))
    false_boss = Node((4, MID), "boss_battle", (2180, 700))
    nodes = {bus.coord: bus, false_boss.coord: false_boss}

    _append_missing_terminal_nodes(nodes, (100, 700))

    assert nodes[(5, MID)].type == "shop"
    assert nodes[(6, MID)].type == "boss_battle"
    assert nodes[(5, MID)] in false_boss.next
    assert nodes[(6, MID)] in nodes[(5, MID)].next


def test_hard_mode_only_caches_bus_and_next_node(monkeypatch):
    route = [
        Node((0, MID), "bus", (0, 0)),
        Node((1, MID), "event", (1, 0)),
        Node((2, MID), "boss_battle", (2, 0)),
    ]
    monkeypatch.setattr(
        "tasks.mirror.search_road.search_road_from_road_map",
        lambda bus_row: (route, {node.coord: node for node in route}),
    )

    normal_map = MirrorMap(hard_mode=False)
    hard_map = MirrorMap(hard_mode=True)
    normal_map.begin_floor()
    hard_map.begin_floor()

    assert normal_map.get_next_node_direction() == "M"
    assert hard_map.get_next_node_direction() == "M"
    assert len(normal_map.floor_route) == 3
    assert len(hard_map.floor_route) == 2


def test_yolo26_model_matches_preprocessor():
    session = _get_onnx_session()

    assert session.get_inputs()[0].shape == [1, 3, ONNX_IMAGE_SIZE, ONNX_IMAGE_SIZE]
    assert session.get_outputs()[0].shape[1] == 11
    assert ONNX_CONFIDENCE_THRESHOLD == 0.4
