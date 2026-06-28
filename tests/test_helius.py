import unittest, json, io
from detector.helius import Helius

class FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False

class FakeOpener:
    def __init__(self, payloads): self.payloads = payloads; self.calls = []
    def open(self, req, timeout=0):
        self.calls.append(req.full_url)
        body = self.payloads.pop(0)
        return FakeResp(json.dumps(body).encode())

SYS = "11111111111111111111111111111111"

class TestHelius(unittest.TestCase):
    def test_balance_sol_converts_lamports(self):
        h = Helius("k", opener=FakeOpener([{"result": {"value": 1_500_000_000}}]))
        self.assertAlmostEqual(h.balance_sol("A"), 1.5)

    def test_is_wallet_true_for_system_owner(self):
        h = Helius("k", opener=FakeOpener([{"result": {"value": {"owner": SYS}}}]))
        self.assertTrue(h.is_wallet("A"))

    def test_is_wallet_false_for_program_owner(self):
        h = Helius("k", opener=FakeOpener([{"result": {"value": {"owner": "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"}}}]))
        self.assertFalse(h.is_wallet("A"))

    def test_enhanced_returns_list(self):
        op = FakeOpener([[{"signature": "s1"}]])
        h = Helius("k", opener=op)
        txs = h.enhanced_transactions("A")
        self.assertEqual(txs[0]["signature"], "s1")
        self.assertIn("/v0/addresses/A/transactions", op.calls[0])
        self.assertIn("limit=100", op.calls[0])

if __name__ == "__main__":
    unittest.main()
