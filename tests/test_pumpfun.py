import unittest
from detector.pumpfun import craft_cursor, is_vortex, launch_slot

class TestCraftCursor(unittest.TestCase):
    def test_format_pads_slot_to_12_and_appends_index_and_ts(self):
        # slot 427327288, ts 1781801312000  -> 12-digit slot + 10 zeros + "-" + ts
        self.assertEqual(
            craft_cursor(427327288, 1781801312000),
            "0004273272880000000000-1781801312000",
        )

class FakeHelius:
    def __init__(self, pages):
        self.pages = pages
        self.calls = 0

    def signatures_page(self, address, before=None, limit=1000):
        page = self.pages[self.calls]
        self.calls += 1
        return page

class TestVortexAndLaunch(unittest.TestCase):
    def test_is_vortex_true(self):
        self.assertTrue(is_vortex({"metadata_uri": "https://api.vortexdeployer.com/metadata/x.json"}))

    def test_is_vortex_false(self):
        self.assertFalse(is_vortex({"metadata_uri": "https://ipfs.io/x"}))
        self.assertFalse(is_vortex({}))

    def test_launch_slot_exact_when_short_page(self):
        # one page < 1000 entries -> oldest reached; slot = last entry's slot
        pages = [[{"slot": 500, "signature": "a"}, {"slot": 499, "signature": "b"}]]
        slot, exact = launch_slot(FakeHelius(pages), "MINT", cap=90)
        self.assertEqual(slot, 499)
        self.assertTrue(exact)

    def test_launch_slot_partial_when_cap_hit(self):
        full = [{"slot": s, "signature": str(s)} for s in range(1000, 0, -1)]
        pages = [full, full]  # always full -> never reaches end
        slot, exact = launch_slot(FakeHelius(pages), "MINT", cap=2)
        self.assertFalse(exact)

if __name__ == "__main__":
    unittest.main()
