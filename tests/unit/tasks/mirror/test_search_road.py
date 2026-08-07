import json
from unittest.mock import Mock, call, patch

import pytest

from module.my_error.my_error import MirrorPathfindingError
from tasks.base import script_task_scheme
from tasks.mirror import search_road
from tasks.mirror.mirror import Mirror


def make_route():
    bus = search_road.Node((0, 0), "bus", (100, 700))
    battle = search_road.Node((1, 0), "battle", (620, 700))
    event = search_road.Node((2, -1), "event", (1140, 263))
    bus.add_next(battle)
    battle.add_next(event)
    route = [bus, battle, event]
    return route, {node.coord: node for node in route}


def test_connection_uses_the_expected_local_template():
    source = search_road.Node((0, 0), "bus", (100, 700))
    target = search_road.Node((1, -1), "event", (620, 263))

    with (
        patch.object(search_road.cfg, "set_win_size", 1440),
        patch.object(search_road.auto, "find_element", return_value=(360, 480)) as find,
    ):
        assert search_road._connection_exists(source, target)

    find.assert_called_once_with(
        "mirror/road_in_mir/up.png",
        threshold=search_road.CONNECTION_MATCH_THRESHOLD,
        my_crop=(210, 361.5, 510, 601.5),
        model="aggressive",
    )


def test_nodes_are_relative_to_bus_and_bus_column_is_ignored():
    with patch.object(search_road.cfg, "set_win_size", 1440):
        nodes = search_road._snap_points_to_grid(
            [
                ["battle", (90, 700)],
                ["event", (120, 263)],
                ["shop", (620, 700)],
            ],
            (100, 700),
        )

    assert set(nodes) == {(0, 0), (1, 0)}
    assert nodes[(1, 0)].type == "shop"


def test_connections_are_not_guessed_when_templates_do_not_match():
    bus = search_road.Node((0, 0), "bus", (100, 700))
    battle = search_road.Node((1, 0), "battle", (620, 700))
    nodes = {bus.coord: bus, battle.coord: battle}

    with patch.object(search_road, "_connection_exists", return_value=False):
        search_road._connect_visible_nodes(nodes)

    assert bus.next == []


@pytest.mark.parametrize(
    ("last_type", "expected_types"),
    [
        ("battle", ["battle", "shop", "boss_battle"]),
        ("shop", ["shop", "boss_battle"]),
        ("boss_battle", ["boss_battle"]),
    ],
)
def test_shop_and_boss_are_completed_from_the_visible_end(last_type, expected_types):
    bus = search_road.Node((0, 0), "bus", (100, 700))
    last = search_road.Node((1, 0), last_type, (620, 700))
    bus.add_next(last)
    nodes = {bus.coord: bus, last.coord: last}

    with patch.object(search_road.cfg, "set_win_size", 1440):
        search_road._append_shop_and_boss(nodes, (100, 700))

    types = [nodes[coord].type for coord in sorted(nodes) if coord != (0, 0)]
    assert types == expected_types


def test_route_targets_the_furthest_column_not_an_early_boss():
    bus = search_road.Node((0, 0), "bus", (0, 0))
    false_boss = search_road.Node((1, 0), "boss_battle", (1, 0))
    event = search_road.Node((1, 1), "event", (1, 1))
    battle = search_road.Node((2, 1), "battle", (2, 1))
    bus.add_next(false_boss)
    bus.add_next(event)
    event.add_next(battle)

    weight, route = search_road.find_min_weight_route({node.coord: node for node in (bus, false_boss, event, battle)})

    assert weight == 5
    assert route == [bus, event, battle]


def test_normal_mode_reuses_the_remaining_floor_route():
    route, floor_map = make_route()
    mirror_map = search_road.MirrorMap(hard_mode=False)
    mirror_map.save_shifted_images = True

    with (
        patch.object(search_road, "search_road_from_road_map", return_value=(route, floor_map)) as build,
        patch.object(search_road.cfg, "mirror_keyboard_navigation", True),
        patch.object(search_road.auto, "key_press"),
        patch.object(search_road, "_enter_succeeded", return_value=True),
        patch.object(search_road, "sleep"),
    ):
        assert mirror_map.get_next_node_direction() == "M"
        assert mirror_map.enter_next_node("M")
        assert mirror_map.get_next_node_direction() == "U"

    build.assert_called_once_with(hard_mode=False, save_shifted_images=True)
    assert mirror_map.save_shifted_images is False


def test_hard_mode_naturally_rebuilds_after_each_node():
    route, floor_map = make_route()
    mirror_map = search_road.MirrorMap(hard_mode=True)
    mirror_map.save_shifted_images = True

    with (
        patch.object(search_road, "search_road_from_road_map", return_value=(route, floor_map)) as build,
        patch.object(search_road.cfg, "mirror_keyboard_navigation", True),
        patch.object(search_road.auto, "key_press"),
        patch.object(search_road, "_enter_succeeded", return_value=True),
        patch.object(search_road, "sleep"),
    ):
        assert mirror_map.get_next_node_direction() == "M"
        assert len(mirror_map.floor_route) == 2
        assert mirror_map.enter_next_node("M")
        assert len(mirror_map.floor_route) == 1
        assert mirror_map.get_next_node_direction() == "M"

    assert build.call_args_list == [
        call(hard_mode=True, save_shifted_images=True),
        call(hard_mode=True, save_shifted_images=False),
    ]


