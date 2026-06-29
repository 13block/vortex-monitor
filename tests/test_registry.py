import unittest, tempfile, os
from detector.registry import Registry, KNOWN_MEGA

class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "registry.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_known_mega_seed(self):
        r = Registry(self.path)
        self.assertTrue(r.is_mega_funder(next(iter(KNOWN_MEGA))))

    def test_threshold_promotes_to_mega(self):
        r = Registry(self.path, mega_threshold=3)
        for i in range(3):
            r.note_funding("F", f"R{i}")
        self.assertTrue(r.is_mega_funder("F"))
        self.assertFalse(r.is_mega_funder("G"))

    def test_persistence_roundtrip(self):
        r = Registry(self.path, mega_threshold=2)
        r.note_funding("F", "R0"); r.note_funding("F", "R1")
        r.save()
        r2 = Registry(self.path, mega_threshold=2)
        self.assertTrue(r2.is_mega_funder("F"))

if __name__ == "__main__":
    unittest.main()
