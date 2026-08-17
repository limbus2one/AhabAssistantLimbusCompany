from module.config.config_typing import TeamSetting
from tasks.battle.battle import DefenseForSoloState


def test_custom_defense_for_solo_turns() -> None:
    state = DefenseForSoloState(TeamSetting(defense_for_solo_turns=3).defense_for_solo_turns)

    for _ in range(3):
        state.consume_turn()

    assert (state.total_turns, state.completed_turns, state.remaining_turns) == (3, 3, 0)
