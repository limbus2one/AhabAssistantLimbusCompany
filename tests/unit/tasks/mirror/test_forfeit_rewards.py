from unittest.mock import patch

import pytest

from module.my_error.my_error import cannotOperateGameError
from tasks.mirror.mirror import Mirror


def test_forfeit_rewards_uses_scaled_offset_and_never_falls_back_to_consuming_modules():
    mirror = Mirror.__new__(Mirror)

    with (
        patch("tasks.mirror.mirror.auto.click_element", return_value=True),
        patch("tasks.mirror.mirror.auto.find_element", return_value=(1000, 400)),
        patch("tasks.mirror.mirror.auto.mouse_click") as click,
        patch("tasks.mirror.mirror.cfg.set_win_size", 720),
        patch("tasks.mirror.mirror.sleep"),
    ):
        assert mirror._forfeit_mirror_rewards()
        click.assert_called_once_with(600, 400)

    with (
        patch("tasks.mirror.mirror.auto.click_element", return_value=True),
        patch("tasks.mirror.mirror.auto.find_element", return_value=None),
        patch("tasks.mirror.mirror.auto.mouse_click") as click,
        patch("tasks.mirror.mirror.sleep"),
        pytest.raises(cannotOperateGameError, match="避免消耗脑啡肽模块"),
    ):
        mirror._forfeit_mirror_rewards()

    click.assert_not_called()
