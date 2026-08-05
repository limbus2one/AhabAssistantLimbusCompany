from unittest.mock import patch

from tasks.mirror import search_road


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
            search_road.Position.MID,
        )

    assert connections == [(1, search_road.Position.MID, search_road.Position.MID)]

    find.assert_called_once_with(
        "mirror/road_in_mir/mid.png",
        threshold=search_road.CONNECTION_MATCH_THRESHOLD,
        my_crop=(210, 440, 510, 680),
        model="aggressive",
    )


def test_route_graph_only_connects_template_matches():
    graph = search_road.RouteGraph(
        [[["battle", (620, 560)]]],
        initial_bus_pos=search_road.Position.MID,
        bus_position=(100, 560),
        hard_mode=True,
    )
    bus = graph.layers["layer1"][search_road.Position.MID]

    graph.init_road([])
    assert bus.next_nodes == []

    graph.init_road([(1, search_road.Position.MID, search_road.Position.MID)])
    assert bus.next_nodes == [graph.layers["layer2"][search_road.Position.MID]]


def test_single_row_does_not_guess_straight_road_when_matching_fails():
    with (
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
        search_road.Position.MID,
    )
