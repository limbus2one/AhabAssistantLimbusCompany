from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path


LABEL_ROOT = Path(r"E:\mirror_yolo\curated\labels")
BACKUP_ROOT = Path(r"E:\mirror_yolo\backups\flagged_fix_20260809")


@dataclass
class Box:
    cls: int
    x: float
    y: float
    w: float
    h: float

    @classmethod
    def parse(cls, line: str) -> "Box":
        values = line.split()
        return cls(int(values[0]), *(float(value) for value in values[1:5]))

    def dump(self) -> str:
        return f"{self.cls} {self.x:.6f} {self.y:.6f} {self.w:.6f} {self.h:.6f}"


def name(group: str, shift: int | None = None) -> str:
    suffix = "" if shift is None else f"_shift_{shift}"
    return f"onnx_nodes_{group}{suffix}"


def add_box(
    additions: dict[str, list[Box]],
    stem: str,
    cls: int,
    x: float,
    y: float,
    w: float,
    h: float,
) -> None:
    additions.setdefault(stem, []).append(Box(cls, x, y, w, h))


def add_spring_row(
    additions: dict[str, list[Box]], group: str, shift: int, nodes: list[tuple[int, float]]
) -> None:
    sizes = {
        1: (0.090, 0.075),
        2: (0.060, 0.105),
        4: (0.065, 0.070),
        5: (0.075, 0.060),
    }
    for cls, x in nodes:
        w, h = sizes[cls]
        add_box(additions, name(group, shift if shift else None), cls, x, 0.810, w, h)


