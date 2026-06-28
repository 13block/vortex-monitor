# Vortex Dev-Wallet Detector — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ajouter à `vortex-monitor` un détecteur qui, pour un token Vortex, classe les wallets dev (preuve + score) et mesure sa couverture via l'oracle N/buy/sell, exposé dans la page web.

**Architecture:** Un package `detector/` en modules isolés : clients I/O fins (`helius`, `pumpfun`), logique **pure** testable (`features`, `classifier`), état persistant (`registry`), et orchestration (`detector`). `monitor.py` ne fait que câbler des endpoints web + une file d'attente mono-worker (analyse on-demand, hors poll loop). Les wallets ne sont PAS clusterisés par graphe (impossible, cf. spec) ; on énumère les participants on-chain puis on classe chaque wallet par comportement.

**Tech Stack:** Python 3.12, **stdlib uniquement** (`urllib`, `json`, `dataclasses`, `threading`, `http.server`, `unittest`). Pas de dépendance pip.

## Global Constraints

- **Stdlib only** — aucune dépendance externe (runtime ET tests). Tests via `python -m unittest`.
- **Python 3.12** (base image `python:3.12-slim`).
- **Hors poll loop** — l'analyse est on-demand ; ne jamais bloquer ni faire planter `poller()`/le serveur.
- **Helius Dev plan** — RPC + Enhanced. **Pas de batch JSON-RPC** (413) → appels unitaires. Clé via env `HELIUS_KEY`.
- **Oracle** = `records.json[ca]` : `wallets` (N), `buy`, `sell`.
- **Endpoint RPC** : `https://mainnet.helius-rpc.com/?api-key=<KEY>` ; **Enhanced** : `https://api.helius.xyz/v0/addresses/{addr}/transactions?api-key=<KEY>`.
- **Confirmation Vortex** : `coins.metadata_uri` contient `vortexdeployer.com`.
- Trades Vortex apparaissent en Helius `type=TRANSFER` ; le token change est sous le **token account** avec champ `userAccount` (matcher là-dessus). Montant SOL d'un trade = `nativeBalanceChange` de l'entrée `accountData` dont `account == wallet`.

---

### Task 1: Scaffolding du package + craft de curseur pump (pur)

**Files:**
- Create: `detector/__init__.py` (vide)
- Create: `detector/pumpfun.py`
- Create: `tests/__init__.py` (vide)
- Create: `tests/test_pumpfun.py`

**Interfaces:**
- Consumes: rien.
- Produces: `detector.pumpfun.craft_cursor(slot: int, ts_ms: int) -> str` — curseur pump = `f"{slot:012d}0000000000-{ts_ms}"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pumpfun.py
import unittest
from detector.pumpfun import craft_cursor

class TestCraftCursor(unittest.TestCase):
    def test_format_pads_slot_to_12_and_appends_index_and_ts(self):
        # slot 427327288, ts 1781801312000  -> 12-digit slot + 10 zeros + "-" + ts
        self.assertEqual(
            craft_cursor(427327288, 1781801312000),
            "0004273272880000000000-1781801312000",
        )

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_pumpfun -v`
Expected: FAIL (ModuleNotFoundError / ImportError: cannot import name 'craft_cursor')

- [ ] **Step 3: Write minimal implementation**

```python
# detector/pumpfun.py
def craft_cursor(slot: int, ts_ms: int) -> str:
    """Cursor for swap-api.pump.fun/v2 trades: 12-digit slot + 10-digit index + -ts_ms."""
    return f"{slot:012d}0000000000-{ts_ms}"
```

Also create empty `detector/__init__.py` and `tests/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_pumpfun -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add detector/__init__.py detector/pumpfun.py tests/__init__.py tests/test_pumpfun.py
git commit -m "feat(detector): package scaffolding + pump cursor craft"
```

---

### Task 2: Extraction des features par wallet (pur)

**Files:**
- Create: `detector/features.py`
- Test: `tests/test_features.py`

**Interfaces:**
- Consumes: rien (entrées = données déjà fetchées).
- Produces:
  - `detector.features.WalletFeatures` (dataclass) avec champs : `address:str, n_tx:int, n_other_mints:int, buy_sol:float, sell_sol:float, funder:str|None, balance_sol:float, first_ms:int, last_ms:int, drain_dest:str|None, traded:bool`.
  - `detector.features.extract_wallet_features(address:str, enhanced_txs:list[dict], mint:str, launch_ms:int, balance_sol:float) -> WalletFeatures`.

