from contextlib import ExitStack
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import tasks.mirror.mirror as mirror_module
from tasks.mirror.mirror import Mirror


class FakeAuto:
    def __init__(self, cards, white_card_x=None, primary_card_x=None, secondary_card_x=None):
        self.cards = cards
        self.white_card_x = white_card_x
        self.primary_card_x = primary_card_x
        self.secondary_card_x = secondary_card_x
        self.model = None
        self.mouse_clicks = []
        self.element_clicks = []
        self.white_ocr_all_text = []

    def mouse_to_blank(self):
        return None

    def take_screenshot(self):
        return object()

    def find_language_text(self, zh_text, en_text, bbox, all_text=False):
        self.white_ocr_all_text.append(all_text)
        card_x = bbox[0] + 50
        return [card_x, bbox[1]] if card_x == self.white_card_x else False

    def find_element(self, target, find_type="image", my_crop=None, **kwargs):
        if find_type == "image_with_multiple_targets":
            return self.cards
        card_x = my_crop[0] + 50
        if target.endswith("/burn.png"):
            return (card_x, my_crop[1]) if card_x == self.primary_card_x else None
        if target.endswith("/bleed.png"):
            return (card_x, my_crop[1]) if card_x == self.secondary_card_x else None
        return None

    def mouse_click(self, x, y):
        self.mouse_clicks.append((x, y))

    def click_element(self, target, **kwargs):
        self.element_clicks.append(target)
        return True


def make_mirror():
    mirror = Mirror.__new__(Mirror)
    mirror.system = "burn"
    mirror.second_system = True
    mirror.second_system_select = 1
    mirror.second_system_setting = 0
    mirror.shop = SimpleNamespace(fuse_IV=False)
    return mirror


def patched_globals(fake_auto, *, allow_white_gossypium=False):
    fake_cfg = SimpleNamespace(
        set_win_size=1440,
        not_skip_whitegossypium=allow_white_gossypium,
        mouse_action_interval=0,
    )
    stack = ExitStack()
    stack.enter_context(patch.object(mirror_module, "auto", fake_auto))
    stack.enter_context(patch.object(mirror_module, "cfg", fake_cfg))
    stack.enter_context(patch.object(mirror_module, "retry", lambda: None))
    stack.enter_context(patch.object(mirror_module, "sleep", lambda _: None))
    stack.enter_context(patch.object(mirror_module.time, "sleep", lambda _: None))
    return stack


class AcquireEgoGiftTest(TestCase):
    def test_filters_white_gossypium_then_clicks_all_cards_by_priority(self):
        cards = [(100, 400), (500, 400), (900, 400), (1300, 400)]
        fake_auto = FakeAuto(
            cards,
            white_card_x=500,
            primary_card_x=900,
            secondary_card_x=100,
        )

        with patched_globals(fake_auto):
            result = make_mirror().acquire_ego_gift()

        self.assertIs(result, True)
        self.assertEqual(fake_auto.mouse_clicks, [(900, 400), (100, 400), (1300, 400)])
        self.assertTrue(all(fake_auto.white_ocr_all_text))
        self.assertEqual(
            fake_auto.element_clicks,
            ["mirror/road_in_mir/acquire_ego_gift_select_assets.png"],
        )

    def test_rejects_when_white_gossypium_filter_removes_every_card(self):
        fake_auto = FakeAuto([(500, 400)], white_card_x=500)

        with patched_globals(fake_auto):
            result = make_mirror().acquire_ego_gift()

        self.assertIs(result, True)
        self.assertEqual(fake_auto.mouse_clicks, [])
        self.assertEqual(
            fake_auto.element_clicks,
            [
                "mirror/road_in_mir/refuse_gift_assets.png",
                "mirror/road_in_mir/refuse_gift_confirm_assets.png",
            ],
        )

    def test_keeps_white_gossypium_when_config_allows_it(self):
        fake_auto = FakeAuto([(500, 400)], white_card_x=500)

        with patched_globals(fake_auto, allow_white_gossypium=True):
            result = make_mirror().acquire_ego_gift()

        self.assertIs(result, True)
        self.assertEqual(fake_auto.white_ocr_all_text, [])
        self.assertEqual(fake_auto.mouse_clicks, [(500, 400)])
        self.assertEqual(
            fake_auto.element_clicks,
            ["mirror/road_in_mir/acquire_ego_gift_select_assets.png"],
        )
