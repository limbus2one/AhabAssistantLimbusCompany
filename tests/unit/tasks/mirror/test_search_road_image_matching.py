from unittest.mock import patch

from tasks.mirror import search_road


def test_yolo26_model_matches_preprocessor():
    import onnxruntime as ort

    session = ort.InferenceSession("assets/model/best.onnx")

    assert session.get_inputs()[0].shape == [1, 3, 544, 960]
    assert session.get_outputs()[0].shape[1] == 11


def test_identify_road_matches_connection_template():
    nodes = [[["battle", (620, 560)]]]

    with (
        patch.object(search_road.cfg, 'set_win_size', 1440),
        patch.object(search_road.auto, "take_screenshot", return_value=object()),
        patch.object(search_road.auto, "find_element", return_value=(360, 560)) as find,
    ):
        connections = search_road.identify_road(
            (100, 560),
            nodes,
            search_road.Row.MID,
        )

    assert connections == [(1, search_road.Row.MID, search_road.Row.MID)]

    find.assert_called_once_with(
        "mirror/road_in_mir/mid.png",
        my_crop=(210, 440, 510, 680),
        model="aggressive",
    )


def test_identify_road_does_not_connect_across_a_missing_column():
    nodes = [
        [["battle", (620, 560)]],
        [["event", (1660, 560)]],
    ]

    with (
        patch.object(search_road.cfg, "set_win_size", 1440),
        patch.object(search_road.auto, "take_screenshot", return_value=object()),
        patch.object(search_road.auto, "find_element", return_value=(360, 560)) as find,
    ):
        connections = search_road.identify_road(
            (100, 560),
            nodes,
            search_road.Row.MID,
        )

    assert connections == [(1, search_road.Row.MID, search_road.Row.MID)]
    assert find.call_count == 1


def test_route_graph_only_connects_template_matches():
    graph = search_road.RouteGraph(
        [[["battle", (620, 560)]]],
        initial_bus_pos=search_road.Row.MID,
        bus_position=(100, 560),
        hard_mode=True,
    )
    bus = graph.layers["layer1"][search_road.Row.MID]

    graph.init_road([])
    assert bus.next_nodes == []

    graph.init_road([(1, search_road.Row.MID, search_road.Row.MID)])
    assert bus.next_nodes == [graph.layers["layer2"][search_road.Row.MID]]


def test_single_row_does_not_guess_straight_road_when_matching_fails():
    with (
        patch.object(search_road.cfg, "set_win_size", 1440),
        patch.object(search_road.auto, "click_element", return_value=False),
        patch.object(search_road.auto, "find_element", side_effect=[(80, 690), (80, 690)]),
        patch.object(
            search_road,
            "identify_nodes",
            return_value=[["battle", (620, 690)], ["event", (1000, 690)]],
        ),
        patch.object(search_road, "identify_road", return_value=[]) as identify,
    ):
        assert search_road.search_road_from_road_map() == ([], [])

    identify.assert_called_once_with(
        (80, 690),
        [[["battle", (620, 690)]]],
        search_road.Row.MID,
    )


def test_node_grouping_tolerance_uses_quarter_grid_gap():
    with patch.object(search_road.cfg, "set_win_size", 1440):
        x_groups = search_road.divide_the_area_by_x(
            [["a", (0, 0)], ["b", (130, 0)], ["c", (261, 0)]]
        )
        y_groups = search_road.divide_the_area_by_y(
            [["a", (0, 0)], ["b", (0, 109)], ["c", (0, 219)]]
        )

    assert [len(group) for group in x_groups] == [2, 1]
    assert [len(group) for group in y_groups] == [2, 1]
