"""运行现有镜牢脚本，在指定楼层选包入口接管；奖励界面出现即停止。"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

from module.automation import auto  # noqa: E402
from module.config import cfg  # noqa: E402
from module.logger import log  # noqa: E402
from tasks.base.script_task_scheme import init_game  # noqa: E402
from tasks.mirror import mirror as mirror_module  # noqa: E402
from tasks.mirror.select_theme_pack import read_english_theme_packs  # noqa: E402
from utils.path_manager import path_manager  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--hard", action="store_true")
parser.add_argument("--team", type=int, default=1)
parser.add_argument("--target", help="指定楼层观测的卡包编号")
parser.add_argument("--target-floor", type=int, choices=range(1, 6), default=1)
parser.add_argument("--stop-floor", type=int, choices=range(1, 6), default=2)
parser.add_argument("--observe-only", action="store_true", help="观测后停止，不拖动卡包")
parser.add_argument("--select-only", action="store_true", help="选包到达地图后停止，不进行本层战斗")
args = parser.parse_args()
out = Path("logs/theme_pack_experiment") / datetime.now().strftime("run_%Y%m%d_%H%M%S")
out.mkdir(parents=True)
logging.basicConfig(level=logging.INFO)
log.setLevel(logging.INFO)
log.addHandler(logging.FileHandler(out / "script.log", encoding="utf-8"))
events = []


class Takeover(BaseException):
    pass


original_find = auto.find_element


def guarded_find(target, *positional, **kwargs):
    result = original_find(target, *positional, **kwargs)
    if target.startswith("mirror/claim_reward/") and result:
        raise Takeover("STOP_BEFORE_REWARD")
    return result


original_select = mirror_module.select_theme_pack
original_observe = mirror_module.observe_theme_pack


def record_observation(pack_id, hard_mode, floor):
    original_screenshot = auto.take_screenshot

    def capture(*positional, **kwargs):
        image = original_screenshot(*positional, **kwargs)
        if image is not None and kwargs.get("gray") is False:
            image.save(out / f"floor_{floor}_observation_rgb.png")
        return image

    auto.take_screenshot = capture
    try:
        result = original_observe(pack_id, hard_mode, floor)
    finally:
        auto.take_screenshot = original_screenshot
    events.append({"kind": "observation", "floor": floor, "hard": hard_mode, "id": pack_id, "result": result})
    auto.take_screenshot(gray=False).save(out / f"floor_{floor}_after_observation.png")
    auto.take_screenshot()
    return result


def select_or_takeover(hard_mode, floor, *positional, **kwargs):
    choices = read_english_theme_packs()
    events.append(
        {
            "floor": floor,
            "hard": hard_mode,
            "choices": [
                {"id": match.pack_id, "score": match.score, "gap": match.gap} for match, _, _ in choices if match
            ],
        }
    )
    auto.take_screenshot(gray=False).save(out / f"floor_{floor}_before_select.png")
    if args.observe_only or (not args.select_only and floor >= args.stop_floor):
        raise Takeover(f"TAKEOVER_AT_FLOOR_{floor}")
    result = original_select(hard_mode, floor, *positional, **kwargs)
    events[-1]["selected"] = result
    auto.take_screenshot(gray=False).save(out / f"floor_{floor}_after_select.png")
    auto.take_screenshot()
    if args.select_only:
        raise Takeover(f"STOP_AFTER_SELECTION_AT_FLOOR_{floor}")
    return result


cfg.config.hard_mirror = args.hard
cfg.config.auto_hard_mirror = False
cfg.config.floor_3_exit = False
path_manager.initialize_paths()
init_game()
auto.take_screenshot()
auto.find_element = guarded_find
mirror_module.select_theme_pack = select_or_takeover
mirror_module.observe_theme_pack = record_observation
team = cfg.config.teams[str(args.team)].model_copy(deep=True)
team.opening_bonus = [0] * 10
team.observe_ego_gift = False
team.defense_for_solo = False
team.normal_to_hard_floor = 0
team.use_team_code = False
if args.target:
    team.observe_theme_pack = True
    team.observe_theme_pack_selected = [""] * 5
    team.observe_theme_pack_selected[args.target_floor - 1] = args.target
try:
    result = mirror_module.Mirror(team, args.team).run()
    log.info(f"镜牢脚本返回: {result}")
except Takeover as exc:
    log.info(str(exc))
    auto.take_screenshot(gray=False).save(out / "takeover.png")
finally:
    (out / "events.json").write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"实验记录: {out}")
