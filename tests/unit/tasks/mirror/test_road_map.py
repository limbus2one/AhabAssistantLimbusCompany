import pytest

from tasks.mirror import road_map, search_road


@pytest.mark.parametrize(
    ("up_exists", "down_exists", "expected_row"),
    [
        (True, True, road_map.Position.MID),
        (False, False, road_map.Position.MID),
        (True, False, road_map.Position.DOWN),
        (False, True, road_map.Position.UP),
    ],
)
def test_find_bus_determines_row(monkeypatch, up_exists, down_exists, expected_row):
    monkeypatch.setattr(road_map.cfg, "set_win_size", 1440)
    monkeypatch.setattr(road_map.auto, "take_screenshot", lambda **kwargs: object())
    monkeypatch.setattr(
        road_map.auto,
        "find_element",
        lambda target, **kwargs: (100, 700),
    )
    results = iter((up_exists, down_exists))
    monkeypatch.setattr(road_map, "_node_marker_exists", lambda position: next(results))

    assert road_map.find_bus() == ((100, 700), expected_row)


@pytest.mark.parametrize(
    ("bus_row", "target"),
    [
        (road_map.Position.UP, (300, 780 - road_map.Y_GAP)),
        (road_map.Position.MID, (300, 780)),
        (road_map.Position.DOWN, (300, 780 + road_map.Y_GAP)),
    ],
)
def test_move_bus_uses_row_target(monkeypatch, bus_row, target):
    monkeypatch.setattr(road_map.cfg, "set_win_size", 1440)
    drag_calls = []
    monkeypatch.setattr(
        road_map.auto,
        "mouse_drag",
        lambda x, y, **kwargs: drag_calls.append((x, y, kwargs["dx"], kwargs["dy"])),
    )
    monkeypatch.setattr(road_map.auto, "mouse_to_blank", lambda: None)
    monkeypatch.setattr(road_map.auto, "take_screenshot", lambda **kwargs: object())
    monkeypatch.setattr(
        road_map.auto,
        "find_element",
        lambda target, **kwargs: target_position,
    )
    monkeypatch.setattr(road_map, "sleep", lambda seconds: None)
    start = (700, 600)
    target_position = target

    assert road_map.move_bus(start, bus_row) == target
    assert drag_calls == [
        (start[0], start[1], target[0] - start[0], target[1] - start[1])
    ]


def test_generate_map_adds_shop_boss_and_finds_lowest_weight_path(monkeypatch):
    monkeypatch.setattr(road_map.cfg, "set_win_size", 1440)
    bus = (100, 700)
    points = [
        ["battle", (620, 263)],
        ["event", (620, 700)],
        ["battle", (1140, 263)],
        ["event", (1140, 700)],
        ["battle", (1660, 263)],
        ["event", (1660, 700)],
        ["battle", (2180, 263)],
        ["event", (2180, 700)],
    ]
    routes = {
        ((0, 0), (1, -1)),
        ((0, 0), (1, 0)),
        ((1, -1), (2, -1)),
        ((1, 0), (2, 0)),
        ((2, -1), (3, -1)),
        ((2, 0), (3, 0)),
        ((3, -1), (4, -1)),
        ((3, 0), (4, 0)),
    }
    monkeypatch.setattr(
        road_map,
        "_connection_exists",
        lambda source, target: (source.coord, target.coord) in routes,
    )

    nodes = road_map.generate_map(points, bus)
    total, selected_path = road_map.path(nodes)
    directions, node_types = road_map.path_to_result(selected_path)

    assert nodes[(5, 0)].type == "shop"
    assert nodes[(6, 0)].type == "boss_battle"
    assert {node.coord for node in nodes[(4, 0)].next} == {(5, 0)}
    assert total == 74
    assert directions == ["M", "M", "M", "M", "M", "M"]
    assert node_types == ["bus", "event", "event", "event", "event", "shop", "boss_battle"]


