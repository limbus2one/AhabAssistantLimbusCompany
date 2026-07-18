import unittest
from unittest.mock import call, patch

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

    def test_onnx_only_recognizes_current_screenshot_without_moving_bus(self):
        screenshot_image = object()
        points = [["event", (600, 700)]]
        with (
            patch.object(search_road.auto, "take_screenshot", return_value=screenshot_image) as screenshot,
            patch.object(search_road.auto, "screenshot", screenshot_image),
            patch.object(search_road, "identify_nodes", return_value=points) as identify,
            patch.object(search_road.auto, "mouse_drag") as drag,
        ):
            result = search_road.onnx((100, 700))

        self.assertEqual(result, ((100, 700), points))
        screenshot.assert_called_once_with(gray=False)
        identify.assert_called_once_with(100, image=screenshot_image)
        drag.assert_not_called()

    def test_find_bus_selects_position_with_most_nodes_and_restores_it(self):
        initial = (500, 500)
        up = (120, 263)
        mid = (120, 700)
        down = (120, 1137)
        up_points = [["event", (600, 263)]]
        mid_points = [["event", (600, 700)], ["battle", (1120, 700)], ["shop", (1640, 700)]]
        down_points = [["event", (600, 1137)], ["battle", (1120, 700)]]

        with (
            patch.object(search_road.auto, "find_element", return_value=initial),
            patch.object(search_road, "move_bus", side_effect=[up, mid, down, mid]) as move,
            patch.object(
                search_road,
                "onnx",
                side_effect=[
                    (up, up_points),
                    (mid, mid_points),
                    (down, down_points),
                    (mid, mid_points),
                ],
            ) as recognize,
        ):
            result = search_road.find_bus()

        self.assertEqual(result, (mid, search_road.Position.MID, mid_points))
        self.assertEqual(
            move.call_args_list,
            [
                call(initial, search_road.Position.UP),
                call(up, search_road.Position.MID),
                call(mid, search_road.Position.DOWN),
                call(down, search_road.Position.MID),
            ],
        )
        self.assertEqual(recognize.call_count, 4)

    def test_find_bus_prefers_middle_when_node_counts_tie(self):
        initial = (500, 500)
        up = (120, 263)
        mid = (120, 700)
        down = (120, 1137)
        points = [["event", (600, 700)]]
        with (
            patch.object(search_road.auto, "find_element", return_value=initial),
            patch.object(search_road, "move_bus", side_effect=[up, mid, down, mid]),
            patch.object(
                search_road,
                "onnx",
                side_effect=[(up, points), (mid, points), (down, points), (mid, points)],
            ),
        ):
            result = search_road.find_bus()

        self.assertEqual(result[1], search_road.Position.MID)

    def test_move_bus_uses_three_standard_screen_positions(self):
        expected_y = {
            search_road.Position.UP: 263,
            search_road.Position.MID: 700,
            search_road.Position.DOWN: 1137,
        }
        for position, target_y in expected_y.items():
            with (
                self.subTest(position=position),
                patch.object(search_road.cfg, "set_win_size", 1440),
                patch.object(search_road.auto, "mouse_drag") as drag,
                patch.object(search_road.auto, "find_element", return_value=(120, target_y)),
            ):
                result = search_road.move_bus((500, 500), position)

            self.assertEqual(result, (120, target_y))
            drag.assert_called_once_with(
                500,
                500,
                drag_time=0.5,
                dx=-380,
                dy=target_y - 500,
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

        self.assertEqual(weight, 1017)
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


class NodeContextTest(unittest.TestCase):
    def test_detected_nodes_include_team_floor_and_theme_pack(self):
        with patch.object(search_road.cfg, "set_win_size", 1440):
            nodes = search_road._snap_points_to_grid(
                [["event", (620, 700)]],
                (100, 700),
                team_number=4,
                floor=2,
                theme_pack_name="时间杀人时间",
            )

        self.assertEqual(set(nodes), {(0, 0), (1, 0)})
        for node in nodes.values():
            self.assertEqual(node.team_number, 4)
            self.assertEqual(node.floor, 2)
            self.assertEqual(node.theme_pack_name, "时间杀人时间")

    def test_synthetic_shop_and_boss_keep_map_context(self):
        context = {
            "team_number": 8,
            "floor": 3,
            "theme_pack_name": "无主张之地",
        }
        nodes = {
            (column, 0): search_road.Node(
                (column, 0),
                "bus" if column == 0 else "event",
                (100 + column * 520, 700),
                **context,
            )
            for column in range(4)
        }
        with patch.object(search_road.cfg, "set_win_size", 1440):
            search_road._append_shop_and_boss(nodes, (100, 700))

        for coord in ((4, 0), (5, 0)):
            self.assertEqual(nodes[coord].team_number, 8)
            self.assertEqual(nodes[coord].floor, 3)
            self.assertEqual(nodes[coord].theme_pack_name, "无主张之地")

    def test_synthetic_boss_is_forced_to_same_row_as_detected_shop(self):
        bus = search_road.Node((0, 0), "bus", (100, 700))
        event_1 = search_road.Node((1, 0), "event", (620, 700))
        event_2 = search_road.Node((2, 1), "event", (1140, 1137))
        shop = search_road.Node((3, 1), "shop", (1660, 1137))
        nodes = {node.coord: node for node in (bus, event_1, event_2, shop)}

        with patch.object(search_road.cfg, "set_win_size", 1440):
            search_road._append_shop_and_boss(nodes, bus.screen_pos)

        boss = nodes[(4, 1)]
        self.assertTrue(boss.synthetic)
        self.assertNotIn((4, 0), nodes)
        self.assertEqual(shop.next, [boss])
        self.assertEqual(search_road.get_node_direction(shop, boss), "M")


class MirrorMapTest(unittest.TestCase):
    def test_contains_and_refreshes_run_context(self):
        mirror_map = search_road.MirrorMap(
            floor=2,
            hard_mode=True,
            team_number=6,
            theme_pack_name="初始卡包",
        )
        mirror_map.floor_route = [object()]
        mirror_map.floor_map = {(0, 0): object()}

        mirror_map.refresh_theme_pack("新卡包")

        self.assertEqual(mirror_map.team_number, 6)
        self.assertEqual(mirror_map.floor, 2)
        self.assertEqual(mirror_map.theme_pack_name, "新卡包")
        self.assertEqual(mirror_map.floor_route, [])
        self.assertEqual(mirror_map.floor_map, {})

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
            patch.object(search_road.auto, "mouse_click") as mouse_click,
            patch.object(search_road.auto, "click_element", return_value=True),
            patch.object(search_road, "sleep"),
        ):
            self.assertEqual(mirror_map.get_next_node_direction(), "M")
            self.assertEqual(mirror_map.get_next_node_direction(), "M")
            self.assertTrue(mirror_map.enter_next_node("M"))
            self.assertEqual(mirror_map.get_next_node_direction(), "U")

        build_route.assert_called_once_with(
            hard_mode=False,
            team_number=None,
            floor=1,
            theme_pack_name=None,
        )
        self.assertEqual(mirror_map.floor_route, [battle, event])
        self.assertEqual(mirror_map.floor_map, floor_map)
        mouse_click.assert_called_once_with(100, 100)

    def test_mouse_flow_keeps_route_when_old_fallback_also_fails(self):
        bus = search_road.Node((0, 0), "bus", (0, 0))
        battle = search_road.Node((1, 0), "battle", (1, 0))
        mirror_map = search_road.MirrorMap()
        mirror_map.floor_route = [bus, battle]

        with (
            patch.object(search_road.cfg, "mirror_keyboard_navigation", False),
            patch.object(mirror_map, "_get_next_node_position", return_value=(100, 100)),
            patch.object(search_road.auto, "mouse_click"),
            patch.object(search_road.auto, "click_element", side_effect=[False, False]) as click,
            patch.object(search_road, "sleep"),
        ):
            result = mirror_map.enter_next_node("M")

        self.assertFalse(result)
        self.assertEqual(mirror_map.floor_route, [bus, battle])
        self.assertEqual(
            click.call_args_list,
            [
                call("mirror/road_in_mir/enter_assets.png", take_screenshot=True),
                call("mirror/mybus_default_distance.png", take_screenshot=True),
            ],
        )

    def test_keyboard_flow_uses_old_fixed_wait_sequence(self):
        bus = search_road.Node((0, 0), "bus", (0, 0))
        battle = search_road.Node((1, 0), "battle", (1, 0))
        mirror_map = search_road.MirrorMap()
        mirror_map.floor_route = [bus, battle]

        with (
            patch.object(search_road.cfg, "mirror_keyboard_navigation", True),
            patch.object(search_road.auto, "key_press") as key_press,
            patch.object(search_road.auto, "click_element", return_value=False) as click,
            patch.object(search_road, "sleep") as wait,
        ):
            result = mirror_map.enter_next_node("M")

        self.assertTrue(result)
        self.assertEqual(key_press.call_args_list, [call("right"), call("enter")])
        self.assertEqual(wait.call_args_list, [call(0.5), call(1.25)])
        click.assert_called_once_with("mirror/road_in_mir/enter_assets.png", take_screenshot=True)
        self.assertEqual(mirror_map.floor_route, [battle])

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
