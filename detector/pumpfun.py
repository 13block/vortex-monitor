import json
import time
import urllib.request
import urllib.error

def craft_cursor(slot: int, ts_ms: int) -> str:
    """Cursor for swap-api.pump.fun/v2 trades: 12-digit slot + 10-digit index + -ts_ms."""
    return f"{slot:012d}0000000000-{ts_ms}"

COINS_URL = "https://frontend-api-v3.pump.fun/coins/{mint}"
TRADES_URL = "https://swap-api.pump.fun/v2/coins/{mint}/trades?limit={limit}"

class PumpFun:
    def __init__(self, opener=None):
        self.opener = opener or urllib.request.build_opener()

    def _get(self, url):
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                                           "accept": "application/json"})
                with self.opener.open(req, timeout=45) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code in (404, 410):
                    return None
                time.sleep(0.6 * (attempt + 1))
            except Exception:
                time.sleep(0.6 * (attempt + 1))
        return None

    def coins(self, mint):
        return self._get(COINS_URL.format(mint=mint))

    def trades(self, mint, cursor=None, limit=100):
        url = TRADES_URL.format(mint=mint, limit=limit)
        if cursor:
            url += f"&cursor={cursor}"
        return self._get(url)

def is_vortex(coins_json):
    return "vortexdeployer.com" in ((coins_json or {}).get("metadata_uri") or "")

def launch_slot(helius, mint, cap=90):
    before = None
    last = []
    for _ in range(cap):
        page = helius.signatures_page(mint, before=before, limit=1000)
        if not page:
            break
        last = page
        before = page[-1]["signature"]
        if len(page) < 1000:
            return last[-1]["slot"], True
    return (last[-1]["slot"] if last else None), False
