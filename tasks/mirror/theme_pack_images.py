"""英文卡包截图模板；同时比较封面和英文名称，未知或相似结果拒绝命中。"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

# main.py 将工作目录设为程序目录；发布包的 assets 与 exe 并列。
ASSET_DIR = Path("assets/theme_packs")
MIN_SCORE = 0.90
MIN_GAP = 0.03


@lru_cache
def catalog():
    return json.loads((ASSET_DIR / "catalog.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1024)
def template(pack_id, width, variant="en"):
    with Image.open(ASSET_DIR / variant / f"{pack_id}.png") as image:
        image = image.resize((width, round(image.height * width / image.width)), Image.Resampling.LANCZOS)
        # 去掉挂钩、观测图标和选中光圈，保留封面主体及英文名称。
        border = round(10 * width / 192)
        top = round(100 * width / 192)
        bottom = round(336 * width / 192)
        core = np.array(image.convert("L"))[top:bottom, border:-border]
        return core, (border, top)


def grayscale(image):
    if isinstance(image, Image.Image):
        return np.array(image.convert("L"))
    image = np.asarray(image)
    return cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image


@dataclass(frozen=True)
class PackMatch:
    pack_id: str
    position: tuple[int, int]
    score: float
    gap: float


def variants(pack_id):
    return ("en", "choice") if "choice_source" in catalog()["packs"][pack_id] else ("en",)


def _rank_matches(screen, width):
    scores = []
    for pack_id in catalog()["packs"]:
        best = None
        for variant in variants(pack_id):
            core, offset = template(pack_id, width, variant)
            if screen.shape[0] < core.shape[0] or screen.shape[1] < core.shape[1]:
                continue
            _, score, _, point = cv2.minMaxLoc(cv2.matchTemplate(screen, core, cv2.TM_CCOEFF_NORMED))
            if np.isfinite(score) and (best is None or score > best[0]):
                best = (score, pack_id, point, offset)
        if best is not None:
            scores.append(best)
    scores.sort(reverse=True)
    return scores


def recognize_pack(image, width):
    """识别单个卡包区域；与全部卡包比较，拒绝相似或未知封面。"""
    screen = grayscale(image)
    scores = _rank_matches(screen, width)
    if not scores:
        return None
    score, pack_id, point, offset = scores[0]
    gap = score - scores[1][0] if len(scores) > 1 else score
    if score < MIN_SCORE or gap < MIN_GAP:
        return None
    return PackMatch(
        pack_id, (point[0] - offset[0] + width // 2, point[1] - offset[1] + round(178 * width / 192)), score, gap
    )


def find_pack(image, pack_id, width):
    """先定位指定模板，再以全部卡包复核该位置，避免相似封面误选。"""
    screen = grayscale(image)
    best = None
    for variant in variants(pack_id):
        core, offset = template(pack_id, width, variant)
        if screen.shape[0] < core.shape[0] or screen.shape[1] < core.shape[1]:
            continue
        _, score, _, point = cv2.minMaxLoc(cv2.matchTemplate(screen, core, cv2.TM_CCOEFF_NORMED))
        if np.isfinite(score) and (best is None or score > best[0]):
            best = (score, point, offset)
    if best is None or best[0] < MIN_SCORE:
        return None
    _, point, offset = best
    left = max(0, point[0] - offset[0] - 4)
    top = max(0, point[1] - offset[1] - 4)
    right = min(screen.shape[1], point[0] - offset[0] + width + 4)
    bottom = min(screen.shape[0], point[1] - offset[1] + round(356 * width / 192) + 4)
    verified = recognize_pack(screen[top:bottom, left:right], width)
    if verified is None or verified.pack_id != pack_id:
        return None
    return PackMatch(pack_id, (verified.position[0] + left, verified.position[1] + top), verified.score, verified.gap)


def selected_border(image, match, width):
    """观测确认前检查目标卡包的金色选中边框，防止沿用其他卡包的确认按钮。"""
    rgb = np.asarray(image.convert("RGB"), dtype=float)
    r, g, b = rgb.transpose(2, 0, 1)
    gold = (r > 180) & (g > 90) & (g < r * 0.85) & (b < g * 0.5)
    x, y = match.position
    half_height = round(178 * width / 192)
    left, right = x - width // 2, x + width // 2
    top, bottom = y - half_height, y + half_height
    margin = max(4, round(width / 24))
    boxes = [
        (left - margin, top, left + margin, bottom),
        (right - margin, top, right + margin, bottom),
        (left, top - margin, right, top + margin),
        (left, bottom - margin, right, bottom + margin),
    ]
    sides = [gold[max(0, y1) : max(0, y2), max(0, x1) : max(0, x2)] for x1, y1, x2, y2 in boxes]
    return sum(side.size > 0 and side.mean() > 0.12 for side in sides) >= 3


def find_selected_pack(image, pack_id, width):
    """选中态容许一像素栅格尺寸误差；每个尺寸仍核验编号、分数及金色边框。"""
    for selected_width in (width, width - 1, width + 1):
        match = find_pack(image, pack_id, selected_width)
        if match is not None and selected_border(image, match, selected_width):
            return match, selected_width
    return None