def build_operations() -> tuple[list[tuple[str, int, float, float, int]], dict[str, list[Box]]]:
    reclasses: list[tuple[str, int, float, float, int]] = [
        (name("1786006481229511700"), 4, 0.458, 0.215, 6),
        (name("1786006930287845100"), 4, 0.655, 0.220, 6),
        (name("1786008931791690200"), 4, 0.858, 0.810, 6),
        (name("1786030995007210700", 2), 4, 0.069, 0.216, 0),
        (name("1786082166831337100"), 4, 0.858, 0.216, 6),
        (name("1786086456169577700"), 4, 0.457, 0.214, 6),
        (name("1786088079890909400", 1), 3, 0.260, 0.512, 4),
        (name("1786100607536402900"), 1, 0.856, 0.815, 6),
        (name("1786100607536402900", 2), 1, 0.475, 0.817, 6),
        (name("1786100607536402900", 3), 4, 0.282, 0.816, 6),
        (name("1786100607536402900", 4), 1, 0.095, 0.817, 6),
        (name("1786111780509329500", 2), 4, 0.486, 0.816, 3),
        (name("1786122306667825700"), 1, 0.462, 0.810, 4),
        (name("1786122306667825700", 1), 1, 0.271, 0.811, 4),
        (name("1786122306667825700", 2), 1, 0.087, 0.810, 4),
    ]
    additions: dict[str, list[Box]] = {}

    # 普通地图中的漏框。
    add_box(additions, name("1785995891721931500"), 4, 0.255, 0.513, 0.058, 0.065)
    add_box(additions, name("1786004554240052300"), 3, 0.266, 0.215, 0.052, 0.056)
    add_box(additions, name("1786008239434095700"), 4, 0.254, 0.514, 0.058, 0.065)
    add_box(additions, name("1786089326820321500", 1), 4, 0.060, 0.514, 0.055, 0.061)

    for shift, x in [(None, 0.254), (1, 0.060)]:
        stem = name("1786093270560524600", shift)
        add_box(additions, stem, 3, x, 0.217, 0.055, 0.060)
        add_box(additions, stem, 3, x, 0.514, 0.055, 0.060)

    add_box(additions, name("1786095101262490700"), 3, 0.454, 0.515, 0.047, 0.054)
    add_box(additions, name("1786097876723582800", 1), 2, 0.065, 0.210, 0.041, 0.065)
    add_box(additions, name("1786097876723582800", 2), 2, 0.071, 0.210, 0.041, 0.065)
    add_box(additions, name("1786098284818809300"), 3, 0.258, 0.215, 0.056, 0.062)
    add_box(additions, name("1786098284818809300", 1), 3, 0.067, 0.215, 0.056, 0.062)
    add_box(additions, name("1786105339520720500"), 6, 0.868, 0.211, 0.060, 0.067)
    add_box(additions, name("1786105960614011300"), 6, 0.255, 0.214, 0.060, 0.067)
    add_box(additions, name("1786107040882278400"), 6, 0.055, 0.214, 0.060, 0.067)

    # Spring Cultivation：event, event, risky, shop, boss 的单行地图。
    add_spring_row(additions, "1786013773379653700", 0, [(2, 0.257), (2, 0.457), (4, 0.657), (5, 0.857)])
    add_spring_row(additions, "1786021132521956200", 0, [(4, 0.257), (5, 0.457), (1, 0.657)])
    add_spring_row(additions, "1786021489024020600", 0, [(5, 0.257), (1, 0.457)])
    add_spring_row(additions, "1786021502366117000", 0, [(1, 0.257)])

    spring_sequence = {
        0: [(2, 0.257), (2, 0.457), (4, 0.657), (5, 0.857)],
        1: [(2, 0.067), (2, 0.267), (4, 0.467), (5, 0.667), (1, 0.867)],
        2: [(2, 0.077), (4, 0.277), (5, 0.477), (1, 0.677)],
        3: [(4, 0.087), (5, 0.287), (1, 0.487)],
        4: [(5, 0.097), (1, 0.297)],
        5: [(1, 0.107)],
        6: [(1, 0.107)],
    }
    for group in ("1786115395325190000", "1786118351839218800"):
        for shift, nodes in spring_sequence.items():
            add_spring_row(additions, group, shift, nodes)

    # The Dusk of Amber 的圆盘地图。
    dusk_event = (0.066, 0.082)
    dusk_shop = (0.066, 0.060)
    for x in (0.460, 0.660):
        add_box(additions, name("1786103307828013200"), 2, x, 0.512, *dusk_event)
    for x in (0.260, 0.460):
        add_box(additions, name("1786103408805928300"), 2, x, 0.512, *dusk_event)
    add_box(additions, name("1786103408805928300"), 5, 0.852, 0.810, *dusk_shop)
    add_box(additions, name("1786103441551340800"), 2, 0.260, 0.512, *dusk_event)
    add_box(additions, name("1786103441551340800"), 5, 0.655, 0.810, *dusk_shop)

    add_box(additions, name("1786107990354509500"), 2, 0.257, 0.216, *dusk_event)
    add_box(additions, name("1786107990354509500"), 2, 0.050, 0.805, *dusk_event)
    add_box(additions, name("1786107990354509500"), 5, 0.655, 0.512, *dusk_shop)
    add_box(additions, name("1786108018906355900"), 5, 0.455, 0.811, *dusk_shop)
    add_box(additions, name("1786108833403690400"), 2, 0.160, 0.704, *dusk_event)
    add_box(additions, name("1786108833403690400"), 5, 0.567, 0.400, *dusk_shop)
    add_box(additions, name("1786108947681315300"), 5, 0.257, 0.800, *dusk_shop)

    dusk_group = "1786111780509329500"
    for x in (0.451, 0.651):
        add_box(additions, name(dusk_group), 2, x, 0.512, *dusk_event)
    for cls, x, y in [(2, 0.260, 0.512), (2, 0.460, 0.512), (3, 0.662, 0.812), (5, 0.862, 0.812)]:
        add_box(additions, name(dusk_group, 1), cls, x, y, *(dusk_shop if cls == 5 else ((0.070, 0.064) if cls == 3 else dusk_event)))
    for cls, x, y in [(2, 0.070, 0.512), (2, 0.270, 0.512), (5, 0.686, 0.812)]:
        add_box(additions, name(dusk_group, 2), cls, x, y, *(dusk_shop if cls == 5 else dusk_event))
    for cls, x, y in [(2, 0.080, 0.512), (5, 0.503, 0.812)]:
        add_box(additions, name(dusk_group, 3), cls, x, y, *(dusk_shop if cls == 5 else dusk_event))
    add_box(additions, name(dusk_group, 4), 5, 0.312, 0.812, *dusk_shop)
    add_box(additions, name(dusk_group, 5), 5, 0.120, 0.812, *dusk_shop)

    # Hatred and Despair：盾牌上的问号也是 event。
    shield_event = (0.060, 0.100)
    for group in ("1786114243275263700", "1786120962649811300"):
        for x in (0.260, 0.460):
            add_box(additions, name(group), 2, x, 0.805, *shield_event)
        for x in (0.070, 0.270):
            add_box(additions, name(group, 1), 2, x, 0.805, *shield_event)
        add_box(additions, name(group, 2), 2, 0.080, 0.805, *shield_event)

    # Charm, Wander, Doubt：首帧右侧 risky 被奖励图标遮挡而漏框。
    add_box(additions, name("1786114781025510800"), 4, 0.861, 0.512, 0.059, 0.062)
    add_box(additions, name("1786121623756600300"), 4, 0.867, 0.508, 0.059, 0.062)
    add_box(additions, name("1786125676528353600"), 4, 0.267, 0.513, 0.061, 0.063)
    add_box(additions, name("1786125676528353600"), 4, 0.866, 0.513, 0.059, 0.063)

    # The Noon of Violet：上下问号分支以及后续 shop。
    noon_event = (0.061, 0.104)
    noon_shop = (0.068, 0.060)

    def noon_add(group: str, shift: int, events: list[tuple[float, float]], shop_x: float | None, focused_x: float | None = None) -> None:
        stem = name(group, shift if shift else None)
        for x, y in events:
            add_box(additions, stem, 2, x, y, *noon_event)
        if shop_x is not None:
            add_box(additions, stem, 5, shop_x, 0.507, *noon_shop)
        if focused_x is not None:
            add_box(additions, stem, 3, focused_x, 0.507, 0.078, 0.070)

    group = "1786117495717223200"
    noon_add(group, 0, [(0.468, 0.205), (0.668, 0.205)], None)
    noon_add(group, 1, [(0.277, 0.205), (0.477, 0.205)], 0.877, 0.077)
    noon_add(group, 2, [(0.087, 0.205), (0.287, 0.205), (0.087, 0.798)], 0.684)
    noon_add(group, 3, [(0.097, 0.205)], 0.502)
    noon_add(group, 4, [], 0.326)
    noon_add(group, 5, [], 0.130)

    group = "1786128377360715500"
    noon_add(group, 0, [(0.468, 0.205), (0.668, 0.205)], None)
    noon_add(group, 1, [(0.280, 0.205), (0.480, 0.205)], 0.880)
    noon_add(group, 2, [(0.100, 0.205), (0.300, 0.205), (0.100, 0.798)], 0.700)
    noon_add(group, 3, [(0.110, 0.205), (0.110, 0.798)], 0.510)
    noon_add(group, 4, [], 0.315)
    noon_add(group, 5, [], 0.128)

    # Mnestic Experience：主题化后的 battle/focused/event/risky/shop。
    mnestic = "1786122306667825700"
    battle = (0.058, 0.053)
    focused = (0.070, 0.065)
    event = (0.055, 0.100)
    shop = (0.065, 0.058)
    for cls, x, y, size in [
        (3, 0.253, 0.213, focused), (0, 0.453, 0.213, battle),
        (2, 0.253, 0.511, event), (2, 0.453, 0.511, event),
        (0, 0.253, 0.806, battle), (3, 0.653, 0.806, focused),
    ]:
        add_box(additions, name(mnestic), cls, x, y, *size)
    for cls, x, y, size in [
        (2, 0.060, 0.511, event), (2, 0.260, 0.511, event),
        (3, 0.460, 0.806, focused), (5, 0.860, 0.511, shop),
    ]:
        add_box(additions, name(mnestic, 1), cls, x, y, *size)
    for cls, x, y, size in [
        (0, 0.070, 0.213, battle), (2, 0.070, 0.511, event),
        (3, 0.270, 0.806, focused), (5, 0.670, 0.511, shop),
    ]:
        add_box(additions, name(mnestic, 2), cls, x, y, *size)
    add_box(additions, name(mnestic, 3), 3, 0.080, 0.806, *focused)
    add_box(additions, name(mnestic, 3), 5, 0.480, 0.511, *shop)
    add_box(additions, name(mnestic, 4), 5, 0.300, 0.511, *shop)
    add_box(additions, name(mnestic, 5), 5, 0.110, 0.511, *shop)

    return reclasses, additions


