import unittest
from unittest.mock import patch

from tasks.mirror import search_road


class FirstDirectionTest(unittest.TestCase):
    def test_returns_first_matching_direction(self):
        with (
            patch.object(search_road.cfg, "set_win_size", 1440),
            patch.object(search_road.auto, "take_screenshot", return_value=object()),
            patch.object(search_road.auto, "find_element", return_value=(350, 500)) as find,
        ):
            direction = search_road.find_first_direction((100, 700))

        self.assertEqual(direction, "U")
        find.assert_called_once_with(
            "mirror/road_in_mir/up_arr.png",
            threshold=0.75,
            my_crop=(200, 380, 500, 620),
            model="aggressive",
        )

    def test_checks_middle_after_upper_direction_is_missing(self):
        with (
            patch.object(search_road.cfg, "set_win_size", 1440),
            patch.object(search_road.auto, "take_screenshot", return_value=object()),
            patch.object(
                search_road.auto,
                "find_element",
                side_effect=[False, (350, 725)],
            ) as find,
        ):
            direction = search_road.find_first_direction((100, 700))

        self.assertEqual(direction, "M")
        self.assertEqual(
            [call.args[0] for call in find.call_args_list],
            [
                "mirror/road_in_mir/up_arr.png",
                "mirror/road_in_mir/mid_arr.png",
            ],
        )

    def test_returns_false_when_no_direction_matches(self):
        with (
            patch.object(search_road.cfg, "set_win_size", 1440),
            patch.object(search_road.auto, "take_screenshot", return_value=object()),
            patch.object(search_road.auto, "find_element", return_value=False),
        ):
            direction = search_road.find_first_direction((100, 700))

        self.assertFalse(direction)


class OnnxPreparationTest(unittest.TestCase):
    def test_find_bus_returns_none_before_using_missing_position(self):
        with patch.object(
            search_road.auto,
            "find_element",
            return_value=None,
        ) as find:
            result = search_road.find_bus()

        self.assertIsNone(result)
        find.assert_called_once_with(
            "mirror/mybus_default_distance.png",
            take_screenshot=True,
        )

    def test_generate_map_refreshes_grayscale_screenshot(self):
        bus = search_road.Node((0, 0), "bus", (100, 700))
        nodes = {bus.coord: bus}
        with (
            patch.object(
                search_road.auto,
                "take_screenshot",
                return_value=object(),
            ) as screenshot,
            patch.object(
                search_road,
                "_snap_points_to_grid",
                return_value=nodes,
            ),
            patch.object(search_road, "_connect_visible_nodes"),
            patch.object(search_road, "_append_shop_and_boss"),
        ):
            result = search_road.generate_map([], (100, 700))

        self.assertIs(result, nodes)
        screenshot.assert_called_once_with(gray=True)

    def test_generate_map_stops_when_grayscale_screenshot_fails(self):
        with patch.object(
            search_road.auto,
            "take_screenshot",
            return_value=None,
        ):
            result = search_road.generate_map([], (100, 700))

        self.assertEqual(result, {})


class RouteTest(unittest.TestCase):
    def test_route_uses_lowest_total_weight(self):
        bus = search_road.Node((0, 0), "bus", (0, 0))
        battle = search_road.Node((1, 0), "battle", (1, 0))
        event = search_road.Node((1, 1), "event", (1, 1))
        boss = search_road.Node((2, 0), "boss_battle", (2, 0))
        bus.add_next(battle)
        bus.add_next(event)
        battle.add_next(boss)
        event.add_next(boss)

        weight, route = search_road.find_min_weight_route(
            {
                bus.coord: bus,
                battle.coord: battle,
                event.coord: event,
                boss.coord: boss,
            }
        )

        self.assertEqual(weight, 19)
        self.assertEqual(route, [bus, event, boss])
        self.assertEqual(
            search_road.route_to_directions(route),
            (["D", "U"], ["bus", "event", "boss_battle"]),
        )

    def test_boss_is_not_target_when_its_column_has_multiple_nodes(self):
        bus = search_road.Node((0, 0), "bus", (0, 0))
        false_boss = search_road.Node((1, 0), "boss_battle", (1, 0))
        event = search_road.Node((1, 1), "event", (1, 1))
        battle = search_road.Node((2, 1), "battle", (2, 1))
        bus.add_next(false_boss)
        bus.add_next(event)
        event.add_next(battle)

        weight, route = search_road.find_min_weight_route(
            {
                bus.coord: bus,
                false_boss.coord: false_boss,
                event.coord: event,
                battle.coord: battle,
            }
        )

        self.assertEqual(weight, 48)
        self.assertEqual(route, [bus, event, battle])


class MirrorMapTest(unittest.TestCase):
    def test_route_node_is_consumed_only_after_entering_node(self):
        bus = search_road.Node((0, 0), "bus", (0, 0))
        battle = search_road.Node((1, 0), "battle", (1, 0))
        event = search_road.Node((2, -1), "event", (2, -1))
        floor_route = [bus, battle, event]
        floor_map = {node.coord: node for node in floor_route}
        mirror_map = search_road.MirrorMap()

        with (
            patch.object(
                search_road,
                "search_road_from_road_map",
                return_value=(floor_route, floor_map),
            ) as build_route,
            patch.object(search_road.cfg, "mirror_keyboard_navigation", False),
            patch.object(
                mirror_map,
                "_get_next_node_position",
                return_value=(100, 100),
            ),
            patch.object(search_road.auto, "mouse_action_with_pos"),
            patch.object(search_road.auto, "wait_page_load"),
            patch.object(search_road.auto, "click_element", return_value=True),
        ):
            self.assertEqual(mirror_map.get_next_node_direction(), "M")
            self.assertEqual(mirror_map.get_next_node_direction(), "M")
            self.assertTrue(mirror_map.enter_next_node("M"))
            self.assertEqual(mirror_map.get_next_node_direction(), "U")

        build_route.assert_called_once_with(hard_mode=False)
        self.assertEqual(mirror_map.floor_route, [battle, event])
        self.assertEqual(mirror_map.floor_map, floor_map)

    def test_invalid_onnx_route_uses_first_direction(self):
        mirror_map = search_road.MirrorMap()
        with (
            patch.object(
                search_road,
                "search_road_from_road_map",
                return_value=([], {}),
            ),
            patch.object(
                search_road.auto,
                "find_element",
                return_value=(100, 700),
            ),
            patch.object(
                search_road,
                "find_first_direction",
                return_value="D",
            ) as find_direction,
        ):
            self.assertEqual(mirror_map.get_next_node_direction(), "D")

        find_direction.assert_called_once_with((100, 700))
        self.assertEqual(mirror_map.floor_route, [])
        self.assertEqual(mirror_map.floor_map, {})

    def test_returns_false_when_onnx_and_first_direction_both_fail(self):
        mirror_map = search_road.MirrorMap()
        with (
            patch.object(
                search_road,
                "search_road_from_road_map",
                return_value=([], {}),
            ),
            patch.object(
                search_road.auto,
                "find_element",
                return_value=(100, 700),
            ),
            patch.object(
                search_road,
                "find_first_direction",
                return_value=False,
            ),
        ):
            self.assertFalse(mirror_map.get_next_node_direction())

        self.assertEqual(mirror_map.floor_route, [])
        self.assertEqual(mirror_map.floor_map, {})


if __name__ == "__main__":
    unittest.main()
