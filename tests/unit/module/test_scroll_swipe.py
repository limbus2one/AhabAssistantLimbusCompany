import unittest

from module.automation.input_handlers.scroll_swipe import build_scroll_swipe_plan


class ScrollSwipePlanTest(unittest.TestCase):
    def test_holds_endpoint_before_release(self) -> None:
        plan = build_scroll_swipe_plan(100, 400, dy=-300, duration=0.3)

        self.assertEqual(plan[-2][0], (100, 100))
        self.assertEqual(plan[-1], ((100, 100), 0.5))


if __name__ == "__main__":
    unittest.main()
