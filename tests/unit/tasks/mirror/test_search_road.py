import unittest
from unittest.mock import Mock, call, patch

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

        self.assertEqual(result, (mid, 0, mid_points))
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

        self.assertEqual(result[1], 0)

    def test_find_bus_with_known_row_moves_to_standard_position_before_recognizing(self):
        bus = (500, 500)
        moved_bus = (120, 263)
        points = [["event", (640, 263)]]
        with (
            patch.object(search_road.auto, "find_element", return_value=bus),
            patch.object(search_road, "onnx", return_value=(moved_bus, points)) as recognize,
            patch.object(search_road, "move_bus", return_value=moved_bus) as move,
        ):
            result = search_road.find_bus(bus_row=1)

        self.assertEqual(result, (moved_bus, 1, points))
        move.assert_called_once_with(bus, search_road.Position.UP)
        recognize.assert_called_once_with(moved_bus)

    def test_find_bus_with_known_row_stops_when_move_fails(self):
        bus = (500, 500)
        with (
            patch.object(search_road.auto, "find_element", return_value=bus),
            patch.object(search_road, "move_bus", return_value=None) as move,
            patch.object(search_road, "onnx") as recognize,
        ):
            result = search_road.find_bus(bus_row=-1)

        self.assertIsNone(result)
        move.assert_called_once_with(bus, search_road.Position.DOWN)
        recognize.assert_not_called()

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
        battle = search_road.Node((0, 1), "battle", (1, 0))
        event = search_road.Node((-1, 1), "event", (1, 1))
        boss = search_road.Node((0, 2), "boss_battle", (2, 0))
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
        false_boss = search_road.Node((0, 1), "boss_battle", (1, 0))
        event = search_road.Node((-1, 1), "event", (1, 1))
        battle = search_road.Node((-1, 2), "battle", (2, 1))
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
    def test_bus_coord_uses_actual_logical_row(self):
        cases = (
            (1, (100, 263), (620, 700), (0, 1)),
            (0, (100, 700), (620, 263), (1, 1)),
            (-1, (100, 1137), (620, 700), (0, 1)),
        )
        with patch.object(search_road.cfg, "set_win_size", 1440):
            for bus_row, bus_position, node_position, expected_node_coord in cases:
                with self.subTest(bus_row=bus_row):
                    nodes = search_road._snap_points_to_grid(
                        [["event", node_position]],
                        bus_position,
                        bus_row=bus_row,
                    )

                    self.assertEqual(nodes[(bus_row, 0)].type, "bus")
                    self.assertIn(expected_node_coord, nodes)

    def test_detected_nodes_include_team_floor_and_theme_pack(self):
        with patch.object(search_road.cfg, "set_win_size", 1440):
            nodes = search_road._snap_points_to_grid(
                [["event", (620, 700)]],
                (100, 700),
                bus_row=0,
                team_number=4,
                floor=2,
                theme_pack_name="时间杀人时间",
            )

        self.assertEqual(set(nodes), {(0, 0), (0, 1)})
        for node in nodes.values():
            self.assertEqual(node.team_number, 4)
            self.assertEqual(node.floor, 2)
            self.assertEqual(node.theme_pack_name, "时间杀人时间")

    def test_snap_points_rejects_missing_bus_row(self):
        with self.assertRaisesRegex(ValueError, "bus_row 必须是"):
            search_road._snap_points_to_grid([], (100, 700), bus_row=None)

    def test_synthetic_shop_and_boss_keep_map_context(self):
        context = {
            "team_number": 8,
            "floor": 3,
            "theme_pack_name": "无主张之地",
        }
        nodes = {
            (0, column): search_road.Node(
                (0, column),
                "bus" if column == 0 else "event",
                (100 + column * 520, 700),
                **context,
            )
            for column in range(4)
        }
        with patch.object(search_road.cfg, "set_win_size", 1440):
            search_road._append_shop_and_boss(nodes, (100, 700))

        for coord in ((0, 4), (0, 5)):
            self.assertEqual(nodes[coord].team_number, 8)
            self.assertEqual(nodes[coord].floor, 3)
            self.assertEqual(nodes[coord].theme_pack_name, "无主张之地")

    def test_synthetic_boss_is_forced_to_same_row_as_detected_shop(self):
        bus = search_road.Node((0, 0), "bus", (100, 700))
        event_1 = search_road.Node((0, 1), "event", (620, 700))
        event_2 = search_road.Node((-1, 2), "event", (1140, 1137))
        shop = search_road.Node((-1, 3), "shop", (1660, 1137))
        nodes = {node.coord: node for node in (bus, event_1, event_2, shop)}

        with patch.object(search_road.cfg, "set_win_size", 1440):
            search_road._append_shop_and_boss(nodes, bus.screen_pos)

        boss = nodes[(-1, 4)]
        self.assertTrue(boss.synthetic)
        self.assertNotIn((0, 4), nodes)
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
        self.assertIsNone(mirror_map.bus_row)
        self.assertEqual(mirror_map.theme_pack_name, "新卡包")
        self.assertEqual(mirror_map.floor_route, [])
        self.assertEqual(mirror_map.floor_map, {})

    def test_route_node_is_consumed_only_after_entering_node(self):
        bus = search_road.Node((0, 0), "bus", (0, 0))
        battle = search_road.Node((0, 1), "battle", (1, 0))
        event = search_road.Node((1, 2), "event", (2, -1))
        floor_route = [bus, battle, event]
        floor_map = {node.coord: node for node in floor_route}
        mirror_map = search_road.MirrorMap()

        with (
            patch.object(
                search_road,
                "search_road_from_road_map",
                return_value=(floor_route, floor_map, 0),
            ) as build_route,
            patch.object(search_road.cfg, "mirror_keyboard_navigation", False),
            patch.object(
                mirror_map,
                "_get_next_node_position",
                return_value=(100, 100),
            ),
            patch.object(search_road.auto, "mouse_action_with_pos") as mouse_action,
            patch.object(search_road.auto, "click_element", return_value=True),
            patch.object(
                search_road,
                "_wait_page_load",
                side_effect=[
                    "mirror/road_in_mir/event_in_assets.png",
                    "mirror/road_in_mir/event_in_assets.png",
                ],
            ),
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
            bus_row=None,
        )
        self.assertEqual(mirror_map.floor_route, [battle, event])
        self.assertEqual(mirror_map.floor_map, floor_map)
        self.assertEqual(mirror_map.bus_row, 0)
        mouse_action.assert_called_once_with((100, 100))

    def test_hard_mode_preserves_cache_then_replaces_it_using_updated_bus_row(self):
        bus = search_road.Node((0, 0), "bus", (0, 0))
        battle = search_road.Node((-1, 1), "battle", (1, 0))
        mirror_map = search_road.MirrorMap(hard_mode=True, bus_row=0)
        mirror_map.floor_route = [bus, battle]
        old_map = {node.coord: node for node in (bus, battle)}
        mirror_map.floor_map = old_map

        with (
            patch.object(search_road.cfg, "mirror_keyboard_navigation", False),
            patch.object(mirror_map, "_get_next_node_position", return_value=(100, 100)),
            patch.object(search_road.auto, "mouse_action_with_pos"),
            patch.object(search_road.auto, "click_element", return_value=True),
            patch.object(search_road, "_wait_page_load", return_value="mirror/road_in_mir/event_in_assets.png"),
            patch.object(search_road, "sleep"),
        ):
            result = mirror_map.enter_next_node("D")

        self.assertTrue(result)
        self.assertEqual(mirror_map.bus_row, -1)
        self.assertEqual(mirror_map.floor_route, [battle])
        self.assertEqual(mirror_map.floor_map, old_map)

        new_bus = search_road.Node((-1, 0), "bus", (0, 0))
        new_event = search_road.Node((0, 1), "event", (1, 0))
        new_route = [new_bus, new_event]
        new_map = {node.coord: node for node in new_route}
        with patch.object(
            search_road,
            "search_road_from_road_map",
            return_value=(new_route, new_map, -1),
        ) as rebuild:
            self.assertEqual(mirror_map.get_next_node_direction(), "U")

        self.assertEqual(rebuild.call_args.kwargs["bus_row"], -1)
        self.assertEqual(mirror_map.floor_route, new_route)
        self.assertEqual(mirror_map.floor_map, new_map)

    def test_hard_event_page_updates_bus_row_without_enter_button(self):
        bus = search_road.Node((0, 0), "bus", (0, 0))
        event = search_road.Node((1, 1), "event", (1, 0))
        mirror_map = search_road.MirrorMap(hard_mode=True, bus_row=0)
        mirror_map.floor_route = [bus, event]
        floor_map = {node.coord: node for node in (bus, event)}
        mirror_map.floor_map = floor_map

        with (
            patch.object(search_road.cfg, "mirror_keyboard_navigation", False),
            patch.object(mirror_map, "_get_next_node_position", return_value=(100, 100)),
            patch.object(search_road.auto, "mouse_action_with_pos"),
            patch.object(search_road.auto, "click_element", return_value=False),
            patch.object(search_road.auto, "find_element", return_value=(500, 500)) as find_event,
            patch.object(search_road, "sleep"),
        ):
            result = mirror_map.enter_next_node("U")

        self.assertTrue(result)
        find_event.assert_called_once_with(
            "mirror/road_in_mir/event_in_assets.png",
            take_screenshot=True,
        )
        self.assertEqual(mirror_map.bus_row, 1)
        self.assertEqual(mirror_map.floor_route, [event])
        self.assertEqual(mirror_map.floor_map, floor_map)

    def test_current_node_fallback_keeps_bus_row_and_route(self):
        bus = search_road.Node((0, 0), "bus", (0, 0))
        battle = search_road.Node((-1, 1), "battle", (1, 0))
        mirror_map = search_road.MirrorMap(hard_mode=True, bus_row=0)
        route = [bus, battle]
        floor_map = {node.coord: node for node in route}
        mirror_map.floor_route = route
        mirror_map.floor_map = floor_map

        with (
            patch.object(search_road.cfg, "mirror_keyboard_navigation", False),
            patch.object(mirror_map, "_get_next_node_position", return_value=(100, 100)),
            patch.object(search_road.auto, "mouse_action_with_pos"),
            patch.object(search_road.auto, "click_element", return_value=False),
            patch.object(search_road.auto, "find_element", return_value=False),
            patch.object(search_road, "sleep"),
        ):
            result = mirror_map.enter_next_node("D")

        self.assertTrue(result)
        self.assertEqual(mirror_map.bus_row, 0)
        self.assertEqual(mirror_map.floor_route, route)
        self.assertEqual(mirror_map.floor_map, floor_map)

    def test_new_floor_keeps_navigation_cache(self):
        mirror_map = search_road.MirrorMap(floor=2, bus_row=-1)
        cached_route = [object()]
        cached_map = {(0, 0): object()}
        mirror_map.floor_route = cached_route
        mirror_map.floor_map = cached_map

        mirror_map.refresh_floor(3)

        self.assertEqual(mirror_map.floor, 3)
        self.assertEqual(mirror_map.bus_row, -1)
        self.assertIs(mirror_map.floor_route, cached_route)
        self.assertIs(mirror_map.floor_map, cached_map)

    def test_keyboard_flow_updates_and_consumes_normal_route(self):
        bus = search_road.Node((0, 0), "bus", (0, 0))
        battle = search_road.Node((0, 1), "battle", (1, 0))
        mirror_map = search_road.MirrorMap()
        mirror_map.floor_route = [bus, battle]

        with (
            patch.object(search_road.cfg, "mirror_keyboard_navigation", True),
            patch.object(search_road.auto, "key_press") as key_press,
            patch.object(search_road.auto, "click_element", return_value=True),
            patch.object(search_road, "_wait_page_load", return_value="mirror/road_in_mir/event_in_assets.png"),
            patch.object(search_road, "sleep") as wait,
        ):
            result = mirror_map.enter_next_node("M")

        self.assertTrue(result)
        self.assertEqual(key_press.call_args_list, [call("right")])
        self.assertEqual(wait.call_args_list, [call(1.25)])
        self.assertEqual(mirror_map.floor_route, [battle])

    def test_hard_mode_rebuilds_and_replaces_map_on_every_decision(self):
        first_bus = search_road.Node((0, 0), "bus", (0, 0))
        first_event = search_road.Node((1, 1), "event", (1, 1))
        first_route = [first_bus, first_event]
        first_map = {node.coord: node for node in first_route}
        second_bus = search_road.Node((0, 0), "bus", (0, 0))
        second_battle = search_road.Node((-1, 1), "battle", (1, -1))
        second_route = [second_bus, second_battle]
        second_map = {node.coord: node for node in second_route}
        mirror_map = search_road.MirrorMap(hard_mode=True)

        with patch.object(
            search_road,
            "search_road_from_road_map",
            side_effect=[
                (first_route, first_map, 0),
                (second_route, second_map, 0),
            ],
        ) as build_route:
            self.assertEqual(mirror_map.get_next_node_direction(), "U")
            self.assertEqual(mirror_map.get_next_node_direction(), "D")

        self.assertIsNone(build_route.call_args_list[0].kwargs["bus_row"])
        self.assertEqual(build_route.call_args_list[1].kwargs["bus_row"], 0)
        self.assertEqual(mirror_map.floor_route, second_route)
        self.assertEqual(mirror_map.floor_map, second_map)

    def test_normal_mode_rebuild_uses_cached_bus_row(self):
        bus = search_road.Node((-1, 0), "bus", (0, 0))
        event = search_road.Node((-1, 1), "event", (1, 0))
        route = [bus, event]
        floor_map = {node.coord: node for node in route}
        mirror_map = search_road.MirrorMap(hard_mode=False, bus_row=-1)

        with patch.object(
            search_road,
            "search_road_from_road_map",
            return_value=(route, floor_map, -1),
        ) as rebuild:
            self.assertEqual(mirror_map.get_next_node_direction(), "M")

        self.assertEqual(rebuild.call_args.kwargs["bus_row"], -1)
        self.assertEqual(mirror_map.floor_route, route)
        self.assertEqual(mirror_map.floor_map, floor_map)

    def test_invalid_onnx_route_uses_first_direction(self):
        mirror_map = search_road.MirrorMap()
        with (
            patch.object(
                search_road,
                "search_road_from_road_map",
                return_value=([], {}, 0),
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
                return_value=([], {}, 0),
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


class MirrorSearchTest(unittest.TestCase):
    def test_hard_mode_skips_legacy_enter_shortcut_and_uses_mirror_map(self):
        from tasks.mirror import mirror as mirror_task

        mirror = mirror_task.Mirror.__new__(mirror_task.Mirror)
        mirror.mirror_map = Mock(hard_mode=True)
        mirror.mirror_map.get_next_node_direction.return_value = "D"
        mirror.mirror_map.enter_next_node.return_value = "entered"

        with (
            patch.object(mirror_task.auto, "find_element") as find_bus,
            patch.object(mirror_task.auto, "click_element") as click_enter,
        ):
            result = mirror.search_road()

        self.assertEqual(result, "entered")
        find_bus.assert_not_called()
        click_enter.assert_not_called()
        mirror.mirror_map.get_next_node_direction.assert_called_once_with()
        mirror.mirror_map.enter_next_node.assert_called_once_with("D")

    def test_normal_mode_also_skips_legacy_shortcut_and_uses_mirror_map(self):
        from tasks.mirror import mirror as mirror_task

        mirror = mirror_task.Mirror.__new__(mirror_task.Mirror)
        mirror.mirror_map = Mock(hard_mode=False)
        mirror.mirror_map.get_next_node_direction.return_value = "M"
        mirror.mirror_map.enter_next_node.return_value = "entered"

        with (
            patch.object(mirror_task.auto, "find_element") as find_bus,
            patch.object(mirror_task.auto, "click_element") as click_enter,
        ):
            result = mirror.search_road()

        self.assertEqual(result, "entered")
        find_bus.assert_not_called()
        click_enter.assert_not_called()
        mirror.mirror_map.get_next_node_direction.assert_called_once_with()
        mirror.mirror_map.enter_next_node.assert_called_once_with("M")


if __name__ == "__main__":
    unittest.main()
