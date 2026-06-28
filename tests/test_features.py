import unittest
from detector.features import extract_wallet_features, WalletFeatures

DEV = "Gy2DKuEJjM67oPJ6ecCFcCtu3So85BoYmnrWjKN35GER"
BANK = "BmFdpraQhkiDQE6SnfG5omcA1VwzqfXrwtNYBwWTymy6"
MINT = "Gn2SDa53t9zegwdKX71ywUVZbjJZDXnBBkGP3T2Apump"
CONS = "5hfG56s6K4AaxUuQ3bgbovHZ6jEqe75TS6o9bwmxzsmM"
LAUNCH = 1781801312000

def tx_funding():
    return {"signature":"f1","slot":427056844,"timestamp":1781700000,
            "accountData":[{"account":DEV,"nativeBalanceChange":600000000,"tokenBalanceChanges":[]}],
            "nativeTransfers":[{"fromUserAccount":BANK,"toUserAccount":DEV,"amount":600000000}]}

def tx_buy():
    # wallet receives token, spends ~0.586 SOL
    return {"signature":"b1","slot":427327290,"timestamp":1781801320,
            "accountData":[
                {"account":DEV,"nativeBalanceChange":-586000000,"tokenBalanceChanges":[]},
                {"account":"tokAcc1","nativeBalanceChange":0,"tokenBalanceChanges":[
                    {"userAccount":DEV,"mint":MINT,"rawTokenAmount":{"tokenAmount":"885799202631","decimals":6}}]}],
            "nativeTransfers":[]}

def tx_sell():
    # wallet sends token, gets +3.324 SOL
    return {"signature":"s1","slot":427400000,"timestamp":1781850000,
            "accountData":[
                {"account":DEV,"nativeBalanceChange":3324000000,"tokenBalanceChanges":[]},
                {"account":"tokAcc1","nativeBalanceChange":0,"tokenBalanceChanges":[
                    {"userAccount":DEV,"mint":MINT,"rawTokenAmount":{"tokenAmount":"-885799202631","decimals":6}}]}],
            "nativeTransfers":[]}

def tx_drain():
    return {"signature":"d1","slot":427450000,"timestamp":1781860000,
            "accountData":[{"account":DEV,"nativeBalanceChange":-3521000000,"tokenBalanceChanges":[]}],
            "nativeTransfers":[{"fromUserAccount":DEV,"toUserAccount":CONS,"amount":3521000000}]}

class TestFeatures(unittest.TestCase):
    def test_dev_wallet_profile(self):
        f = extract_wallet_features(DEV, [tx_funding(),tx_buy(),tx_sell(),tx_drain()], MINT, LAUNCH, 0.002)
        self.assertIsInstance(f, WalletFeatures)
        self.assertEqual(f.n_tx, 4)
        self.assertEqual(f.n_other_mints, 0)
        self.assertAlmostEqual(f.buy_sol, 0.586, places=3)
        self.assertAlmostEqual(f.sell_sol, 3.324, places=3)
        self.assertEqual(f.funder, BANK)
        self.assertEqual(f.drain_dest, CONS)
        self.assertTrue(f.traded)
        self.assertEqual(f.balance_sol, 0.002)

    def test_other_mints_counted(self):
        other = {"signature":"o1","slot":1,"timestamp":1781802000,
                 "accountData":[{"account":DEV,"nativeBalanceChange":-1000,"tokenBalanceChanges":[
                     {"userAccount":DEV,"mint":"OtherMint111","rawTokenAmount":{"tokenAmount":"5","decimals":0}}]}],
                 "nativeTransfers":[]}
        f = extract_wallet_features(DEV, [tx_buy(), other], MINT, LAUNCH, 0.0)
        self.assertEqual(f.n_other_mints, 1)

    def test_no_trades_not_traded(self):
        f = extract_wallet_features(DEV, [tx_funding()], MINT, LAUNCH, 1.0)
        self.assertFalse(f.traded)
        self.assertEqual(f.buy_sol, 0.0)

if __name__ == "__main__":
    unittest.main()
