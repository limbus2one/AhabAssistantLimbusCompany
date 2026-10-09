"""截图回归只验证匹配逻辑；真实导航及输入结果见模拟器实验记录。"""

from pathlib import Path

import numpy as np
from PIL import Image
from ruamel.yaml import YAML

from tasks.mirror.theme_pack_images import (
    ASSET_DIR,
    catalog,
    find_pack,
    find_selected_pack,
    recognize_pack,
    selected_border,
)


def test_catalog_and_image_matching():
    data = catalog()
    weights = YAML().load((ASSET_DIR.parent / "config" / "theme_pack_list.example.yaml").read_text(encoding="utf-8"))
    for mode in ("normal", "hard"):
        assert set(data["maps"][mode]) == {"1", "2", "3", "4", "5"}
        for floor, ids in data["maps"][mode].items():
            assert ids == sorted(set(ids), key=int)
            assert all(int(floor) in data["packs"][key]["floors"][mode] for key in ids)
            effective = weights["theme_pack_list"] | (weights["theme_pack_list_hard"] if mode == "hard" else {})
            assert all(data["packs"][key]["weight_key"] in effective for key in ids)
    for pack_id, pack in data["packs"].items():
        assert pack["en"] and pack["zh"] and pack["weight_key"] != "unknown"
        with Image.open(ASSET_DIR / "en" / f"{pack_id}.png") as image:
            assert image.size == (192, 356)
            match = recognize_pack(image, 192)
            assert match is not None and match.pack_id == pack_id
    fixtures = Path(__file__).parent / "fixtures" / "theme_packs"
    for pack_id in ("1002", "1004", "1006", "1011", "1014", "1102", "1128", "1202", "1204", "1206", "1306", "1321"):
        with Image.open(fixtures / f"choice_{pack_id}.png") as image:
            match = recognize_pack(image, 267)
            assert match is not None and match.pack_id == pack_id
            assert find_pack(image, "1007", 267) is None
            confusing = {
                "1011": "1014", "1014": "1011", "1102": "1008",
                "1202": "1204", "1204": "1202", "1306": "1321", "1321": "1306",
            }.get(pack_id)
            if confusing:
                assert find_pack(image, confusing, 267) is None
    with Image.open(fixtures / "grid_selected_1003.png") as image:
        match = find_pack(image, "1003", 144)
        assert match is not None and selected_border(image, match, 144)
        other = find_pack(image, "1001", 144)
        assert other is not None and not selected_border(image, other, 144)
    with Image.open(fixtures / "grid_selected_1319.png") as image:
        selected = find_selected_pack(image, "1319", 144)
        assert selected is not None and selected[0].pack_id == "1319"
        assert find_selected_pack(image, "1316", 144) is None
    assert recognize_pack(np.zeros((525, 310), dtype=np.uint8), 267) is None
    with Image.open(ASSET_DIR / "en" / "1002.png") as image:
        image.paste((0, 0, 0), (0, 255, 192, 330))
        assert recognize_pack(image, 192) is None
    with Image.open(ASSET_DIR / "choice" / "1204.png") as image:
        image.paste((0, 0, 0), (0, 255, 192, 330))
        assert recognize_pack(image, 192) is None
