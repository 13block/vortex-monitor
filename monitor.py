#!/usr/bin/env python3
# Vortex Deployer - monitor de nouveaux launches (stdlib only).
# Poll le sitemap (10-15 min, jitter) -> diff -> scrape -> Discord, et sert une page web auto-actualisee.
import os, re, json, time, random, threading, urllib.request, urllib.error, ssl
from urllib.parse import urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from detector.pumpfun import PumpFun

# ---------- Config (variables d'environnement) ----------
PORT         = int(os.environ.get("PORT", "8080"))
DISCORD_RAW  = os.environ.get("DISCORD_WEBHOOK", "")
WEBHOOKS     = [w.strip().strip("'\"").strip() for w in re.split(r"[,\s]+", DISCORD_RAW) if w.strip()]
WEBHOOKS     = [w for w in WEBHOOKS if w.startswith("http")]
POLL_MIN     = int(os.environ.get("POLL_MIN", "600"))      # delai mini (s) -> 600 = 10 min
POLL_JITTER  = int(os.environ.get("POLL_JITTER", "300"))   # alea (s)       -> +0..300 = jusqu'a 15 min
DATA_DIR     = os.environ.get("DATA_DIR", "/data")
PROXY        = os.environ.get("PROXY", "").strip()         # ex: http://user:pass@host:port (optionnel)
REFRESH_HOURS = int(os.environ.get("REFRESH_HOURS", "24")) # re-scrape les tokens plus jeunes que X h (0 = jamais)
REFRESH_MAX   = int(os.environ.get("REFRESH_MAX", "60"))   # nb max de tokens rafraichis par cycle
SITE         = "https://www.vortexdeployer.com"

STATE = os.path.join(DATA_DIR, "records.json")
SEED  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed.json")

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
]
_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE
_opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}) if PROXY else urllib.request.ProxyHandler({})
)

LOCK = threading.Lock()
RECORDS = {}            # ca -> dict
LAST_CHECK = 0
BASELINE_DONE = False

_pump = PumpFun()

def log(*a): print(time.strftime("%H:%M:%S"), *a, flush=True)

