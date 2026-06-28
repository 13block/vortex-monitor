import unittest
from detector.pumpfun import craft_cursor

class TestCraftCursor(unittest.TestCase):
    def test_format_pads_slot_to_12_and_appends_index_and_ts(self):
        # slot 427327288, ts 1781801312000  -> 12-digit slot + 10 zeros + "-" + ts
        self.assertEqual(
            craft_cursor(427327288, 1781801312000),
            "0004273272880000000000-1781801312000",
        )

if __name__ == "__main__":
    unittest.main()
