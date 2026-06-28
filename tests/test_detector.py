import unittest
from detector.detector import analyze_token, participants_in_window, DetectionResult

MINT = "Gn2SDa53t9zegwdKX71ywUVZbjJZDXnBBkGP3T2Apump"
DEV = "DevWallet1111111111111111111111111111111111"
BOT = "BotWallet2222222222222222222222222222222222"
CREATOR = "Creator333333333333333333333333333333333333"
LAUNCH_TS = 1781801312000

def dev_txs():
    return [
        {"timestamp": 1781700000, "accountData": [{"account": DEV, "nativeBalanceChange": 500000000, "tokenBalanceChanges": []}],
         "nativeTransfers": [{"fromUserAccount": "CleanFunderXXXX", "toUserAccount": DEV, "amount": 500000000}]},
        {"timestamp": 1781801320, "accountData": [
            {"account": DEV, "nativeBalanceChange": -500000000, "tokenBalanceChanges": []},
            {"account": "ta", "nativeBalanceChange": 0, "tokenBalanceChanges": [
                {"userAccount": DEV, "mint": MINT, "rawTokenAmount": {"tokenAmount": "100", "decimals": 0}}]}],
         "nativeTransfers": []},
        {"timestamp": 1781850000, "accountData": [
            {"account": DEV, "nativeBalanceChange": 3000000000, "tokenBalanceChanges": []},
            {"account": "ta", "nativeBalanceChange": 0, "tokenBalanceChanges": [
                {"userAccount": DEV, "mint": MINT, "rawTokenAmount": {"tokenAmount": "-100", "decimals": 0}}]}],
         "nativeTransfers": []},
    ]

def bot_txs():
    base = [{"timestamp": 1781801320 + i, "accountData": [
        {"account": BOT, "nativeBalanceChange": -10000000, "tokenBalanceChanges": [
            {"userAccount": BOT, "mint": f"M{i}", "rawTokenAmount": {"tokenAmount": "1", "decimals": 0}}]}],
        "nativeTransfers": []} for i in range(40)]
    base.append({"timestamp": 1781801325, "accountData": [
        {"account": BOT, "nativeBalanceChange": -200000000, "tokenBalanceChanges": [
            {"userAccount": BOT, "mint": MINT, "rawTokenAmount": {"tokenAmount": "50", "decimals": 0}}]}],
        "nativeTransfers": []})
    return base

class FakePump:
    def coins(self, mint):
        return {"creator": CREATOR, "created_timestamp": LAUNCH_TS,
                "metadata_uri": "https://api.vortexdeployer.com/metadata/x.json"}
    def trades(self, mint, cursor=None, limit=100):
        return {"trades": [
            {"userAddress": DEV, "type": "buy", "slotIndexId": "000427327288" + "0"*10, "amountSol": "0.5"},
            {"userAddress": BOT, "type": "buy", "slotIndexId": "000427327289" + "0"*10, "amountSol": "0.2"},
        ], "pagination": {"hasMore": False, "nextCursor": None}}

class FakeHelius:
    def __init__(self): self._sig = [[{"slot": 427327288, "signature": "z"}]]
    def signatures_page(self, address, before=None, limit=1000): return self._sig[0]
    def enhanced_transactions(self, address, limit=100, before=None):
        return {DEV: dev_txs(), BOT: bot_txs(), CREATOR: []}.get(address, [])
    def balance_sol(self, address): return {DEV: 0.001, BOT: 4.0, CREATOR: 0.0}.get(address, 0.0)
    def is_wallet(self, address): return True

class FakeRegistry:
    def is_mega_funder(self, addr): return False
    def note_funding(self, f, r): pass

class TestParticipants(unittest.TestCase):
    def test_window_returns_distinct_buyers_in_span(self):
        # participants_in_window takes pump client (not helius)
        ws = participants_in_window(FakePump(), MINT, 427327288, span=80)
        self.assertIsInstance(ws, list)
        self.assertIn(DEV, ws)
        self.assertEqual(len(ws), 2)

class TestAnalyze(unittest.TestCase):
    def test_detects_dev_not_bot_and_computes_confidence(self):
        oracle = {"wallets": 2, "buy": 0.5, "sell": 3.0}
        res = analyze_token(MINT, oracle, FakeHelius(), FakePump(), FakeRegistry())
        self.assertIsInstance(res, DetectionResult)
        self.assertTrue(res.is_vortex)
        self.assertEqual(res.creator, CREATOR)
        addrs = [w["address"] for w in res.dev_wallets]
        self.assertIn(DEV, addrs)
        self.assertNotIn(BOT, addrs)
        self.assertEqual(res.confidence["n_oracle"], 2)
        self.assertGreater(res.confidence["sell_cov"], 0)

if __name__ == "__main__":
    unittest.main()
