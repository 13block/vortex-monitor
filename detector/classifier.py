from dataclasses import dataclass, field

@dataclass
class DevConfig:
    fresh_max: int = 40
    mono_max: int = 3
    drain_max: float = 0.05
    threshold: float = 0.6
    w_mono: float = 0.30
    w_fresh: float = 0.25
    w_drained: float = 0.25
    w_profit: float = 0.10
    w_funded_clean: float = 0.10

@dataclass
class WalletVerdict:
    address: str
    dev_score: float
    is_dev: bool
    reasons: list[str] = field(default_factory=list)

def score_wallet(f, is_mega_funder, cfg=None):
    if cfg is None: cfg = DevConfig()
    if not f.traded:
        return WalletVerdict(address=f.address, dev_score=0.0, is_dev=False, reasons=[])
    reasons = []
    score = 0.0
    if f.n_other_mints <= cfg.mono_max:
        score += cfg.w_mono
        reasons.append("mono")
    if f.n_tx <= cfg.fresh_max:
        score += cfg.w_fresh
        reasons.append("fresh")
    if f.balance_sol <= cfg.drain_max:
        score += cfg.w_drained
        reasons.append("drained")
    if f.sell_sol > f.buy_sol:
        score += cfg.w_profit
        reasons.append("profit")
    if f.funder and not is_mega_funder(f.funder):
        score += cfg.w_funded_clean
        reasons.append("funded_clean")
    return WalletVerdict(address=f.address, dev_score=round(score, 3),
                         is_dev=score >= cfg.threshold, reasons=reasons)
