import os
import shutil
import tempfile
import unittest

from detector.cache import JsonCache


class TestJsonCache(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "sub", "cache.json")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_get_missing_key_returns_none(self):
        c = JsonCache(self.path)
        self.assertIsNone(c.get("nope"))

    def test_set_then_get_roundtrip(self):
        c = JsonCache(self.path)
        c.set("k", {"a": 1})
        self.assertEqual(c.get("k"), {"a": 1})

    def test_save_then_reload_restores_data(self):
        c = JsonCache(self.path)
        c.set("k", {"a": 1, "b": [1, 2, 3]})
        c.save()
        c2 = JsonCache(self.path)
        self.assertEqual(c2.get("k"), {"a": 1, "b": [1, 2, 3]})

    def test_corrupt_file_yields_empty_cache(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        c = JsonCache(self.path)
        self.assertEqual(c.data, {})
        self.assertIsNone(c.get("anything"))


if __name__ == "__main__":
    unittest.main()
