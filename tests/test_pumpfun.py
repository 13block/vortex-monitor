import unittest
from detector.pumpfun import is_vortex

class TestVortex(unittest.TestCase):
    def test_is_vortex_true(self):
        self.assertTrue(is_vortex({"metadata_uri": "https://api.vortexdeployer.com/metadata/x.json"}))

    def test_is_vortex_false(self):
        self.assertFalse(is_vortex({"metadata_uri": "https://ipfs.io/x"}))
        self.assertFalse(is_vortex({}))
        self.assertFalse(is_vortex({"metadata_uri": None}))

if __name__ == "__main__":
    unittest.main()
