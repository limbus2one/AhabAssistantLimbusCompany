from ruamel.yaml import YAML

from module.config.config import Theme_pack_list


def test_selected_theme_pack_weight_is_decremented(tmp_path):
    theme_list = object.__new__(Theme_pack_list)
    theme_list.yaml = YAML()
    theme_list.theme_pack_list_path = str(tmp_path / "theme_pack_list.yaml")
    theme_list.theme_pack_weight_path = tmp_path / "theme_pack_weight"
    theme_list.config = {"theme_pack_list": {"chick": 1}}

    assert theme_list.decrement_theme_pack_weight("chick", False, "en", 1, False) == 0
    assert theme_list.load_config(theme_list.theme_pack_list_path)["theme_pack_list"]["chick"] == 0

    custom_path = theme_list.build_team_weight_path(1)
    theme_list.save_config(custom_path, {"theme_pack_list": {"chick": 3}})
    assert theme_list.decrement_theme_pack_weight("chick", False, "en", 1, True) == 2
    assert theme_list.load_config(custom_path)["theme_pack_list"]["chick"] == 2
    assert theme_list.config["theme_pack_list"]["chick"] == 0
