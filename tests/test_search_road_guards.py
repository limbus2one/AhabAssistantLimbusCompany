from tasks.mirror import search_road


def test_search_road_handles_missing_recognition(monkeypatch):
    monkeypatch.setattr(search_road.auto, "click_element", lambda *args, **kwargs: False)
    monkeypatch.setattr(search_road.auto, "find_element", lambda *args, **kwargs: None)

    assert search_road.search_road_from_road_map() == ([], [])
    assert search_road.divide_the_area_by_y(None) == []

    scale = search_road.cfg.set_win_size / 1440
    bus_positions = iter([(80 * scale, 690 * scale), None])
    monkeypatch.setattr(search_road.auto, "find_element", lambda *args, **kwargs: next(bus_positions))
    monkeypatch.setattr(search_road, "identify_nodes", lambda _: None)

    assert search_road.search_road_from_road_map() == ([], [])