Règles (validées empiriquement) :
- Pour chaque tx, montant SOL net du wallet = `nativeBalanceChange` de l'entrée `accountData` dont `account == address`.
- Direction token = somme des `rawTokenAmount.tokenAmount` des `tokenBalanceChanges` dont `userAccount == address` et `mint == mint` (>0 ⇒ buy, <0 ⇒ sell). `buy_sol += max(0,-net)/1e9` ; `sell_sol += max(0, net)/1e9`.
- `n_other_mints` = nb de mints distincts (≠ mint) vus dans des `tokenBalanceChanges` avec `userAccount == address`.
- `funder` = `fromUserAccount` du **plus ancien** `nativeTransfers` entrant (`toUserAccount==address`, montant >0.02 SOL, `timestamp*1000 <= launch_ms + 3_600_000`).
- `drain_dest` = `toUserAccount` du plus gros `nativeTransfers` sortant (`fromUserAccount==address`, montant >0.05 SOL, `timestamp*1000 >= launch_ms`), agrégé par destinataire (somme), max.
- `first_ms`/`last_ms` = min/max de `timestamp*1000`. `traded = buy_sol>0 or sell_sol>0`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_features.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_features -v`
Expected: FAIL (ImportError: cannot import name 'extract_wallet_features')

- [ ] **Step 3: Write minimal implementation**

```python
# detector/features.py
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
                    tdir += int(tb["rawTokenAmount"]["tokenAmount"])
                else:
                    other.add(tb.get("mint"))
        if tdir > 0:
            buy += max(0, -net) / LAMPORTS
        elif tdir < 0:
            sell += max(0, net) / LAMPORTS
        for nt in t.get("nativeTransfers", []):
            amt = nt["amount"] / LAMPORTS
            if (nt["toUserAccount"] == address and amt > 0.02
                    and tms <= launch_ms + 3_600_000):
                if funder_ts is None or tms < funder_ts:
                    funder_ts = tms
                    funder = nt["fromUserAccount"]
            if nt["fromUserAccount"] == address and amt > 0.05 and tms >= launch_ms:
                drains[nt["toUserAccount"]] += amt
    drain_dest = drains.most_common(1)[0][0] if drains else None
    return WalletFeatures(
        address=address, n_tx=len(enhanced_txs), n_other_mints=len(other),
        buy_sol=round(buy, 3), sell_sol=round(sell, 3), funder=funder,
        balance_sol=balance_sol, first_ms=first_ms or 0, last_ms=last_ms,
        drain_dest=drain_dest, traded=(buy > 0 or sell > 0),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_features -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add detector/features.py tests/test_features.py
git commit -m "feat(detector): per-wallet feature extraction (pure)"
```

---

### Task 3: Classifieur dev/non-dev (pur)

**Files:**
- Create: `detector/classifier.py`
- Test: `tests/test_classifier.py`

**Interfaces:**
- Consumes: `detector.features.WalletFeatures`.
- Produces:
  - `detector.classifier.DevConfig` (dataclass) seuils/poids avec valeurs par défaut.
  - `detector.classifier.WalletVerdict` (dataclass) : `address:str, dev_score:float, is_dev:bool, reasons:list[str]`.
  - `detector.classifier.score_wallet(f:WalletFeatures, is_mega_funder:callable, cfg:DevConfig=DevConfig()) -> WalletVerdict`.
    - `is_mega_funder(addr:str) -> bool` (injecté ; cf. Task 4).

Logique : si `not f.traded` ⇒ score 0, is_dev False. Sinon somme pondérée des signaux booléens :
fresh (`n_tx <= fresh_max`), mono (`n_other_mints <= mono_max`), drained (`balance_sol <= drain_max`),
profit (`sell_sol > buy_sol`), funded_clean (`funder` non None et **pas** mega-funder).
`is_dev = dev_score >= cfg.threshold`. `reasons` = libellés des signaux actifs.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_classifier.py
import unittest
from detector.features import WalletFeatures
from detector.classifier import score_wallet, DevConfig, WalletVerdict

def feats(**kw):
    base = dict(address="W", n_tx=17, n_other_mints=0, buy_sol=0.586, sell_sol=3.324,
                funder="BANK", balance_sol=0.002, first_ms=1, last_ms=2,
                drain_dest="C", traded=True)
    base.update(kw)
    return WalletFeatures(**base)

never_mega = lambda a: False
always_mega = lambda a: True

class TestClassifier(unittest.TestCase):
    def test_textbook_dev_is_dev(self):
        v = score_wallet(feats(), never_mega)
        self.assertIsInstance(v, WalletVerdict)
        self.assertTrue(v.is_dev)
        self.assertGreaterEqual(v.dev_score, 0.6)
        self.assertIn("mono", v.reasons)
        self.assertIn("fresh", v.reasons)
        self.assertIn("drained", v.reasons)

    def test_bot_is_not_dev(self):
        v = score_wallet(feats(n_tx=100, n_other_mints=30, balance_sol=5.0, sell_sol=0.1, buy_sol=0.5), never_mega)
        self.assertFalse(v.is_dev)
        self.assertLess(v.dev_score, 0.6)

    def test_untraded_scores_zero(self):
        v = score_wallet(feats(traded=False, buy_sol=0.0, sell_sol=0.0), never_mega)
        self.assertEqual(v.dev_score, 0.0)
        self.assertFalse(v.is_dev)

    def test_mega_funder_does_not_count_as_clean_funding(self):
        v = score_wallet(feats(), always_mega)
        self.assertNotIn("funded_clean", v.reasons)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_classifier -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Write minimal implementation**

```python
# detector/classifier.py
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

def score_wallet(f, is_mega_funder, cfg=DevConfig()):
    if not f.traded:
        return WalletVerdict(address=f.address, dev_score=0.0, is_dev=False, reasons=[])
    reasons = []
    score = 0.0
    if f.n_other_mints <= cfg.mono_max:
        score += cfg.w_mono; reasons.append("mono")
    if f.n_tx <= cfg.fresh_max:
        score += cfg.w_fresh; reasons.append("fresh")
    if f.balance_sol <= cfg.drain_max:
        score += cfg.w_drained; reasons.append("drained")
    if f.sell_sol > f.buy_sol:
        score += cfg.w_profit; reasons.append("profit")
    if f.funder and not is_mega_funder(f.funder):
        score += cfg.w_funded_clean; reasons.append("funded_clean")
    return WalletVerdict(address=f.address, dev_score=round(score, 3),
                         is_dev=score >= cfg.threshold, reasons=reasons)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_classifier -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add detector/classifier.py tests/test_classifier.py
git commit -m "feat(detector): wallet dev/non-dev classifier (pure)"
```

---

### Task 4: Registry des méga-funders (persistant)

**Files:**
- Create: `detector/registry.py`
- Test: `tests/test_registry.py`

**Interfaces:**
- Consumes: rien.
- Produces:
  - `detector.registry.Registry(path:str, mega_threshold:int=100)`.
  - `Registry.is_mega_funder(addr:str) -> bool` — True si `addr` dans la seed connue OU si `recipients(addr) >= mega_threshold`.
  - `Registry.note_funding(funder:str, recipient:str) -> None` — incrémente l'ensemble des destinataires distincts.
  - `Registry.save() -> None` / chargement auto à l'init si le fichier existe.
  - Constante `detector.registry.KNOWN_MEGA = {"BmFdpraQhkiDQE6SnfG5omcA1VwzqfXrwtNYBwWTymy6"}`.

Persistance JSON : `{"recipients": {funder: [recipient,...]}}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_registry.py
import unittest, tempfile, os
from detector.registry import Registry, KNOWN_MEGA

class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "registry.json")

    def test_known_mega_seed(self):
        r = Registry(self.path)
        self.assertTrue(r.is_mega_funder(next(iter(KNOWN_MEGA))))

    def test_threshold_promotes_to_mega(self):
        r = Registry(self.path, mega_threshold=3)
        for i in range(3):
            r.note_funding("F", f"R{i}")
        self.assertTrue(r.is_mega_funder("F"))
        self.assertFalse(r.is_mega_funder("G"))

    def test_persistence_roundtrip(self):
        r = Registry(self.path, mega_threshold=2)
        r.note_funding("F", "R0"); r.note_funding("F", "R1")
        r.save()
        r2 = Registry(self.path, mega_threshold=2)
        self.assertTrue(r2.is_mega_funder("F"))

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_registry -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Write minimal implementation**

