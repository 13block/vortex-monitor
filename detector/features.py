from dataclasses import dataclass
from collections import Counter

LAMPORTS = 1_000_000_000

@dataclass
class WalletFeatures:
    address: str
    n_tx: int
    n_other_mints: int
    buy_sol: float
    sell_sol: float
    funder: str | None
    balance_sol: float
    first_ms: int
    last_ms: int
    drain_dest: str | None
    traded: bool

def extract_wallet_features(address, enhanced_txs, mint, launch_ms, balance_sol):
    buy = sell = 0.0
    other = set()
    funder = None
    funder_ts = None
    drains = Counter()
    first_ms = None
    last_ms = 0
    for t in enhanced_txs:
        tms = t.get("timestamp", 0) * 1000
        if tms:
            first_ms = tms if first_ms is None else min(first_ms, tms)
            last_ms = max(last_ms, tms)
        net = 0
        tdir = 0
        for ad in t.get("accountData", []):
            if ad.get("account") == address:
                net = ad.get("nativeBalanceChange", 0)
            for tb in ad.get("tokenBalanceChanges", []):
                if tb.get("userAccount") != address:
                    continue
                if tb.get("mint") == mint:
                    tdir += int(tb.get("rawTokenAmount", {}).get("tokenAmount", 0) or 0)
                else:
                    other.add(tb.get("mint"))
        if tdir > 0:
            buy += max(0, -net) / LAMPORTS
        elif tdir < 0:
            sell += max(0, net) / LAMPORTS
        for nt in t.get("nativeTransfers", []):
            amt = nt.get("amount", 0) / LAMPORTS
            if (nt.get("toUserAccount") == address and amt > 0.02
                    and tms <= launch_ms + 3_600_000):
                if funder_ts is None or tms < funder_ts:
                    funder_ts = tms
                    funder = nt.get("fromUserAccount")
            if nt.get("fromUserAccount") == address and amt > 0.05 and tms >= launch_ms:
                drains[nt.get("toUserAccount")] += amt
    drain_dest = drains.most_common(1)[0][0] if drains else None
    return WalletFeatures(
        address=address, n_tx=len(enhanced_txs), n_other_mints=len(other),
        buy_sol=round(buy, 3), sell_sol=round(sell, 3), funder=funder,
        balance_sol=balance_sol, first_ms=first_ms or 0, last_ms=last_ms,
        drain_dest=drain_dest, traded=(buy > 0 or sell > 0),
    )