def test_snap_points_keeps_rows_relative_to_bus(monkeypatch):
    monkeypatch.setattr(road_map.cfg, "set_win_size", 1440)

    nodes = road_map._snap_points_to_grid(
        [["event", (620, 1174)]],
        bus_position=(100, 300),
    )

    assert nodes[(1, 2)].type == "event"


def test_generate_map_does_not_duplicate_detected_shop(monkeypatch):
    monkeypatch.setattr(road_map.cfg, "set_win_size", 1440)
    monkeypatch.setattr(road_map, "_connection_exists", lambda source, target: True)
    bus = (100, 700)
    points = [
        ["battle", (620, 700)],
        ["event", (1140, 700)],
        ["battle", (1660, 700)],
        ["shop", (2180, 700)],
    ]

    nodes = road_map.generate_map(points, bus)

    assert [node.type for node in nodes.values()].count("shop") == 1
    assert nodes[(5, 0)].type == "boss_battle"
    assert [node.coord for node in nodes[(4, 0)].next] == [(5, 0)]


def test_generate_map_passes_mirror_context_to_nodes(monkeypatch):
    monkeypatch.setattr(road_map.cfg, "set_win_size", 1440)
    monkeypatch.setattr(road_map, "find_bus", lambda: ((100, 700), road_map.Position.MID))
    monkeypatch.setattr(road_map, "_connect_visible_nodes", lambda nodes: None)
    monkeypatch.setattr(road_map, "_append_shop_and_boss", lambda nodes, bus: None)
    monkeypatch.setattr(road_map, "_log_map", lambda nodes: None)

    nodes = road_map.generate_map(
        [],
        (100, 700),
        theme_pack="test_pack",
        team_number="test_team",
        floor=3,
    )

    node = nodes[(0, 0)]
    assert node.theme_pack == "test_pack"
    assert node.team_number == "test_team"
    assert node.floor == 3
    assert node.node_time == 0


def test_node_time_is_recorded_when_next_node_is_entered(monkeypatch):
    mirror_map = search_road.MirrorMap()
    node_a = road_map.Node((1, 0), "event", (620, 700))
    node_b = road_map.Node((2, 0), "battle", (1140, 700))
    times = iter((100.0, 112.5))
    monkeypatch.setattr(search_road.time, "monotonic", lambda: next(times))

    mirror_map.pending_node = node_a
    mirror_map._record_pending_node_entry()

    assert node_a.node_time == 0

    mirror_map.pending_node = node_b
    mirror_map._record_pending_node_entry()

    assert node_a.node_time == 12.5
    assert node_b.node_time == 0
    assert mirror_map.current_node is node_b
    assert mirror_map.node_history == [node_a, node_b]


def test_get_next_step_sets_the_corresponding_pending_node():
    mirror_map = search_road.MirrorMap()
    next_node = road_map.Node((1, 0), "event", (620, 700))
    mirror_map.floor_map = ["M"]
    mirror_map.floor_nodes = [next_node]

    assert mirror_map.get_next_step() == "M"
    assert mirror_map.pending_node is next_node


def test_current_node_context_is_logged_before_entering_next_node(monkeypatch):
    mirror_map = search_road.MirrorMap()
    current_node = road_map.Node(
        (1, 0),
        "event",
        (620, 700),
        theme_pack="test_pack",
        team_number="test_team",
        floor=2,
    )
    mirror_map.current_node = current_node
    mirror_map.current_node_started_at = 100.0
    messages = []
    monkeypatch.setattr(search_road.time, "monotonic", lambda: 112.5)
    monkeypatch.setattr(search_road.log, "info", messages.append)

    mirror_map._log_current_node_before_next_entry()

    assert current_node.node_time == 12.5
    assert messages == [
        "当前节点: team_number=test_team, package=test_pack, "
        "time_cost=12.50秒, node_type=event, floor=2"
    ]
