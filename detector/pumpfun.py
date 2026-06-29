import json
import time
import urllib.request
import urllib.error

COINS_URL = "https://frontend-api-v3.pump.fun/coins/{mint}"

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

def is_vortex(coins_json):
    return "vortexdeployer.com" in ((coins_json or {}).get("metadata_uri") or "")
