from dataclasses import dataclass
from .features import extract_wallet_features
from .classifier import score_wallet, DevConfig
from .pumpfun import is_vortex


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


def enumerate_launch(helius, mint, cap_pages=90, window_txs=250, cache=None):
    """Paginate the mint's signatures to the oldest, then return
    (launch_slot, exact, participants) where participants = the distinct fee-payers
    (accountKeys[0]) of the oldest `window_txs` transactions of the mint.
    Complete launch-participant source (includes fresh dev wallets), per spec §5.
    `cache` (optional JsonCache) memoizes the result per mint (immutable)."""
    if cache is not None:
        hit = cache.get(mint)
        if hit:
            return hit.get("launch_slot"), hit.get("exact", False), list(hit.get("participants", []))
    before = None
    last_page = []
    exact = False
    for _ in range(cap_pages):
        page = helius.signatures_page(mint, before=before, limit=1000)
        if not page:
            break
        last_page = page
        before = page[-1].get("signature")
        if len(page) < 1000:
            exact = True
            break
    if not last_page:
        return None, False, []
    launch_slot = last_page[-1].get("slot")
    # oldest window: tail of the final page (newest->oldest within the page; reverse to chronological)
    oldest = list(reversed(last_page[-window_txs:]))
    seen, participants = set(), []
    for s in oldest:
        sig = s.get("signature")
        if not sig:
            continue
        tx = helius.transaction(sig)
        if not tx:
            continue
        try:
            keys = tx["transaction"]["message"]["accountKeys"]
            payer = keys[0]["pubkey"] if isinstance(keys[0], dict) else keys[0]
        except (KeyError, IndexError, TypeError):
            continue
        if payer and payer not in seen:
            seen.add(payer)
            participants.append(payer)
    if cache is not None and launch_slot is not None:
        cache.set(mint, {"launch_slot": launch_slot, "exact": exact, "participants": participants})
        cache.save()
    return launch_slot, exact, participants


def _cov(detected, oracle):
    if not oracle:
        return None
    return round(detected / oracle, 3)


def analyze_token(mint, oracle, helius, pump, registry, cfg=None, cache=None):
    """Orchestrate dev-wallet detection for a given token mint.

    Parameters
    ----------
    mint     : token mint address
    oracle   : dict {"wallets":N, "buy":float, "sell":float} or None
    helius   : Helius client
    pump     : PumpFun client
    registry : Registry client
    cfg      : DevConfig (default constructed if None)
    cache    : optional JsonCache memoizing launch enumeration per mint

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

    # --- launch enumeration (mint signatures -> participants) ---
    lslot, exact, parts = enumerate_launch(helius, mint, cache=cache)

    # Build candidate list: creator first, then window participants (deduped)
    cand = ([creator] if creator else []) + [p for p in parts if p != creator]
    cand = list(dict.fromkeys(cand))  # preserve order, remove duplicates

    dev = []
    funders: set[str] = set()
    partial = (not exact) or (not coins)

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
