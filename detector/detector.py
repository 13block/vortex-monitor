from dataclasses import dataclass, field
from .features import extract_wallet_features
from .classifier import score_wallet, DevConfig
from .pumpfun import is_vortex, launch_slot, craft_cursor


@dataclass
class DetectionResult:
    mint: str
    creator: str | None
    is_vortex: bool
    launch_slot: int | None
    launch_exact: bool
    participants: int
    dev_wallets: list[dict]
    confidence: dict
    funders: list[str]
    partial: bool


def participants_in_window(pump, mint, lslot, span=80, pages=20):
    """Return distinct buyers from pump.fun trades within lslot+span slots."""
    if lslot is None:
        return []
    ts_hint = 9_999_999_999_999  # upper-bound ts so cursor starts near launch slot
    cursor = craft_cursor(lslot + 45, ts_hint)
    seen, out, base = set(), [], None
    for _ in range(pages):
        j = pump.trades(mint, cursor=cursor)
        if not j or not j.get("trades"):
            break
        # determine base slot from first batch
        for t in j["trades"]:
            slot = int(t["slotIndexId"][:12])
            base = slot if base is None else min(base, slot)
        for t in j["trades"]:
            if t["type"] != "buy":
                continue
            if int(t["slotIndexId"][:12]) > base + span:
                continue
            w = t["userAddress"]
            if w not in seen:
                seen.add(w)
                out.append(w)
        if not j["pagination"].get("hasMore"):
            break
        cursor = j["pagination"]["nextCursor"]
    return out


def _cov(detected, oracle):
    """Coverage ratio: detected / oracle, or None if oracle is absent/zero."""
    if not oracle:
        return None
    return round(detected / oracle, 3)


def analyze_token(mint, oracle, helius, pump, registry, cfg=None):
    """Orchestrate dev-wallet detection for a given token mint.

    Parameters
    ----------
    mint     : token mint address
    oracle   : dict {"wallets":N, "buy":float, "sell":float} or None
    helius   : Helius client
    pump     : PumpFun client
    registry : Registry client
    cfg      : DevConfig (default constructed if None)

    Returns
    -------
    DetectionResult
    """
    cfg = cfg or DevConfig()

    # --- token metadata ---
    coins = pump.coins(mint) or {}
    creator = coins.get("creator")
    launch_ms = coins.get("created_timestamp") or 0
    vortex = is_vortex(coins)

    # --- launch slot ---
    lslot, exact = launch_slot(helius, mint)

    # --- participant window (pump trades) ---
    parts = participants_in_window(pump, mint, lslot)

    # Build candidate list: creator first, then window participants (deduped)
    cand = ([creator] if creator else []) + [p for p in parts if p != creator]
    cand = list(dict.fromkeys(cand))  # preserve order, remove duplicates

    dev = []
    funders: set[str] = set()
    partial = not exact

    for addr in cand:
        txs = helius.enhanced_transactions(addr)
        bal = helius.balance_sol(addr)
        f = extract_wallet_features(addr, txs, mint, launch_ms, bal)
        if f.funder:
            funders.add(f.funder)
            registry.note_funding(f.funder, addr)
        v = score_wallet(f, registry.is_mega_funder, cfg)
        if v.is_dev or addr == creator:
            dev.append({
                "address": addr,
                "dev_score": v.dev_score,
                "reasons": v.reasons,
                "buy": f.buy_sol,
                "sell": f.sell_sol,
                "balance": f.balance_sol,
                "n_tx": f.n_tx,
                "n_other_mints": f.n_other_mints,
                "funder": f.funder,
                "drain_dest": f.drain_dest,
            })

    # --- confidence ---
    buy_det = round(sum(w["buy"] for w in dev), 3)
    sell_det = round(sum(w["sell"] for w in dev), 3)
    n_oracle = (oracle or {}).get("wallets")
    buy_oracle = (oracle or {}).get("buy")
    sell_oracle = (oracle or {}).get("sell")
    confidence = {
        "n_oracle": n_oracle,
        "n_detected": len(dev),
        "count_ratio": _cov(len(dev), n_oracle),
        "buy_oracle": buy_oracle,
        "buy_detected": buy_det,
        "buy_cov": _cov(buy_det, buy_oracle),
        "sell_oracle": sell_oracle,
        "sell_detected": sell_det,
        "sell_cov": _cov(sell_det, sell_oracle),
    }

    return DetectionResult(
        mint=mint,
        creator=creator,
        is_vortex=vortex,
        launch_slot=lslot,
        launch_exact=exact,
        participants=len(parts),
        dev_wallets=dev,
        confidence=confidence,
        funders=sorted(funders),
        partial=partial,
    )