def load_boxes(path: Path) -> list[Box]:
    if not path.exists():
        raise FileNotFoundError(path)
    return [Box.parse(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def distance(a: Box, x: float, y: float) -> float:
    return ((a.x - x) ** 2 + (a.y - y) ** 2) ** 0.5


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="实际写入 curated 标签")
    args = parser.parse_args()

    reclasses, additions = build_operations()
    affected = {stem for stem, *_ in reclasses} | set(additions)
    updated: dict[str, list[Box]] = {}
    report: list[dict[str, object]] = []

    for stem in sorted(affected):
        updated[stem] = load_boxes(LABEL_ROOT / f"{stem}.txt")

    for stem, old_cls, x, y, new_cls in reclasses:
        boxes = updated[stem]
        nearest = min(boxes, key=lambda box: distance(box, x, y))
        d = distance(nearest, x, y)
        if d > 0.04:
            raise RuntimeError(f"{stem}: reclass 未找到目标框，最近距离 {d:.4f}")
        if nearest.cls == new_cls:
            continue
        if nearest.cls != old_cls:
            raise RuntimeError(f"{stem}: 预期类别 {old_cls}，实际为 {nearest.cls}")
        report.append({"file": stem, "action": "reclass", "from": old_cls, "to": new_cls, "x": nearest.x, "y": nearest.y})
        nearest.cls = new_cls

    for stem, new_boxes in additions.items():
        boxes = updated[stem]
        for new_box in new_boxes:
            nearby = [box for box in boxes if distance(box, new_box.x, new_box.y) < 0.025]
            if nearby:
                if any(box.cls == new_box.cls for box in nearby):
                    continue
                raise RuntimeError(
                    f"{stem}: ({new_box.x:.3f}, {new_box.y:.3f}) 已有其他类别 "
                    f"{[box.cls for box in nearby]}，拒绝覆盖"
                )
            boxes.append(new_box)
            report.append({"file": stem, "action": "add", "class": new_box.cls, "x": new_box.x, "y": new_box.y})

    changed_stems = sorted({item["file"] for item in report})
    summary = {
        "changed_files": len(changed_stems),
        "operations": len(report),
        "reclassified": sum(item["action"] == "reclass" for item in report),
        "added": sum(item["action"] == "add" for item in report),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if not args.apply:
        print("dry-run：未写入。使用 --apply 应用修正。")
        return

    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    for stem in changed_stems:
        source = LABEL_ROOT / f"{stem}.txt"
        backup = BACKUP_ROOT / source.name
        if not backup.exists():
            shutil.copy2(source, backup)
        boxes = sorted(updated[stem], key=lambda box: (box.y, box.x, box.cls))
        source.write_text("\n".join(box.dump() for box in boxes) + ("\n" if boxes else ""), encoding="utf-8")

    (BACKUP_ROOT / "report.json").write_text(
        json.dumps({"summary": summary, "changes": report}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"已写入 {LABEL_ROOT}")
    print(f"备份与报告：{BACKUP_ROOT}")


if __name__ == "__main__":
    main()