def test_failed_entry_does_not_consume_the_route():
    route, floor_map = make_route()
    mirror_map = search_road.MirrorMap()
    mirror_map.floor_route = list(route)
    mirror_map.floor_map = floor_map

    with (
        patch.object(search_road.cfg, "mirror_keyboard_navigation", True),
        patch.object(search_road.auto, "key_press"),
        patch.object(search_road, "_enter_succeeded", return_value=False),
        patch.object(search_road, "sleep"),
        pytest.raises(MirrorPathfindingError, match="无法确认已进入镜牢节点"),
    ):
        mirror_map.enter_next_node("M")

    assert mirror_map.floor_route == route


def test_onnx_screenshots_use_unique_log_names():
    screenshot = Mock()
    with (
        patch.object(search_road, "find_bus", return_value=((100, 700), search_road.Position.MID)),
        patch.object(search_road, "move_bus", return_value=(120, 700)),
        patch.object(search_road.auto, "take_screenshot", return_value=object()) as take_screenshot,
        patch.object(search_road.auto, "screenshot", screenshot),
        patch.object(search_road, "identify_nodes", return_value=[["battle", (620, 700)]]),
        patch.object(search_road.Path, "mkdir"),
        patch.object(search_road.time, "time_ns", side_effect=[10, 11]),
    ):
        first = search_road.onnx()
        second = search_road.onnx()

    assert screenshot.save.call_args_list == [
        call(search_road.Path("logs/onnx_nodes_10.png")),
        call(search_road.Path("logs/onnx_nodes_11.png")),
    ]
    assert take_screenshot.call_args_list == [call(gray=False), call(gray=False)]
    assert first[-1] == 10
    assert second[-1] == 11


def test_map_and_route_logs_contain_nodes_connections_and_selected_route(tmp_path, monkeypatch):
    route, floor_map = make_route()
    monkeypatch.chdir(tmp_path)

    search_road._save_pathfinding_logs(12, floor_map, route, 5, False)

    map_log = json.loads((tmp_path / "logs/onnx_map_12.json").read_text(encoding="utf-8"))
    route_log = json.loads((tmp_path / "logs/onnx_route_12.json").read_text(encoding="utf-8"))
    assert [node["type"] for node in map_log["nodes"]] == ["bus", "battle", "event"]
    assert map_log["connections"] == [
        {"from": [0, 0], "to": [1, 0]},
        {"from": [1, 0], "to": [2, -1]},
    ]
    assert route_log == {
        "hard_mode": False,
        "weight": 5,
        "directions": ["M", "U"],
        "nodes": [
            {"coord": [0, 0], "type": "bus"},
            {"coord": [1, 0], "type": "battle"},
            {"coord": [2, -1], "type": "event"},
        ],
    }


def test_hard_mode_logs_only_the_cached_bus_and_next_node():
    route, floor_map = make_route()
    with (
        patch.object(search_road, "onnx", return_value=((100, 700), [["battle", (620, 700)]], 13)),
        patch.object(search_road, "generate_map", return_value=floor_map),
        patch.object(search_road, "find_min_weight_route", return_value=(5, route)),
        patch.object(search_road, "_save_pathfinding_logs") as save,
    ):
        search_road.search_road_from_road_map(hard_mode=True)

    save.assert_called_once_with(13, floor_map, route[:2], 5, True)


def test_shifted_onnx_images_are_saved_and_the_map_is_restored():
    screenshot = Mock()
    with (
        patch.object(search_road.cfg, "set_win_size", 1440),
        patch.object(search_road.auto, "mouse_drag") as drag,
        patch.object(search_road.auto, "take_screenshot", return_value=True),
        patch.object(search_road.auto, "screenshot", screenshot),
        patch.object(search_road.auto, "find_element", return_value=(999, 999)),
        patch.object(search_road, "sleep"),
    ):
        search_road._save_shifted_onnx_images(13)

    assert drag.call_args_list == [
        *[call(1600, 700, drag_time=0.5, dx=-520) for _ in range(6)],
        call(400, 700, drag_time=0.5, dx=1560),
        call(400, 700, drag_time=0.5, dx=1560),
    ]
    assert screenshot.save.call_args_list == [
        call(search_road.Path("logs") / f"onnx_nodes_13_shift_{index}.png")
        for index in range(1, 7)
    ]


