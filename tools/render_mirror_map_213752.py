from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


nodes = [
    {"coord": (0, 0), "type": "bus", "screen_pos": (109, 897), "value": 0, "next": [(1, -1), (1, 0)]},
    {"coord": (1, -1), "type": "battle", "screen_pos": (491, 585), "value": 30, "next": [(2, -2), (2, -1)]},
    {"coord": (1, 0), "type": "battle", "screen_pos": (491, 901), "value": 30, "next": [(2, -1), (2, 0)]},
    {"coord": (2, -2), "type": "event", "screen_pos": (876, 260), "value": 18, "next": [(3, -1)]},
    {"coord": (2, -1), "type": "battle", "screen_pos": (875, 584), "value": 30, "next": [(3, -1)]},
    {"coord": (2, 0), "type": "battle", "screen_pos": (877, 903), "value": 30, "next": [(3, -1)]},
    {"coord": (3, -1), "type": "shop", "screen_pos": (1260, 581), "value": 1, "next": [(4, -1)]},
    {"coord": (4, -1), "type": "boss_battle", "screen_pos": (1643, 587), "value": 1, "next": []},
]

node_by_coord = {node["coord"]: node for node in nodes}
edges = [(node["coord"], target) for node in nodes for target in node["next"]]

colors = {
    "bus": ("#dbeafe", "#1d4ed8"),
    "battle": ("#fee2e2", "#b91c1c"),
    "event": ("#fef3c7", "#b45309"),
    "shop": ("#dcfce7", "#15803d"),
    "boss_battle": ("#ede9fe", "#6d28d9"),
}

pos = {coord: (coord[0], -coord[1]) for coord in node_by_coord}

fig, ax = plt.subplots(figsize=(13, 6.8), dpi=170)
fig.patch.set_facecolor("#f8fafc")
ax.set_facecolor("#f8fafc")

box_w = 0.68
box_h = 0.34

for source, target in edges:
    sx, sy = pos[source]
    tx, ty = pos[target]
    ax.add_patch(
        FancyArrowPatch(
            (sx + box_w / 2 - 0.03, sy),
            (tx - box_w / 2 + 0.03, ty),
            arrowstyle="-|>",
            mutation_scale=13,
            linewidth=1.8,
            color="#475569",
            connectionstyle="arc3,rad=0.04",
            shrinkA=4,
            shrinkB=4,
            zorder=1,
        )
    )

for coord, node in node_by_coord.items():
    x, y = pos[coord]
    fill, edge = colors[node["type"]]
    ax.add_patch(
        FancyBboxPatch(
            (x - box_w / 2, y - box_h / 2),
            box_w,
            box_h,
            boxstyle="round,pad=0.035,rounding_size=0.045",
            linewidth=2,
            edgecolor=edge,
            facecolor=fill,
            zorder=3,
        )
    )
    label = f"{coord}\n{node['type']}  v={node['value']}\n{node['screen_pos']}"
    ax.text(x, y, label, ha="center", va="center", fontsize=9.5, color="#0f172a", zorder=4)

ax.text(
    0,
    2.75,
    "Mirror Dungeon Road Map - 2026-07-16 21:37:52",
    ha="left",
    va="center",
    fontsize=15,
    weight="bold",
    color="#0f172a",
)
ax.text(0, 2.48, "8 nodes, 10 directed routes", ha="left", va="center", fontsize=10.5, color="#475569")

ax.set_xlim(-0.75, 4.85)
ax.set_ylim(-0.6, 2.95)
ax.axis("off")

out_dir = Path("outputs")
out_dir.mkdir(exist_ok=True)
out_path = out_dir / "mirror_map_20260716_213752.png"
fig.savefig(out_path, bbox_inches="tight", pad_inches=0.25)
plt.close(fig)

print(out_path.resolve())
