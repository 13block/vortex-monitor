import json, time, urllib.request, urllib.error

SYSTEM_PROGRAM = "11111111111111111111111111111111"
RPC_URL = "https://mainnet.helius-rpc.com/?api-key={key}"
ENH_URL = "https://api.helius.xyz/v0/addresses/{addr}/transactions?api-key={key}&limit={limit}"
LAMPORTS = 1_000_000_000

class Helius:
    def __init__(self, api_key, opener=None):
        self.key = api_key
        self.opener = opener or urllib.request.build_opener()

    def _get(self, url):
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "vortex-monitor/1.0",
                                                           "accept": "application/json"})
                with self.opener.open(req, timeout=50) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code in (404, 410):
                    return None
                time.sleep(0.8 * (attempt + 1))
            except Exception:
                time.sleep(0.8 * (attempt + 1))
        return None

    def _post(self, url, payload):
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                             headers={"Content-Type": "application/json"})
                with self.opener.open(req, timeout=50) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code in (404, 410):
                    return None
                time.sleep(0.8 * (attempt + 1))
            except Exception:
                time.sleep(0.8 * (attempt + 1))
        return None

    def rpc(self, method, params):
        j = self._post(RPC_URL.format(key=self.key),
                       {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        return j.get("result") if j else None

    def enhanced_transactions(self, address, limit=100, before=None):
        url = ENH_URL.format(addr=address, key=self.key, limit=limit)
        if before:
            url += f"&before={before}"
        return self._get(url) or []

    def signatures_page(self, address, before=None, limit=1000):
        params = [address, {"limit": limit}]
        if before:
            params[1]["before"] = before
        return self.rpc("getSignaturesForAddress", params) or []

    def balance_sol(self, address):
        r = self.rpc("getBalance", [address]) or {}
        return (r.get("value", 0) or 0) / LAMPORTS

    def account_owner(self, address):
        r = self.rpc("getAccountInfo", [address, {"encoding": "base64"}])
        try:
            return r["value"]["owner"]
        except Exception:
            return None

    def is_wallet(self, address):
        o = self.account_owner(address)
        return o == SYSTEM_PROGRAM or o is None