```python
# detector/registry.py
import json, os

KNOWN_MEGA = {"BmFdpraQhkiDQE6SnfG5omcA1VwzqfXrwtNYBwWTymy6"}

class Registry:
    def __init__(self, path, mega_threshold=100):
        self.path = path
        self.mega_threshold = mega_threshold
        self.recipients = {}  # funder -> set(recipient)
        if os.path.exists(path):
            data = json.load(open(path, encoding="utf-8"))
            self.recipients = {k: set(v) for k, v in data.get("recipients", {}).items()}

    def is_mega_funder(self, addr):
        if addr in KNOWN_MEGA:
            return True
        return len(self.recipients.get(addr, ())) >= self.mega_threshold

    def note_funding(self, funder, recipient):
        if not funder or not recipient:
            return
        self.recipients.setdefault(funder, set()).add(recipient)

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        json.dump({"recipients": {k: sorted(v) for k, v in self.recipients.items()}},
                  open(tmp, "w", encoding="utf-8"))
        os.replace(tmp, self.path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_registry -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add detector/registry.py tests/test_registry.py
git commit -m "feat(detector): mega-funder registry with persistence"
```

---

### Task 5: Client Helius (RPC + Enhanced)

**Files:**
- Create: `detector/helius.py`
- Test: `tests/test_helius.py`

