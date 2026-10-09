# ruff: noqa: E402, T201
"""在战斗待机页验证黑兽浮士德 Hollow；下一回合或奖励页出现即接管。"""

import argparse
import importlib
import logging
import sys
import threading
from datetime import datetime
from pathlib import Path
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication

app = QApplication([])
from module.automation import auto
from module.config import cfg
from module.logger import log
from tasks.base.script_task_scheme import init_game

battle_module = importlib.import_module("tasks.battle.battle")
from tasks.battle.ego_release import prepare_faust_ego
from tasks.mirror.mirror import Mirror
from utils.path_manager import path_manager

p = argparse.ArgumentParser(description=__doc__)
p.add_argument("--corrosion", action="store_true")
p.add_argument("--speedup", action="store_true")
p.add_argument("--queue-only", action="store_true")
args = p.parse_args()
out = Path("logs/ego_experiment") / datetime.now().strftime("release_%Y%m%d_%H%M%S")
out.mkdir(parents=True)
logging.basicConfig(level=logging.INFO)
log.setLevel(logging.INFO)
log.addHandler(logging.FileHandler(out / "script.log", encoding="utf-8"))


class Stop(BaseException):
    pass


path_manager.initialize_paths()
init_game()
auto.take_screenshot()
if not (auto.find_element("battle/win_rate_assets.png") or auto.find_element("battle/more_information_assets.png")):
    raise SystemExit("必须从战斗待机页启动")
from adbutils import adb

device = adb.device("127.0.0.1:16384")
done = threading.Event()
recording = None


def record():
    i = 0
    while not done.is_set():
        device.screenshot().save(out / f"frame_{i:04}.png")
        i += 1
        done.wait(0.35)


queued = False
playback = False
deadline = monotonic() + 300
original_find = auto.find_element
original_operation = battle_module.Battle._battle_operation


def operation(self, *positional, **kwargs):
    if queued:
        if playback:
            raise Stop("NEXT_PLANNING")
        return False  # Enter 后的旧待机帧不能再次提交操作。
    return original_operation(self, *positional, **kwargs)


battle_module.Battle._battle_operation = operation
original_prepare = battle_module.prepare_faust_ego
drag_count = 0
original_drag = auto.mouse_drag
original_screenshot = auto.take_screenshot
rgb_count = 0


def screenshot(*positional, **kwargs):
    global rgb_count
    result = original_screenshot(*positional, **kwargs)
    if kwargs.get("gray") is False and result is not None:
        result.save(out / f"rgb_{rgb_count:02}.png")
        print("RGB_FRAME", rgb_count, result.mode, result.size, "scale", cfg.set_win_size / 1440, flush=True)
        rgb_count += 1
    return result


auto.take_screenshot = screenshot


def prepare(corrosion):
    global queued, recording
    result = original_prepare(corrosion)
    device.screenshot().save(out / "queue_result.png")
    print("QUEUE_RESULT", result, flush=True)
    if not result:
        raise Stop("QUEUE_FAILED")
    if result:
        queued = True
        recording = threading.Thread(target=record, daemon=True)
        recording.start()
    return result


def find(target, *positional, **kwargs):
    global playback
    if monotonic() > deadline:
        raise Stop("TIME_LIMIT")
    result = original_find(target, *positional, **kwargs)
    if target.startswith("mirror/claim_reward/") and result:
        raise Stop("STOP_BEFORE_REWARD")
    if queued and result:
        if target in (
            "battle/pause_assets.png",
            "battle/ego_release/ego_animation.png",
            "battle/ego_release/ego_corrosion_animation.png",
            "battle/ego_release/overclock_animation.png",
        ):
            playback = True
        if playback and target in ("battle/win_rate_assets.png", "battle/more_information_assets.png"):
            raise Stop("NEXT_PLANNING")
    return result


def drag(*positional, **kwargs):
    global drag_count
    start = monotonic()
    result = original_drag(*positional, **kwargs)
    log.info("长按/拖动 %s %s 实际 %.3fs", positional, kwargs, monotonic() - start)
    device.screenshot().save(out / f"drag_{drag_count:02}.png")
    drag_count += 1
    return result


auto.find_element = find
auto.mouse_drag = drag
battle_module.prepare_faust_ego = prepare
team = cfg.config.teams["1"].model_copy(deep=True)
team.use_ego = True
team.ego_corrosion = args.corrosion
team.ego_speedup = args.speedup
team.defense_for_solo = False
team.defense_first_round = False
try:
    if args.queue_only:
        auto.key_press("p")
        print("QUEUE_RESULT", prepare_faust_ego(args.corrosion), flush=True)
    else:
        Mirror(team, 1)._fight()
        print("ENCOUNTER_RETURNED", flush=True)
except Stop as exc:
    print(exc, flush=True)
finally:
    done.set()
    if recording:
        recording.join(5)
    device.screenshot().save(out / "stopped.png")
    print("EVIDENCE", out, flush=True)
