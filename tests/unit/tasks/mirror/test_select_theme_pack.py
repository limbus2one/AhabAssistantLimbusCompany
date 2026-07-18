import unittest
from unittest.mock import patch

from module.automation import TextMatchResult
from tasks.mirror import select_theme_pack as select_theme_pack_module


class SelectThemePackTest(unittest.TestCase):
    def test_returns_selected_theme_pack_name(self):
        def find_element(target, *args, **kwargs):
            if target == "mirror/theme_pack/normal_assets.png":
                return (100, 100)
            if target == "mirror/theme_pack/theme_pack_features.png":
                return [(500, 500)]
            return None

        match = TextMatchResult(value=10, text="时间杀人时间", position=[500, 500])
        with (
            patch.object(select_theme_pack_module.path_manager, "current_language", "zh_cn"),
            patch.object(
                select_theme_pack_module.theme_list,
                "get_effective_theme_pack_list",
                return_value={"时间杀人时间": 10},
            ),
            patch.object(select_theme_pack_module.theme_list, "preferred_thresholds", 5),
            patch.object(select_theme_pack_module.auto, "take_screenshot", return_value=object()),
            patch.object(select_theme_pack_module.auto, "find_element", side_effect=find_element),
            patch.object(select_theme_pack_module.auto, "click_element", return_value=False),
            patch.object(select_theme_pack_module.auto, "find_language_text", return_value=match),
            patch.object(select_theme_pack_module.auto, "mouse_drag_down"),
            patch.object(select_theme_pack_module, "sleep"),
        ):
            result = select_theme_pack_module.select_theme_pack(
                hard_switch=False,
                floor=1,
                team_num=3,
                use_custom_theme_pack_weight=False,
            )

        self.assertEqual(result, "时间杀人时间")

    def test_returns_recognized_event_theme_pack_name(self):
        def find_element(target, *args, **kwargs):
            if target == "mirror/theme_pack/normal_assets.png":
                return (100, 100)
            if target == "mirror/theme_pack/theme_pack_features.png":
                return [(500, 500), (900, 500)]
            return None

        match = TextMatchResult(value=10, text="活动：瓦尔普吉斯之夜", position=[500, 500])
        with (
            patch.object(select_theme_pack_module.path_manager, "current_language", "zh_cn"),
            patch.object(
                select_theme_pack_module.theme_list,
                "get_effective_theme_pack_list",
                return_value={"活动：瓦尔普吉斯之夜": 10},
            ),
            patch.object(select_theme_pack_module.cfg, "select_event_pack", True),
            patch.object(select_theme_pack_module.auto, "take_screenshot", return_value=object()),
            patch.object(select_theme_pack_module.auto, "find_element", side_effect=find_element),
            patch.object(select_theme_pack_module.auto, "click_element", return_value=False),
            patch.object(select_theme_pack_module.auto, "find_language_text", return_value=match),
            patch.object(select_theme_pack_module.auto, "mouse_drag_down"),
            patch.object(select_theme_pack_module, "sleep"),
        ):
            result = select_theme_pack_module.select_theme_pack(
                hard_switch=False,
                floor=4,
                team_num=3,
                use_custom_theme_pack_weight=False,
            )

        self.assertEqual(result, "活动：瓦尔普吉斯之夜")


if __name__ == "__main__":
    unittest.main()
