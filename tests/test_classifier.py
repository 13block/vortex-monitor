import unittest
from detector.features import WalletFeatures
from detector.classifier import score_wallet, DevConfig, WalletVerdict

def feats(**kw):
    base = dict(address="W", n_tx=17, n_other_mints=0, buy_sol=0.586, sell_sol=3.324,
                funder="BANK", balance_sol=0.002, first_ms=1, last_ms=2,
                drain_dest="C", traded=True)
    base.update(kw)
    return WalletFeatures(**base)

never_mega = lambda a: False
always_mega = lambda a: True

class TestClassifier(unittest.TestCase):
    def test_textbook_dev_is_dev(self):
        v = score_wallet(feats(), never_mega)
        self.assertIsInstance(v, WalletVerdict)
        self.assertTrue(v.is_dev)
        self.assertGreaterEqual(v.dev_score, 0.6)
        self.assertIn("mono", v.reasons)
        self.assertIn("fresh", v.reasons)
        self.assertIn("drained", v.reasons)

    def test_bot_is_not_dev(self):
        v = score_wallet(feats(n_tx=100, n_other_mints=30, balance_sol=5.0, sell_sol=0.1, buy_sol=0.5), never_mega)
        self.assertFalse(v.is_dev)
        self.assertLess(v.dev_score, 0.6)

    def test_untraded_scores_zero(self):
        v = score_wallet(feats(traded=False, buy_sol=0.0, sell_sol=0.0), never_mega)
        self.assertEqual(v.dev_score, 0.0)
        self.assertFalse(v.is_dev)

    def test_mega_funder_does_not_count_as_clean_funding(self):
        v = score_wallet(feats(), always_mega)
        self.assertNotIn("funded_clean", v.reasons)

if __name__ == "__main__":
    unittest.main()
