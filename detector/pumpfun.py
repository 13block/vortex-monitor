def craft_cursor(slot: int, ts_ms: int) -> str:
    """Cursor for swap-api.pump.fun/v2 trades: 12-digit slot + 10-digit index + -ts_ms."""
    return f"{slot:012d}0000000000-{ts_ms}"