def test_empty_node_result_still_returns_capture():
    screenshot = Mock()
    with (
        patch.object(search_road, "find_bus", return_value=((100, 700), search_road.Position.MID)),
        patch.object(search_road, "move_bus", return_value=(100, 700)),
        patch.object(search_road.auto, "take_screenshot", return_value=True),
        patch.object(search_road.auto, "screenshot", screenshot),
        patch.object(search_road, "identify_nodes", return_value=[]),
        patch.object(search_road, "_save_shifted_onnx_images") as save_shifted,
        patch.object(search_road.time, "time_ns", return_value=13),
    ):
        assert search_road.onnx(save_shifted_images=True) == ((100, 700), [], 13)

    save_shifted.assert_called_once_with(13)


def test_empty_node_result_becomes_no_reachable_route():
    with (
        patch.object(search_road, "onnx", return_value=((100, 700), [], 13)),
        patch.object(search_road.auto, "take_screenshot", return_value=True),
        patch.object(search_road, "_save_pathfinding_logs"),
        pytest.raises(MirrorPathfindingError, match="不存在可达路线"),
    ):
        search_road.search_road_from_road_map()


def test_simple_keyboard_mode_failure_does_not_fall_back_to_onnx():
    mirror = Mirror.__new__(Mirror)
    mirror.mirror_map = Mock()

    with (
        patch.object(search_road.cfg, "mirror_keyboard_simple_pathfinding", True),
        patch("tasks.mirror.mirror.search_road_simple_keyboard", return_value=False),
        pytest.raises(MirrorPathfindingError, match="简单键盘寻路失败"),
    ):
        mirror.search_road()

    mirror.mirror_map.get_next_node_direction.assert_not_called()


def test_no_reachable_route_falls_back_to_simple_keyboard():
    mirror = Mirror.__new__(Mirror)
    mirror.mirror_map = Mock()
    mirror.mirror_map.get_next_node_direction.side_effect = MirrorPathfindingError(
        "镜牢节点图中不存在可达路线"
    )

    with (
        patch.object(search_road.cfg, "mirror_keyboard_simple_pathfinding", False),
        patch("tasks.mirror.mirror.auto.find_element", return_value=None),
        patch("tasks.mirror.mirror.auto.click_element", return_value=False),
        patch("tasks.mirror.mirror.search_road_simple_keyboard", return_value=True) as fallback,
    ):
        assert mirror.search_road()

    fallback.assert_called_once_with()
    mirror.mirror_map.enter_next_node.assert_not_called()


def test_bus_detection_failure_does_not_fall_back_to_simple_keyboard():
    mirror = Mirror.__new__(Mirror)
    mirror.mirror_map = Mock()
    mirror.mirror_map.get_next_node_direction.side_effect = MirrorPathfindingError(
        "镜牢 ONNX 节点识别失败"
    )

    with (
        patch.object(search_road.cfg, "mirror_keyboard_simple_pathfinding", False),
        patch("tasks.mirror.mirror.auto.find_element", return_value=None),
        patch("tasks.mirror.mirror.auto.click_element", return_value=False),
        patch("tasks.mirror.mirror.search_road_simple_keyboard") as fallback,
        pytest.raises(MirrorPathfindingError, match="ONNX 节点识别失败"),
    ):
        mirror.search_road()

    fallback.assert_not_called()


def test_floor_is_read_once_and_refreshes_the_cache():
    mirror = Mirror.__new__(Mirror)
    mirror.floor = 1
    mirror.get_floor_num = True
    mirror.mirror_map = Mock()

    with (
        patch.object(search_road.cfg, "set_win_size", 1440),
        patch("tasks.mirror.mirror.auto.click_element", return_value=True),
        patch(
            "tasks.mirror.mirror.auto.find_element",
            side_effect=[(1000, 700), [(1, 1), (2, 2), (3, 3)], None],
        ),
        patch("tasks.mirror.mirror.auto.mouse_action_with_pos"),
        patch("tasks.mirror.mirror.sleep"),
    ):
        mirror.get_which_floor()

    assert mirror.floor == 2
    assert mirror.get_floor_num is False
    mirror.mirror_map.refresh_floor.assert_called_once_with(2)


def test_floor_read_failure_and_map_failure_raise_pathfinding_error():
    mirror = Mirror.__new__(Mirror)
    with (
        patch("tasks.mirror.mirror.auto.click_element", return_value=False),
        patch("tasks.mirror.mirror.sleep"),
        pytest.raises(MirrorPathfindingError, match="无法打开镜牢设置页面"),
    ):
        mirror.get_which_floor()

    with (
        patch.object(search_road, "onnx", return_value=None),
        pytest.raises(MirrorPathfindingError, match="ONNX 节点识别失败"),
    ):
        search_road.search_road_from_road_map()


def test_pathfinding_error_escapes_the_single_mirror_task():
    with (
        patch.object(script_task_scheme.cfg, "auto_hard_mirror", False),
        patch.object(script_task_scheme, "Mirror", side_effect=MirrorPathfindingError("stop")),
        pytest.raises(MirrorPathfindingError, match="stop"),
    ):
        script_task_scheme.onetime_mir_process(Mock(), 1)