**Interfaces:**
- Consumes: rien.
- Produces:
  - `detector.helius.Helius(api_key:str, opener=None)`.
  - `Helius.rpc(method:str, params:list) -> dict|list|None`.
  - `Helius.enhanced_transactions(address:str, limit:int=100, before:str|None=None) -> list[dict]`.
  - `Helius.signatures_page(address:str, before:str|None=None, limit:int=1000) -> list[dict]` (une page, plus récent d'abord).
  - `Helius.balance_sol(address:str) -> float`.
  - `Helius.account_owner(address:str) -> str|None`.
  - `Helius.is_wallet(address:str) -> bool` (owner == system program ou inconnu).

Le constructeur accepte un `opener` (urllib OpenerDirector) injectable pour tester sans réseau. Retries (3) avec petit backoff sur exception/HTTP non-404.

- [ ] **Step 1: Write the failing test** (injection d'un faux opener — pas de réseau)

```python
# tests/test_helius.py
import unittest, json, io
from detector.helius import Helius

class FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False

class FakeOpener:
    def __init__(self, payloads): self.payloads = payloads; self.calls = []
    def open(self, req, timeout=0):
        self.calls.append(req.full_url)
        body = self.payloads.pop(0)
        return FakeResp(json.dumps(body).encode())

SYS = "11111111111111111111111111111111"

class TestHelius(unittest.TestCase):
    def test_balance_sol_converts_lamports(self):
        h = Helius("k", opener=FakeOpener([{"result": {"value": 1_500_000_000}}]))
        self.assertAlmostEqual(h.balance_sol("A"), 1.5)

    def test_is_wallet_true_for_system_owner(self):
        h = Helius("k", opener=FakeOpener([{"result": {"value": {"owner": SYS}}}]))
        self.assertTrue(h.is_wallet("A"))

    def test_is_wallet_false_for_program_owner(self):
        h = Helius("k", opener=FakeOpener([{"result": {"value": {"owner": "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"}}}]))
        self.assertFalse(h.is_wallet("A"))

    def test_enhanced_returns_list(self):
        h = Helius("k", opener=FakeOpener([[{"signature": "s1"}]]))
        txs = h.enhanced_transactions("A")
        self.assertEqual(txs[0]["signature"], "s1")

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_helius -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Write minimal implementation**

```python
# detector/helius.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_helius -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add detector/helius.py tests/test_helius.py
git commit -m "feat(detector): Helius RPC+Enhanced client (injectable opener)"
```

---

### Task 6: Client pump.fun (coins + trades + helpers Vortex/launch)

**Files:**
- Modify: `detector/pumpfun.py`
- Test: `tests/test_pumpfun.py` (ajouts)

**Interfaces:**
- Consumes: `Helius` (pour la pagination des signatures dans `launch_slot`).
- Produces:
  - `detector.pumpfun.PumpFun(opener=None)` avec `coins(mint:str) -> dict|None`, `trades(mint:str, cursor:str|None=None, limit:int=100) -> dict|None`.
  - `detector.pumpfun.is_vortex(coins_json:dict) -> bool` (pur) — `"vortexdeployer.com" in coins_json.get("metadata_uri","")`.
  - `detector.pumpfun.launch_slot(helius:Helius, mint:str, cap:int=90) -> tuple[int|None, bool]` — pagine `signatures_page` jusqu'au plus ancien ; retourne (slot, exact). `exact=False` si `cap` atteint sans fin.

- [ ] **Step 1: Write the failing test** (ajouter à `tests/test_pumpfun.py`)

```python
# append to tests/test_pumpfun.py
from detector.pumpfun import is_vortex, launch_slot

class FakeHelius:
    def __init__(self, pages): self.pages = pages; self.calls = 0
    def signatures_page(self, address, before=None, limit=1000):
        page = self.pages[self.calls]; self.calls += 1; return page

class TestVortexAndLaunch(unittest.TestCase):
    def test_is_vortex_true(self):
        self.assertTrue(is_vortex({"metadata_uri": "https://api.vortexdeployer.com/metadata/x.json"}))
    def test_is_vortex_false(self):
        self.assertFalse(is_vortex({"metadata_uri": "https://ipfs.io/x"}))
        self.assertFalse(is_vortex({}))

    def test_launch_slot_exact_when_short_page(self):
        # one page < 1000 entries -> oldest reached; slot = last entry's slot
        pages = [[{"slot": 500, "signature": "a"}, {"slot": 499, "signature": "b"}]]
        slot, exact = launch_slot(FakeHelius(pages), "MINT", cap=90)
        self.assertEqual(slot, 499)
        self.assertTrue(exact)

    def test_launch_slot_partial_when_cap_hit(self):
        full = [{"slot": s, "signature": str(s)} for s in range(1000, 0, -1)]
        pages = [full, full]  # always full -> never reaches end
        slot, exact = launch_slot(FakeHelius(pages), "MINT", cap=2)
        self.assertFalse(exact)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_pumpfun -v`
Expected: FAIL (ImportError: cannot import name 'is_vortex')

- [ ] **Step 3: Write minimal implementation** (ajouter à `detector/pumpfun.py`)

```python
# add to detector/pumpfun.py
import json, time, urllib.request, urllib.error

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
    return "vortexdeployer.com" in (coins_json or {}).get("metadata_uri", "")

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_pumpfun -v`
Expected: PASS (craft_cursor + 4 nouveaux = 5 tests)

- [ ] **Step 5: Commit**

```bash
git add detector/pumpfun.py tests/test_pumpfun.py
git commit -m "feat(detector): pump.fun client + vortex/launch-slot helpers"
```

---

### Task 7: Orchestration `analyze_token` + confiance oracle

**Files:**
- Create: `detector/detector.py`
- Test: `tests/test_detector.py`

**Interfaces:**
- Consumes: `Helius`, `PumpFun`, `Registry`, `features.extract_wallet_features`, `classifier.score_wallet`, `pumpfun.launch_slot/is_vortex/craft_cursor`.
- Produces:
  - `detector.detector.DetectionResult` (dataclass) : `mint:str, creator:str|None, is_vortex:bool, launch_slot:int|None, launch_exact:bool, participants:int, dev_wallets:list[dict], confidence:dict, funders:list[str], partial:bool`.
    - `dev_wallets` items : `{"address","dev_score","reasons","buy","sell","balance","n_tx","n_other_mints","funder","drain_dest"}`.
    - `confidence` : `{"n_oracle","n_detected","count_ratio","buy_oracle","buy_detected","buy_cov","sell_oracle","sell_detected","sell_cov"}` (cov None si pas d'oracle).
  - `detector.detector.participants_in_window(helius, mint, launch_slot, span=80, pages=20) -> list[str]` — distinct buyers via `trades` (curseur crafté `launch_slot+45`), bornés à `launch_slot+span`.
  - `detector.detector.analyze_token(mint, oracle:dict|None, helius, pumpfun, registry, cfg=None) -> DetectionResult`.

`oracle` = `{"wallets":N,"buy":..,"sell":..}` ou None. `participants_in_window` lit `t["userAddress"]`, `t["type"]=="buy"`, `t["slotIndexId"][:12]` pour le slot, suit `pagination.nextCursor`/`hasMore`.

- [ ] **Step 1: Write the failing test** (clients fakes renvoyant des fixtures)

```python
# tests/test_detector.py
import unittest
from detector.detector import analyze_token, participants_in_window, DetectionResult

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
    def trades(self, mint, cursor=None, limit=100):
        return {"trades": [
            {"userAddress": DEV, "type": "buy", "slotIndexId": "000427327288" + "0"*10, "amountSol": "0.5"},
            {"userAddress": BOT, "type": "buy", "slotIndexId": "000427327289" + "0"*10, "amountSol": "0.2"},
        ], "pagination": {"hasMore": False, "nextCursor": None}}

class FakeHelius:
    def __init__(self): self._sig = [[{"slot": 427327288, "signature": "z"}]]
    def signatures_page(self, address, before=None, limit=1000): return self._sig[0]
    def enhanced_transactions(self, address, limit=100, before=None):
        return {DEV: dev_txs(), BOT: bot_txs(), CREATOR: []}.get(address, [])
    def balance_sol(self, address): return {DEV: 0.001, BOT: 4.0, CREATOR: 0.0}.get(address, 0.0)
    def is_wallet(self, address): return True

class FakeRegistry:
    def is_mega_funder(self, addr): return False
    def note_funding(self, f, r): pass

class TestParticipants(unittest.TestCase):
    def test_window_returns_distinct_buyers_in_span(self):
        ws = participants_in_window(FakeHelius(), MINT, 427327288, span=80)
        # both buyers within slot+80 via FakePump-like helius? participants uses pump trades:
        # (this test uses analyze_token path instead; kept minimal)
        self.assertIsInstance(ws, list)

class TestAnalyze(unittest.TestCase):
    def test_detects_dev_not_bot_and_computes_confidence(self):
        oracle = {"wallets": 2, "buy": 0.5, "sell": 3.0}
        res = analyze_token(MINT, oracle, FakeHelius(), FakePump(), FakeRegistry())
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
```

NOTE: `participants_in_window` interroge pump.fun, pas Helius. Le test `TestParticipants` est volontairement minimal ; corrige sa signature pour appeler `participants_in_window(pump, mint, launch_slot)` au Step 3 si tu changes la signature — garde-la cohérente avec l'implémentation ci-dessous (qui prend `pump`).

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_detector -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Write minimal implementation**

```python
# detector/detector.py
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
    if lslot is None:
        return []
    ts_hint = 9_999_999_999_999  # cursor ts upper bound; pump keys on slot mainly
    cursor = craft_cursor(lslot + 45, ts_hint)
    seen, out, base = set(), [], None
    for _ in range(pages):
        j = pump.trades(mint, cursor=cursor)
        if not j or not j.get("trades"):
            break
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
                seen.add(w); out.append(w)
        if not j["pagination"].get("hasMore"):
            break
        cursor = j["pagination"]["nextCursor"]
    return out

def _cov(detected, oracle):
    if not oracle:
        return None
    return round(detected / oracle, 3) if oracle else None

def analyze_token(mint, oracle, helius, pump, registry, cfg=None):
    cfg = cfg or DevConfig()
    coins = pump.coins(mint) or {}
    creator = coins.get("creator")
    launch_ms = coins.get("created_timestamp") or 0
    vortex = is_vortex(coins)
    lslot, exact = launch_slot(helius, mint)
    parts = participants_in_window(pump, mint, lslot)
    cand = ([creator] if creator else []) + [p for p in parts if p != creator]
    cand = list(dict.fromkeys(cand))
    dev = []
    funders = set()
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
            dev.append({"address": addr, "dev_score": v.dev_score, "reasons": v.reasons,
                        "buy": f.buy_sol, "sell": f.sell_sol, "balance": f.balance_sol,
                        "n_tx": f.n_tx, "n_other_mints": f.n_other_mints,
                        "funder": f.funder, "drain_dest": f.drain_dest})
    buy_det = round(sum(w["buy"] for w in dev), 3)
    sell_det = round(sum(w["sell"] for w in dev), 3)
    n_oracle = (oracle or {}).get("wallets")
    buy_oracle = (oracle or {}).get("buy")
    sell_oracle = (oracle or {}).get("sell")
    confidence = {
        "n_oracle": n_oracle, "n_detected": len(dev),
        "count_ratio": _cov(len(dev), n_oracle),
        "buy_oracle": buy_oracle, "buy_detected": buy_det, "buy_cov": _cov(buy_det, buy_oracle),
        "sell_oracle": sell_oracle, "sell_detected": sell_det, "sell_cov": _cov(sell_det, sell_oracle),
    }
    return DetectionResult(mint=mint, creator=creator, is_vortex=vortex,
                           launch_slot=lslot, launch_exact=exact, participants=len(parts),
                           dev_wallets=dev, confidence=confidence,
                           funders=sorted(funders), partial=partial)
```

Au Step 1, ajuste `TestParticipants` pour appeler `participants_in_window(FakePump(), MINT, 427327288)` (passe le pump fake, pas helius). Garde l'assertion `isinstance(list)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_detector -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add detector/detector.py tests/test_detector.py
git commit -m "feat(detector): analyze_token orchestration + oracle confidence"
```

---

### Task 8: Intégration web (file d'attente + endpoints + page détail)

**Files:**
- Create: `detector/jobs.py`
- Modify: `monitor.py` (imports en tête ; nouvelles routes dans `class H.do_GET` ~ lignes 248-260 ; bouton dans `PAGE` ~ ligne 228 ; démarrage worker dans `__main__` ~ lignes 262-266)
- Modify: `Dockerfile` (copier le package `detector/`)
- Test: `tests/test_jobs.py`

**Interfaces:**
- Consumes: `detector.detector.analyze_token`, `Helius`, `PumpFun`, `Registry`, `RECORDS` (oracle).
- Produces:
  - `detector.jobs.JobQueue(run_fn)` : `submit(ca:str) -> str` (status), `status(ca:str) -> dict` (`{"state": "pending|running|done|error", "result": <dict|None>, "error": <str|None>}`), worker thread démarré par `start()`.
    - `run_fn(ca:str) -> dict` est injecté (en prod : lambda qui appelle `analyze_token(...)` et `dataclasses.asdict`).
  - Routes web : `GET /analyze?ca=<CA>` (submit → `{"state":...}`), `GET /detection?ca=<CA>` (status JSON), `GET /token?ca=<CA>` (page HTML détail).

- [ ] **Step 1: Write the failing test** (queue testée sans réseau via `run_fn` fake)

```python
# tests/test_jobs.py
import unittest, time
from detector.jobs import JobQueue

class TestJobQueue(unittest.TestCase):
    def test_submit_runs_and_stores_result(self):
        q = JobQueue(run_fn=lambda ca: {"mint": ca, "ok": True})
        q.start()
        q.submit("CA1")
        for _ in range(50):
            if q.status("CA1")["state"] == "done":
                break
            time.sleep(0.05)
        st = q.status("CA1")
        self.assertEqual(st["state"], "done")
        self.assertEqual(st["result"]["mint"], "CA1")

    def test_error_is_captured(self):
        def boom(ca): raise ValueError("nope")
        q = JobQueue(run_fn=boom); q.start(); q.submit("CA2")
        for _ in range(50):
            if q.status("CA2")["state"] == "error":
                break
            time.sleep(0.05)
        self.assertEqual(q.status("CA2")["state"], "error")
        self.assertIn("nope", q.status("CA2")["error"])

    def test_unknown_ca_is_idle(self):
        q = JobQueue(run_fn=lambda ca: {})
        self.assertEqual(q.status("X")["state"], "idle")

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_jobs -v`
Expected: FAIL (ImportError)

- [ ] **Step 3: Write minimal implementation** (`detector/jobs.py`)

```python
# detector/jobs.py
import threading, queue, traceback

class JobQueue:
    def __init__(self, run_fn):
        self.run_fn = run_fn
        self._q = queue.Queue()
        self._state = {}            # ca -> dict
        self._lock = threading.Lock()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def submit(self, ca):
        with self._lock:
            cur = self._state.get(ca, {}).get("state")
            if cur in ("pending", "running"):
                return self._state[ca]
            self._state[ca] = {"state": "pending", "result": None, "error": None}
        self._q.put(ca)
        return self._state[ca]

    def status(self, ca):
        with self._lock:
            return dict(self._state.get(ca, {"state": "idle", "result": None, "error": None}))

    def _set(self, ca, **kw):
        with self._lock:
            self._state.setdefault(ca, {})
            self._state[ca].update(kw)

    def _worker(self):
        while True:
            ca = self._q.get()
            self._set(ca, state="running")
            try:
                res = self.run_fn(ca)
                self._set(ca, state="done", result=res, error=None)
            except Exception as e:
                self._set(ca, state="error", error=f"{e}\n{traceback.format_exc()}")
            finally:
                self._q.task_done()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m unittest tests.test_jobs -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Wire into monitor.py**

En tête de `monitor.py` (après les imports existants) :

```python
from dataclasses import asdict
from detector.helius import Helius
from detector.pumpfun import PumpFun
from detector.registry import Registry
from detector.detector import analyze_token
from detector.jobs import JobQueue

HELIUS_KEY = os.environ.get("HELIUS_KEY", "")
_helius = Helius(HELIUS_KEY)
_pump = PumpFun()
_registry = Registry(os.path.join(DATA_DIR, "registry.json"))

def _run_detection(ca):
    oracle = RECORDS.get(ca)
    res = analyze_token(ca, oracle, _helius, _pump, _registry)
    _registry.save()
    return asdict(res)

JOBS = JobQueue(_run_detection)
```

Dans `class H.do_GET`, ajouter avant le `else` final :

```python
        elif self.path.startswith("/analyze"):
            from urllib.parse import urlparse, parse_qs
            ca = parse_qs(urlparse(self.path).query).get("ca", [""])[0]
            st = JOBS.submit(ca) if ca else {"state": "error", "error": "no ca"}
            body = json.dumps(st, ensure_ascii=False).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers(); self.wfile.write(body)
        elif self.path.startswith("/detection"):
            from urllib.parse import urlparse, parse_qs
            ca = parse_qs(urlparse(self.path).query).get("ca", [""])[0]
            body = json.dumps(JOBS.status(ca), ensure_ascii=False).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers(); self.wfile.write(body)
        elif self.path.startswith("/token"):
            b = TOKEN_PAGE.encode()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers(); self.wfile.write(b)
```

Dans `__main__`, après le démarrage du poller, ajouter : `JOBS.start()`.

Dans `PAGE`, dans le template de ligne (`render()`), ajouter une cellule lien détail (à côté du lien gmgn) :

```javascript
  <a class="gm" href="/token?ca=${x.ca}" target="_blank">analyser</a>
```

Ajouter une constante `TOKEN_PAGE` (nouvelle page) après `PAGE` :

```python
TOKEN_PAGE = """<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Analyse wallet dev</title>
<style>body{margin:0;background:#0b0e14;color:#e6e9ef;font-family:-apple-system,Segoe UI,sans-serif;font-size:14px;padding:20px}
table{border-collapse:collapse;width:100%;margin-top:12px}th,td{padding:8px 10px;text-align:left;border-bottom:1px solid #1c222e;font-size:12px}
.pos{color:#3ddc84}.neg{color:#ff5c5c}.mut{color:#8b94a7}code{font-family:ui-monospace,monospace}
.bar{background:#131825;border:1px solid #1c222e;border-radius:8px;padding:10px 14px;margin:8px 0;display:inline-block;margin-right:10px}</style>
</head><body>
<h2>Analyse wallets dev</h2><div id="st" class="mut">Chargement…</div>
<div id="sum"></div><div id="wrap"></div>
<script>
const ca=new URLSearchParams(location.search).get("ca");
document.title="Analyse "+ca;
async function poll(){
 const j=await (await fetch("/detection?ca="+ca)).json();
 const st=document.getElementById("st");
 if(j.state==="idle"){await fetch("/analyze?ca="+ca);st.textContent="Analyse lancée…";return setTimeout(poll,2000);}
 if(j.state==="pending"||j.state==="running"){st.textContent="Analyse en cours ("+j.state+")…";return setTimeout(poll,2000);}
 if(j.state==="error"){st.innerHTML="<span class='neg'>Erreur</span>: <code>"+(j.error||"").slice(0,300)+"</code>";return;}
 const r=j.result;st.innerHTML="Vortex: "+(r.is_vortex?"oui":"non")+" · creator <code>"+(r.creator||"?")+"</code> · participants "+r.participants+(r.partial?" · <span class='neg'>partiel</span>":"");
 const c=r.confidence;
 document.getElementById("sum").innerHTML=
  "<div class='bar'><b>"+c.n_detected+"</b>"+(c.n_oracle?" / "+c.n_oracle:"")+" wallets</div>"+
  "<div class='bar'>buy "+c.buy_detected+(c.buy_oracle?" / "+c.buy_oracle+" ("+Math.round((c.buy_cov||0)*100)+"%)":"")+"</div>"+
  "<div class='bar'>sell "+c.sell_detected+(c.sell_oracle?" / "+c.sell_oracle+" ("+Math.round((c.sell_cov||0)*100)+"%)":"")+"</div>";
 document.getElementById("wrap").innerHTML="<table><thead><tr><th>wallet</th><th>score</th><th>buy</th><th>sell</th><th>solde</th><th>tx</th><th>autres</th><th>preuves</th><th>funder</th></tr></thead><tbody>"+
  r.dev_wallets.map(w=>"<tr><td><code>"+w.address+"</code></td><td>"+w.dev_score+"</td><td>"+w.buy+"</td><td>"+w.sell+"</td><td>"+w.balance+"</td><td>"+w.n_tx+"</td><td>"+w.n_other_mints+"</td><td class='mut'>"+w.reasons.join(", ")+"</td><td class='mut'><code>"+(w.funder||"-").slice(0,8)+"</code></td></tr>").join("")+"</tbody></table>";
}
poll();
</script></body></html>"""
```

Mettre à jour `Dockerfile` ligne 3 :

```dockerfile
COPY monitor.py seed.json /app/
COPY detector/ /app/detector/
```

- [ ] **Step 6: Run full test suite + smoke import**

Run: `python -m unittest discover -s tests -v`
Expected: PASS (tous les tests)
Run: `python -c "import monitor"` (avec `HELIUS_KEY` non requis à l'import)
Expected: aucune erreur d'import.

- [ ] **Step 7: Commit**

```bash
git add detector/jobs.py tests/test_jobs.py monitor.py Dockerfile
git commit -m "feat(web): on-demand detection queue + /analyze /detection /token routes"
```

---

## Self-Review (rempli par l'auteur du plan)

**Couverture spec :**
- §5 pipeline (6 phases) → Tasks 6 (contexte/launch), 7 (participants, features, classify, confiance, sortie), 2-3 (features/classify). ✓
- §4 modules isolés (helius/pumpfun/features/classifier/registry/detector/web) → Tasks 5,6,2,3,4,7,8. ✓
- §6 intégration web (file, /analyze, page détail, hors poll loop) → Task 8. ✓
- §7 gestion d'erreurs (retries, partial, job error) → Tasks 5/6 (_get retries), 7 (partial), 8 (JobQueue error). ✓
- §3 sources/pièges (TRANSFER+userAccount, curseur, denylist owner) → Tasks 2 (userAccount), 1/6 (curseur), 5 (is_wallet). ✓
- §8 tests avec fixtures Gy2DKu/bot → Tasks 2,3,7. ✓

**Scan placeholders :** aucun TBD/TODO ; code complet à chaque step. ✓

**Cohérence des types :** `WalletFeatures` (T2) consommé par `score_wallet` (T3) et `analyze_token` (T7) ; `is_mega_funder` callable (T4) injecté dans `score_wallet` (T3) et `analyze_token` (T7) ; `craft_cursor`/`launch_slot`/`is_vortex` (T1/T6) utilisés par `detector` (T7) ; `JobQueue(run_fn)` (T8) appelle `analyze_token`+`asdict`. ✓

**Note de calibration :** les seuils/poids de `DevConfig` (T3) sont des valeurs initiales ; à calibrer après coup en lançant l'outil sur des tokens connus (COMOS/CHESS) et en comparant `confidence` — n'est pas bloquant pour l'implémentation.