def http_get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": random.choice(UA_POOL), "accept": "text/html,application/json"})
    with _opener.open(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")

def num(s):
    if s is None: return None
    s = str(s).replace(",", "").replace("+", "").strip()
    m = re.search(r"-?[0-9]*\.?[0-9]+", s)
    return float(m.group()) if m else None

def sitemap_cas():
    xml = http_get(f"{SITE}/sitemap.xml")
    out, seen = [], set()
    for m in re.findall(r"/token/([1-9A-HJ-NP-Za-km-z]+)", xml):
        if m not in seen:
            seen.add(m); out.append(m)
    return out  # plus recent en premier

def scrape_token(ca):
    try:
        h = http_get(f"{SITE}/token/{ca}")
    except urllib.error.HTTPError as e:
        if e.code == 404: return None
        raise
    mb = re.search(r"Buy volume</div><div[^>]*>([^<]+)", h)
    ms = re.search(r"Sell volume</div><div[^>]*>([^<]+)", h)
    mw = re.search(r"Wallets</div><div[^>]*>([^<]+)", h)
    mt = re.search(r"<title>\$(\S+)\s+—\s+(.+?)\s+on\s+", h)
    md = re.search(r'"datePublished":"([^"]+)"', h)
    if not mb and not mt: return None
    buy = num(mb.group(1)) if mb else 0.0
    sell = num(ms.group(1)) if ms else 0.0
    import datetime
    dms = 0
    if md:
        try: dms = int(datetime.datetime.fromisoformat(md.group(1).replace("Z", "+00:00")).timestamp() * 1000)
        except Exception: dms = 0
    if not dms: dms = int(time.time() * 1000)
    return {"ca": ca, "sym": mt.group(1) if mt else "?", "name": (mt.group(2).strip() if mt else ""),
            "date": dms, "buy": round(buy, 3), "sell": round(sell, 3),
            "benef": round(sell - buy, 3), "wallets": int(num(mw.group(1))) if mw else 0}

def fmt_usd(v):
    if v is None: return None
    v = float(v)
    if v >= 1e6: return f"${v/1e6:.2f}M"
    if v >= 1e3: return f"${v/1e3:.1f}K"
    return f"${v:.0f}"

def pump_fetch(ca):
    # Best-effort raw metadata from pump.fun: name, avatar, ATH MC (USD), socials URLs.
    try:
        c = _pump.coins(ca) or {}
    except Exception as e:
        log("pump fetch err", e); return {}
    def link(k):
        u = c.get(k)
        return u if (isinstance(u, str) and u.startswith("http")) else None
    return {"name": c.get("name"), "sym": c.get("symbol"),
            "image": (c.get("image_uri") or None), "ath": c.get("ath_market_cap"),
            "website": link("website"), "twitter": link("twitter"), "telegram": link("telegram")}

def pump_meta(ca):
    # Discord-formatted view of pump_fetch (socials as markdown links).
    m = pump_fetch(ca)
    socials = []
    for label, key in (("Website", "website"), ("Twitter", "twitter"), ("Telegram", "telegram")):
        if m.get(key): socials.append(f"[{label}]({m[key]})")
    m["socials"] = " · ".join(socials)
    return m

def enrich_record(ca, rec):
    # Add avatar / ATH / socials to a token record (in place). Marks rec["meta"]=1 on success.
    m = pump_fetch(ca)
    if not m: return rec
    rec["image"] = m.get("image"); rec["ath"] = m.get("ath")
    rec["website"] = m.get("website"); rec["twitter"] = m.get("twitter"); rec["telegram"] = m.get("telegram")
    rec["meta"] = 1
    return rec

def post_discord(d):
    if not WEBHOOKS: return
    color = 0x3ddc84 if d["benef"] > 0 else (0xff5c5c if d["benef"] < 0 else 0x8b94a7)
    ca = d["ca"]
    m = pump_meta(ca)
    name = m.get("name") or d.get("name") or ""
    sym = m.get("sym") or d.get("sym") or "?"
    fields = [
        {"name": "Buy", "value": f"{d['buy']:.2f} SOL", "inline": True},
        {"name": "Sell", "value": f"{d['sell']:.2f} SOL", "inline": True},
        {"name": "Benef", "value": f"{'+' if d['benef']>0 else ''}{d['benef']:.2f} SOL", "inline": True},
        {"name": "Wallets", "value": str(d["wallets"]), "inline": True},
    ]
    ath = fmt_usd(m.get("ath"))
    if ath: fields.append({"name": "ATH MC", "value": ath, "inline": True})
    fields.append({"name": "CA", "value": f"`{ca}`", "inline": False})
    if m.get("socials"):
        fields.append({"name": "Socials", "value": m["socials"], "inline": False})
    fields.append({"name": "Liens", "value": f"[gmgn](https://gmgn.ai/sol/token/{ca}) | [vortex]({SITE}/token/{ca})", "inline": False})
    embed = {
        "title": (f"{name} — ${sym}" if name else f"${sym}"),
        "url": f"https://gmgn.ai/sol/token/{ca}", "color": color, "fields": fields,
        "timestamp": __import__("datetime").datetime.fromtimestamp(d["date"]/1000, __import__("datetime").timezone.utc).isoformat()}
    if m.get("image"): embed["thumbnail"] = {"url": m["image"]}
    payload = {"username": "Vortex Monitor", "embeds": [embed]}
    data = json.dumps(payload).encode()
    for wh in WEBHOOKS:
        try:
            req = urllib.request.Request(wh, data=data,
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": "Mozilla/5.0 (compatible; VortexMonitor/1.0)"}, method="POST")
            urllib.request.urlopen(req, timeout=20)
        except Exception as e:
            log("discord err", e)

def load_state():
    global RECORDS, BASELINE_DONE
    if os.path.exists(STATE):
        RECORDS = json.load(open(STATE, encoding="utf-8")); log("state charge:", len(RECORDS))
    elif os.path.exists(SEED):
        RECORDS = json.load(open(SEED, encoding="utf-8")); log("seed charge:", len(RECORDS)); save_state()
    else:
        RECORDS = {}; log("demarrage a vide")
    BASELINE_DONE = len(RECORDS) > 0

def save_state():
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = STATE + ".tmp"
    json.dump(RECORDS, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, STATE)

def poll_once():
    global LAST_CHECK, BASELINE_DONE
    cas = sitemap_cas()
    with LOCK:
        new = [c for c in cas if c not in RECORDS]
    for ca in reversed(new):  # plus ancien -> plus recent
        d = scrape_token(ca)
        if not d: continue
        enrich_record(ca, d)
        with LOCK:
            RECORDS[ca] = d; save_state()
        if BASELINE_DONE:
            post_discord(d); log("NEW", d["sym"], ca)
        time.sleep(0.4)
    if not BASELINE_DONE:
        BASELINE_DONE = True
        log("baseline etablie (silencieuse):", len(RECORDS))
    refresh_recent()
    backfill_meta()
    LAST_CHECK = int(time.time() * 1000)

def backfill_meta(limit=25):
    # Populate avatar/ATH/socials for older records that predate enrichment.
    with LOCK:
        todo = [r["ca"] for r in RECORDS.values() if not r.get("meta")][:limit]
    n = 0
    for ca in todo:
        rec = {}
        enrich_record(ca, rec)
        if not rec.get("meta"): continue
        with LOCK:
            if ca in RECORDS:
                RECORDS[ca].update({k: rec.get(k) for k in ("image", "ath", "website", "twitter", "telegram", "meta")})
                n += 1
        time.sleep(0.2)
    if n:
        with LOCK: save_state()
        log(f"backfill meta: {n}/{len(todo)}")

def refresh_recent():
    # Re-scrape les tokens recents dont les stats peuvent encore bouger (sans re-ping Discord).
    if REFRESH_HOURS <= 0: return
    cutoff = (time.time() - REFRESH_HOURS * 3600) * 1000
    with LOCK:
        recent = [r["ca"] for r in RECORDS.values() if r.get("date", 0) >= cutoff][:REFRESH_MAX]
    upd = 0
    for ca in recent:
        d = scrape_token(ca)
        if not d: continue
        enrich_record(ca, d)
        with LOCK:
            old = RECORDS.get(ca, {})
            d["date"] = old.get("date", d["date"])           # garde la date d'origine
            if old.get("sym") and old["sym"] != "?":         # garde sym/name d'origine si valides
                d["sym"] = old["sym"]; d["name"] = old.get("name", d["name"])
            for k in ("image", "ath", "website", "twitter", "telegram", "meta"):
                if k not in d and k in old: d[k] = old[k]    # garde l'enrichissement si pump a echoue
            if d != old:
                RECORDS[ca] = d; upd += 1
        time.sleep(0.3)
    if upd:
        with LOCK: save_state()
        log(f"refresh: {upd}/{len(recent)} tokens MAJ")

def poller():
    while True:
        try: poll_once()
        except Exception as e: log("poll err", e)
        wait = POLL_MIN + random.randint(0, POLL_JITTER)
        log(f"prochain check dans {wait}s")
        time.sleep(wait)

# ---------- Web ----------
PAGE = """<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Vortex Monitor</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap">
<link rel="stylesheet" href="https://unpkg.com/@phosphor-icons/web@2.1.1/src/regular/style.css">
<style>
:root{--bg:#09090b;--surface:#0e0e11;--card:#121216;--elev:#17171b;--border:#262629;--border-soft:#1b1b1e;--fg:#fafafa;--muted:#a1a1aa;--faint:#71717a;--pos:#34d399;--neg:#fb7185;--ring:#6366f1;--r:10px;--rs:7px;--htop:120px}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font-family:'Inter',system-ui,-apple-system,sans-serif;font-size:14px;line-height:1.45;-webkit-font-smoothing:antialiased;font-feature-settings:"cv02","cv03","ss01"}
a{color:inherit;text-decoration:none}
:focus-visible{outline:2px solid var(--ring);outline-offset:1px;border-radius:4px}
header{position:sticky;top:0;z-index:20;background:var(--bg);border-bottom:1px solid var(--border-soft)}
.hwrap{max-width:1440px;margin:0 auto;padding:14px 22px}
.hrow{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap}
h1{margin:0;font-size:16px;font-weight:600;letter-spacing:-0.01em;display:flex;align-items:center;gap:9px}
h1 .dot{width:8px;height:8px;border-radius:50%;background:var(--pos);box-shadow:0 0 0 3px color-mix(in srgb,var(--pos) 22%,transparent)}
.sub{color:var(--muted);font-size:12px;margin-top:4px;max-width:60ch}
.stats{display:flex;gap:8px;flex-wrap:wrap}
.pill{display:inline-flex;align-items:baseline;gap:6px;background:var(--card);border:1px solid var(--border);border-radius:var(--rs);padding:6px 11px;font-size:12px;color:var(--muted)}
.pill b{font-size:13px;color:var(--fg);font-weight:600;font-variant-numeric:tabular-nums}
.pill b.pos{color:var(--pos)}.pill b.neg{color:var(--neg)}
.toolbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:14px}
.search{display:flex;align-items:center;gap:8px;background:var(--card);border:1px solid var(--border);border-radius:var(--rs);padding:0 11px;flex:0 1 300px;min-width:200px}
.search i{color:var(--faint);font-size:17px}
.search input{background:transparent;border:0;outline:0;color:var(--fg);font:inherit;font-size:13px;padding:9px 0;width:100%}
.search input::placeholder{color:var(--faint)}
.seg{display:inline-flex;background:var(--card);border:1px solid var(--border);border-radius:var(--rs);padding:3px;gap:2px}
.seg button{display:inline-flex;align-items:center;gap:6px;background:transparent;color:var(--muted);border:0;border-radius:5px;padding:6px 11px;font:inherit;font-size:12.5px;font-weight:500;cursor:pointer;transition:background .15s,color .15s}
.seg button i{font-size:16px}
.seg button:hover{color:var(--fg)}
.seg button.on{background:var(--elev);color:var(--fg);box-shadow:0 1px 2px rgba(0,0,0,.35)}
.grow{flex:1}
.sortc{display:inline-flex;align-items:center;gap:7px;color:var(--muted);font-size:12px}
select{background:var(--card);border:1px solid var(--border);border-radius:var(--rs);color:var(--fg);font:inherit;font-size:12.5px;padding:7px 9px;cursor:pointer;outline:0}
.iconbtn{display:inline-flex;align-items:center;justify-content:center;background:var(--card);border:1px solid var(--border);border-radius:var(--rs);color:var(--muted);width:32px;height:32px;cursor:pointer;transition:color .15s,background .15s}
.iconbtn:hover{color:var(--fg);background:var(--elev)}
.iconbtn i{font-size:15px}
#upd{color:var(--faint);font-size:11.5px;margin-left:auto;white-space:nowrap}
main{max-width:1440px;margin:0 auto;padding:18px 22px 90px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(254px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--border);border-radius:var(--r);padding:14px;display:flex;flex-direction:column;gap:12px;transition:border-color .15s,transform .15s,box-shadow .15s}
.card:hover{border-color:#35353b;transform:translateY(-2px);box-shadow:0 10px 28px -14px rgba(0,0,0,.7)}
.chead{display:flex;align-items:center;gap:11px;min-width:0}
.avw{position:relative;flex:none}
.avw .ph0{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-weight:600;color:var(--faint);background:var(--elev)}
.avw .ph0,.avw img{border-radius:9px;border:1px solid var(--border)}
.avw img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.avw.av{width:44px;height:44px;font-size:16px}
.avw.tav{width:24px;height:24px;font-size:11px;display:inline-block;vertical-align:middle;margin-right:9px}
.tt{min-width:0;flex:1}
.tname{font-weight:600;font-size:14px;letter-spacing:-0.005em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ttick{color:var(--muted);font-size:12px;font-family:ui-monospace,SFMono-Regular,monospace;margin-top:1px}
.badge{display:inline-flex;align-items:center;gap:3px;background:color-mix(in srgb,var(--pos) 14%,transparent);color:var(--pos);border:1px solid color-mix(in srgb,var(--pos) 32%,transparent);border-radius:5px;font-size:10px;font-weight:600;padding:2px 6px;flex:none}
.badge i{font-size:11px}
.cstats{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--border-soft);border:1px solid var(--border-soft);border-radius:9px;overflow:hidden}
.cell{background:var(--card);padding:8px 9px;min-width:0}
.cell .k{font-size:10px;color:var(--muted);font-weight:500}
.cell .v{font-size:13.5px;font-weight:600;margin-top:2px;white-space:nowrap;font-variant-numeric:tabular-nums}
.v.pos{color:var(--pos)}.v.neg{color:var(--neg)}
.cfoot{display:flex;align-items:center;gap:7px}
.ico{display:inline-flex;align-items:center;justify-content:center;color:var(--muted);border:1px solid var(--border);border-radius:7px;width:30px;height:30px;transition:color .15s,background .15s,border-color .15s}
.ico:hover{color:var(--fg);background:var(--elev);border-color:#35353b}
.ico i{font-size:16px}
.ico img{display:block;width:15px;height:15px;border-radius:3px}
.gmico{margin-left:auto}
.tablewrap{border:1px solid var(--border);border-radius:var(--r);overflow:hidden}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px}
thead th{position:sticky;top:0;background:var(--surface);text-align:right;white-space:nowrap;padding:10px 14px;font-size:11.5px;font-weight:500;color:var(--muted);border-bottom:1px solid var(--border);cursor:pointer;user-select:none;transition:color .12s;z-index:2}
thead th:hover{color:var(--fg)}
thead th.l,tbody td.l{text-align:left}
thead th.np{cursor:default}thead th.np:hover{color:var(--muted)}
.sw{display:inline-flex;align-items:center;gap:5px;justify-content:flex-end}
th.l .sw{justify-content:flex-start}
.sw i{font-size:13px;opacity:0}
th.act{color:var(--fg)}th.act .sw i{opacity:1}
tbody td{padding:10px 14px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--border-soft);font-variant-numeric:tabular-nums}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover td{background:var(--surface)}
.sym{font-weight:600}.nm{color:var(--muted);font-size:12px}
.rank{color:var(--faint)}
.row-ico{display:inline-flex;gap:6px;align-items:center;justify-content:flex-end}
.pos{color:var(--pos)}.neg{color:var(--neg)}
.hide{display:none}
.empty{padding:64px 20px;text-align:center;color:var(--muted);font-size:13px}
@media (prefers-reduced-motion:reduce){*{transition:none!important}.card:hover{transform:none}}
@media (max-width:680px){.hrow{flex-direction:column}#upd{display:none}.sortc span{display:none}}
</style></head><body>
<header><div class="hwrap">
<div class="hrow">
<div><h1><span class="dot"></span>Vortex Monitor</h1>
<div class="sub">Launches Vortex en direct · benef = sell − buy (SOL) · ATH = market cap maximum (pump.fun)</div></div>
<div class="stats">
<span class="pill"><b id="n">—</b>tokens</span>
<span class="pill"><b id="tbn">—</b>benef SOL</span>
<span class="pill"><b id="tw">—</b>wallets</span>
</div>
</div>
<div class="toolbar">
<div class="search"><i class="ph ph-magnifying-glass"></i><input id="q" placeholder="Rechercher symbole, nom ou CA"></div>
<div class="seg" id="bfseg"><button data-bf="all" class="on">Tous</button><button data-bf="profit">Profit</button><button data-bf="loss">Perte</button></div>
<div class="grow"></div>
<label class="sortc"><span>Trier</span>
<select id="sortsel">
<option value="date">Date</option><option value="benef">Benef</option><option value="buy">Buy</option><option value="sell">Sell</option><option value="wallets">Wallets</option><option value="ath">ATH</option><option value="sym">Nom</option>
</select>
<button id="dir" class="iconbtn" title="Sens du tri"><i class="ph ph-arrow-down"></i></button></label>
<div class="seg" id="viewseg"><button data-v="cards"><i class="ph ph-cards-three"></i>Cartes</button><button data-v="table"><i class="ph ph-table"></i>Tableau</button></div>
<span id="upd"></span>
</div>
</div></header>
<main>
<div id="cards" class="cards"></div>
<div id="tablewrap" class="tablewrap hide"><div class="scroll"><table><thead><tr>
<th class="l np">#</th>
<th class="l" data-k="sym"><span class="sw">Token <i></i></span></th>
<th data-k="date"><span class="sw">Date <i></i></span></th>
<th data-k="buy"><span class="sw">Buy <i></i></span></th>
<th data-k="sell"><span class="sw">Sell <i></i></span></th>
<th data-k="benef"><span class="sw">Benef <i></i></span></th>
<th data-k="wallets"><span class="sw">Wallets <i></i></span></th>
<th data-k="ath"><span class="sw">ATH <i></i></span></th>
<th class="l np">Liens</th>
</tr></thead><tbody id="b"></tbody></table></div></div>
<div id="empty" class="empty hide">Aucun token ne correspond.</div>
</main>
<script>
let DATA=[],sortK="date",asc=false,bf="all",view=localStorage.getItem("vmview")||"cards";
const $=s=>document.getElementById(s);
const fmtD=ms=>{if(!ms)return"—";const d=new Date(ms);return d.toLocaleString("fr-FR",{timeZone:"Europe/Paris",year:"2-digit",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"})};
const ago=ms=>{if(!ms)return"—";const s=(Date.now()-ms)/1000;if(s<3600)return Math.floor(s/60)+"min";if(s<86400)return Math.floor(s/3600)+"h";return Math.floor(s/86400)+"j"};
const fmtUsd=v=>{if(v==null||isNaN(v))return"—";v=+v;if(v>=1e9)return"$"+(v/1e9).toFixed(2)+"B";if(v>=1e6)return"$"+(v/1e6).toFixed(2)+"M";if(v>=1e3)return"$"+(v/1e3).toFixed(1)+"K";return"$"+v.toFixed(0)};
const esc=s=>(s==null?"":""+s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const avatar=(x,sz)=>`<div class="avw ${sz}"><div class="ph0">${esc((x.sym||"?")[0])}</div>${x.image?`<img src="${esc(x.image)}" loading="lazy" onerror="this.remove()">`:""}</div>`;
const links=x=>{let h="";if(x.website)h+=`<a class="ico" href="${esc(x.website)}" target="_blank" rel="noopener" title="Website"><i class="ph ph-globe-simple"></i></a>`;if(x.twitter)h+=`<a class="ico" href="${esc(x.twitter)}" target="_blank" rel="noopener" title="Twitter / X"><i class="ph ph-x-logo"></i></a>`;if(x.telegram)h+=`<a class="ico" href="${esc(x.telegram)}" target="_blank" rel="noopener" title="Telegram"><i class="ph ph-telegram-logo"></i></a>`;h+=`<a class="ico gmico" href="https://gmgn.ai/sol/token/${esc(x.ca)}" target="_blank" rel="noopener" title="GMGN"><img src="https://gmgn.ai/static/opstatic/GMGN_logo.svg" alt="GMGN"></a>`;return h};
function filtered(){let r=DATA.slice();const q=$("q").value.toLowerCase().trim();if(q)r=r.filter(x=>(x.sym+" "+x.name+" "+x.ca).toLowerCase().includes(q));if(bf=="profit")r=r.filter(x=>(x.benef||0)>0);else if(bf=="loss")r=r.filter(x=>(x.benef||0)<0);r.sort((a,c)=>{let va,vc;if(sortK=="sym"){va=(a.sym||"").toLowerCase();vc=(c.sym||"").toLowerCase()}else{va=a[sortK]??0;vc=c[sortK]??0}if(va<vc)return asc?-1:1;if(va>vc)return asc?1:-1;return 0});return r}
function renderCards(r){$("cards").innerHTML=r.map(x=>{const bc=x.benef>0?"pos":(x.benef<0?"neg":"");const sg=x.benef>0?"+":"";const isnew=(Date.now()-x.date)<3600000?'<span class="badge"><i class="ph ph-sparkle"></i>NEW</span>':"";return `<div class="card"><div class="chead">${avatar(x,"av")}<div class="tt"><div class="tname">${esc(x.name||x.sym)}</div><div class="ttick">$${esc(x.sym)}</div></div>${isnew}</div><div class="cstats"><div class="cell"><div class="k">Buy</div><div class="v">${(x.buy||0).toFixed(2)}</div></div><div class="cell"><div class="k">Sell</div><div class="v">${(x.sell||0).toFixed(2)}</div></div><div class="cell"><div class="k">Benef</div><div class="v ${bc}">${sg}${(x.benef||0).toFixed(2)}</div></div><div class="cell"><div class="k">Wallets</div><div class="v">${x.wallets||0}</div></div><div class="cell"><div class="k">ATH MC</div><div class="v">${fmtUsd(x.ath)}</div></div><div class="cell"><div class="k">Âge</div><div class="v">${ago(x.date)}</div></div></div><div class="cfoot">${links(x)}</div></div>`}).join("")}
function renderTable(r){$("b").innerHTML=r.map((x,i)=>{const bc=x.benef>0?"pos":(x.benef<0?"neg":"");const sg=x.benef>0?"+":"";const isnew=(Date.now()-x.date)<3600000?' <span class="badge"><i class="ph ph-sparkle"></i>NEW</span>':"";return `<tr><td class="l rank">${i+1}</td><td class="l">${avatar(x,"tav")}<span class="sym">$${esc(x.sym)}</span> <span class="nm">${esc(x.name||"")}</span>${isnew}</td><td>${fmtD(x.date)}</td><td>${(x.buy||0).toFixed(2)}</td><td>${(x.sell||0).toFixed(2)}</td><td class="${bc}">${sg}${(x.benef||0).toFixed(2)}</td><td>${x.wallets||0}</td><td>${fmtUsd(x.ath)}</td><td class="l"><span class="row-ico">${links(x)}</span></td></tr>`}).join("");document.querySelectorAll("th[data-k]").forEach(th=>{const on=th.dataset.k==sortK;th.classList.toggle("act",on);const ic=th.querySelector("i");ic.className=on?(asc?"ph ph-caret-up":"ph ph-caret-down"):""})}
function syncControls(){$("sortsel").value=sortK;$("dir").querySelector("i").className=asc?"ph ph-arrow-up":"ph ph-arrow-down";document.querySelectorAll("#viewseg button").forEach(b=>b.classList.toggle("on",b.dataset.v==view));document.querySelectorAll("#bfseg button").forEach(b=>b.classList.toggle("on",b.dataset.bf==bf))}
function render(){const r=filtered();const cards=$("cards"),tw=$("tablewrap"),emp=$("empty");const isCards=view=="cards";cards.classList.toggle("hide",!isCards);tw.classList.toggle("hide",isCards);emp.classList.toggle("hide",r.length>0);if(isCards)renderCards(r);else renderTable(r);syncControls()}
async function load(){try{const j=await (await fetch("/data.json?_="+Date.now())).json();DATA=j.tokens||[];$("n").textContent=DATA.length;const tbn=DATA.reduce((s,x)=>s+(x.benef||0),0),tw=DATA.reduce((s,x)=>s+(x.wallets||0),0);const e=$("tbn");e.textContent=(tbn>=0?"+":"")+tbn.toFixed(1);e.className=tbn>=0?"pos":"neg";$("tw").textContent=tw;$("upd").textContent="maj "+ago(j.updated);render()}catch(err){$("upd").textContent="hors-ligne"}}
$("q").oninput=render;
$("sortsel").onchange=e=>{sortK=e.target.value;render()};
$("dir").onclick=()=>{asc=!asc;render()};
$("bfseg").onclick=e=>{const b=e.target.closest("button");if(!b)return;bf=b.dataset.bf;render()};
$("viewseg").onclick=e=>{const b=e.target.closest("button");if(!b)return;view=b.dataset.v;localStorage.setItem("vmview",view);render()};
document.querySelectorAll("th[data-k]").forEach(th=>th.onclick=()=>{const k=th.dataset.k;if(sortK==k)asc=!asc;else{sortK=k;asc=(k=="sym")}render()});
load();setInterval(load,60000);
</script></body></html>"""

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/data.json":
            with LOCK:
                toks = sorted(RECORDS.values(), key=lambda x: x.get("date", 0), reverse=True)
                body = json.dumps({"updated": LAST_CHECK, "tokens": toks}, ensure_ascii=False).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers(); self.wfile.write(body)
        elif path in ("/health", "/healthz"):
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
        else:
            b = PAGE.encode()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers(); self.wfile.write(b)

if __name__ == "__main__":
    load_state()
    threading.Thread(target=poller, daemon=True).start()
    log(f"web sur :{PORT}  (poll {POLL_MIN}-{POLL_MIN+POLL_JITTER}s, proxy={'oui' if PROXY else 'non'})")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
