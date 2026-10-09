"""E.G.O. 长按回归；不连接模拟器。"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

from module.automation.input_handlers.simulator.mumu_control import MumuControl


def test_long_press_duration_and_release():
    touch = SimpleNamespace(down=Mock(), up=Mock())
    with patch("module.automation.input_handlers.simulator.mumu_control.time.sleep") as wait:
        MumuControl.mouse_drag(touch, 300, 400, drag_time=1.2)
    touch.down.assert_called_once_with(300, 400)
    wait.assert_called_once_with(1.2)
    touch.up.assert_called_once_with()
    touch.up.reset_mock()
    with patch("module.automation.input_handlers.simulator.mumu_control.time.sleep", side_effect=InterruptedError):
        try:
            MumuControl.mouse_drag(touch, 300, 400, drag_time=2)
        except InterruptedError:
            pass
        else:
            raise AssertionError("长按中断必须传回调用者")
    touch.up.assert_called_once_with()


def test_speedup_only_during_ego_animation():
    from module.config import cfg
    from tasks.battle.battle import Battle

    for state, enabled in (("ego", True), ("corrosion", True), ("ego", False), ("combat", True), ("planning", True)):
        battle = Battle(is_tool=True)
        battle.ego_used = True
        legend_checks = 0

        def find(target, **kwargs):
            nonlocal legend_checks
            if target == "mirror/road_in_mir/legend_assets.png":
                legend_checks += 1
                return legend_checks > 1
            expected = {
                "ego": "battle/ego_release/ego_animation.png",
                "corrosion": "battle/ego_release/overclock_animation.png",
                "combat": "battle/pause_assets.png",
                "planning": "battle/win_rate_assets.png",
            }[state]
            return target == expected

        fake = SimpleNamespace(
            take_screenshot=lambda: True,
            get_restore_time=lambda: None,
            find_element=find,
            mouse_drag=Mock(),
            click_element=Mock(return_value=False),
        )
        with (
            patch("tasks.battle.battle.auto", fake),
            patch("tasks.battle.battle.sleep"),
            patch.object(battle, "_battle_operation"),
            patch("tasks.battle.battle.retry", return_value=True),
        ):
            battle.fight(ego_speedup=enabled)
        assert not battle.ego_used
        if state in ("ego", "corrosion") and enabled:
            scale = cfg.set_win_size / 1440
            fake.mouse_drag.assert_called_once_with(1280 * scale, 720 * scale, drag_time=2)
        else:
            fake.mouse_drag.assert_not_called()


def test_ego_once_per_battle_and_verification_failure_stops_enter():
    from tasks.battle.battle import Battle

    fake = SimpleNamespace(mouse_click_blank=Mock(), key_press=Mock(), find_element=Mock(return_value=False))
    battle = Battle()
    with (
        patch("tasks.battle.battle.auto", fake),
        patch("tasks.battle.battle.sleep"),
        patch("tasks.battle.battle.prepare_faust_ego", return_value=True) as prepare,
    ):
        for _ in range(2):
            battle._battle_operation(False, False, False, use_ego=True, ego_corrosion=True)
        prepare.assert_called_once_with(True)
    assert battle.ego_used
    fake.key_press.reset_mock()
    battle.ego_used = False
    with (
        patch("tasks.battle.battle.auto", fake),
        patch("tasks.battle.battle.prepare_faust_ego", side_effect=RuntimeError("行动槽复核失败")),
    ):
        try:
            battle._battle_operation(False, False, False, use_ego=True)
        except RuntimeError:
            pass
        else:
            raise AssertionError("复核失败后必须停止")
    assert fake.key_press.call_args_list == [(("p",),)]


def test_cropped_matches_use_full_screen_coordinates_and_cancel_unusable_card():
    import tasks.battle.ego_release as release

    for usable in (True, False):
        panel_open = False

        def find(target, my_crop, **kwargs):
            positions = {
                "faust_avatar.png": (543, 993),
                "ego_name.png": (1500, 521),
                "ego_skill.png": (566, 862),
            }
            name = target.rsplit("/", 1)[-1]
            if name == "details.png":
                center = (1600, 215) if panel_open else None
            else:
                center = positions.get(name)
            return (center[0] - my_crop[0], center[1] - my_crop[1]) if center else None

        def drag(x, y, **kwargs):
            nonlocal panel_open
            if kwargs.get("drag_time") == 1.2:
                panel_open = True

        def click(x, y):
            nonlocal panel_open
            if usable or x > 1700:
                panel_open = False

        fake = SimpleNamespace(
            find_element=find,
            take_screenshot=lambda: True,
            mouse_drag=Mock(side_effect=drag),
            mouse_click=Mock(side_effect=click),
            mouse_click_blank=Mock(),
        )
        with (
            patch.object(release, "auto", fake),
            patch.object(release, "cfg", SimpleNamespace(set_win_size=1080)),
            patch.object(release.path_manager, "current_language", "en"),
            patch.object(release.ImageUtils, "existing_image_paths", return_value=["en"]),
            patch.object(release, "_assigned_enemy_target", return_value=(1418, 213)),
            patch.object(release, "sleep"),
        ):
            assert release.prepare_faust_ego() == usable
        assert fake.mouse_drag.call_args_list[0] == ((543, 993), {"drag_time": 1.2})
        assert ((1500, 393.5),) == (fake.mouse_click.call_args_list[0].args,)
        assert not panel_open


def test_enemy_target_follows_real_win_rate_lines_and_restores_grayscale():
    from pathlib import Path

    from PIL import Image

    import tasks.battle.ego_release as release

    root = Path(__file__).parent / "fixtures/ego_release"
    for name, expected in (
        ("target_wide.png", (1418, 213)),
        ("target_focused.png", (963, 459)),
        ("target_unassigned.png", None),
    ):
        frame = Image.new("RGB", (1920, 1080))
        frame.paste(Image.open(root / name), (150, 105))
        fake = SimpleNamespace(screenshot=frame, take_screenshot=lambda **kwargs: frame)
        with patch.object(release, "auto", fake):
            point = release._assigned_enemy_target(0.75)
        assert fake.screenshot.mode == "L"
        if expected:
            assert point and max(abs(a - b) for a, b in zip(point, expected)) < 15
        else:
            assert point is None


def test_target_skill_templates_reject_ordinary_skills():
    from pathlib import Path

    import cv2
    import numpy as np
    from PIL import Image

    root = Path(__file__).resolve().parents[1]
    assets = root / "assets/images/default/share/battle/ego_release"
    fixtures = root / "tests/fixtures/ego_release"
    for names, positive in (
        (("ego_skill.png", "ego_skill_center.png"), "le_trou.png"),
        (("overclock_slot.png",), "hollow_overclock.png"),
    ):
        for fixture, expected in ((positive, True), ("heishou_ordinary_skills.png", False)):
            image = np.array(Image.open(fixtures / fixture).convert("L"))
            scores = []
            for name in names:
                marker = np.array(Image.open(assets / name).convert("L"))
                marker = cv2.resize(marker, None, fx=0.75, fy=0.75, interpolation=cv2.INTER_AREA)
                scores.append(cv2.matchTemplate(image, marker, cv2.TM_CCOEFF_NORMED).max())
            assert bool(max(scores) >= 0.90) == expected, (fixture, scores)


def test_unavailable_ego_does_not_toggle_win_rate_twice():
    from tasks.battle.battle import Battle

    fake = SimpleNamespace(mouse_click_blank=Mock(), key_press=Mock(), find_element=Mock(return_value=False))
    with (
        patch("tasks.battle.battle.auto", fake),
        patch("tasks.battle.battle.sleep"),
        patch("tasks.battle.battle.prepare_faust_ego", return_value=False),
    ):
        Battle()._battle_operation(False, False, False, use_ego=True)
    assert fake.key_press.call_args_list == [(("p",),), (("enter",),)]


def test_animation_templates_reject_planning_and_card_preview():
    from pathlib import Path

    import cv2
    import numpy as np
    from PIL import Image

    root = Path(__file__).resolve().parents[1]
    assets = root / "assets/images/default"
    fixtures = root / "tests/fixtures/ego_release"
    cases = (
        ("en/battle/ego_release/ego_animation.png", "normal_animation.png"),
        ("en/battle/ego_release/ego_corrosion_animation.png", "corrosion_title.png"),
        ("share/battle/ego_release/overclock_animation.png", "corrosion_animation.png"),
    )
    for template, positive in cases:
        marker = np.array(Image.open(assets / template).convert("L"))
        marker = cv2.resize(marker, None, fx=0.75, fy=0.75, interpolation=cv2.INTER_AREA)
        for fixture, expected in ((positive, True), ("planning_header.png", False), ("card_preview.png", False)):
            frame = np.array(Image.open(fixtures / fixture).convert("L"))
            score = cv2.matchTemplate(frame, marker, cv2.TM_CCOEFF_NORMED).max()
            assert bool(score >= 0.90) == expected, (template, fixture, score)
