import unittest
from detector.detector import analyze_token, enumerate_launch, DetectionResult

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

# Signatures page (newest -> oldest within the page). The oldest three entries map,
# via transaction(), to CREATOR, BOT, DEV signers (so chronological order = DEV, BOT, CREATOR).
SIG_TO_SIGNER = {
    "sig_dev": DEV,
    "sig_bot": BOT,
    "sig_creator": CREATOR,
}

class FakeHelius:
    def __init__(self):
        # newest first; oldest entries (tail) are creator/bot/dev
        self._page = [
            {"slot": 427327290, "signature": "sig_newest"},
            {"slot": 427327289, "signature": "sig_dev"},
            {"slot": 427327288, "signature": "sig_bot"},
            {"slot": 427327287, "signature": "sig_creator"},
        ]
    def signatures_page(self, address, before=None, limit=1000):
        # Single short page (len < 1000) -> exact launch
        if before:
            return []
        return self._page
    def transaction(self, signature):
        signer = SIG_TO_SIGNER.get(signature, "OtherSigner000000000000000000000000000000000")
        return {"transaction": {"message": {"accountKeys": [{"pubkey": signer}]}}}
    def enhanced_transactions(self, address, limit=100, before=None):
        return {DEV: dev_txs(), BOT: bot_txs(), CREATOR: []}.get(address, [])
    def balance_sol(self, address): return {DEV: 0.001, BOT: 4.0, CREATOR: 0.0}.get(address, 0.0)
    def is_wallet(self, address): return True

class FakeRegistry:
    def is_mega_funder(self, addr): return False
    def note_funding(self, f, r): pass

class TestEnumerateLaunch(unittest.TestCase):
    def test_enumerate_launch_returns_slot_exact_and_chrono_participants(self):
        lslot, exact, parts = enumerate_launch(FakeHelius(), MINT, window_txs=3)
        # launch_slot = oldest entry's slot
        self.assertEqual(lslot, 427327287)
        self.assertTrue(exact)
        # window_txs=3 oldest entries, chronological (oldest first): creator, bot, dev
        self.assertEqual(parts, [CREATOR, BOT, DEV])

class TestAnalyze(unittest.TestCase):
    def test_detects_dev_not_bot_and_computes_confidence(self):
        oracle = {"wallets": 2, "buy": 0.5, "sell": 3.0}
        res = analyze_token(MINT, oracle, FakeHelius(), FakePump(), FakeRegistry(), cache=None)
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
