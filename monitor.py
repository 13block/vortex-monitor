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
<style>
*{box-sizing:border-box}body{margin:0;background:#0b0e14;color:#e6e9ef;font-family:-apple-system,Segoe UI,Roboto,sans-serif;font-size:14px}
header{padding:16px 22px;border-bottom:1px solid #1c222e;position:sticky;top:0;background:#0b0e14;z-index:5}
h1{margin:0;font-size:18px}.sub{color:#8b94a7;font-size:12px;margin-top:4px}
.bar{display:flex;gap:12px;flex-wrap:wrap;margin-top:12px;align-items:center}
.stat{background:#131825;border:1px solid #1c222e;border-radius:8px;padding:7px 12px}
.stat b{display:block;font-size:15px}.stat span{font-size:11px;color:#8b94a7}
input{background:#131825;border:1px solid #2a3242;color:#e6e9ef;border-radius:8px;padding:8px 12px;font-size:13px;width:230px}
.spacer{flex:1}
.seg{display:inline-flex;border:1px solid #2a3242;border-radius:8px;overflow:hidden}
.seg button{background:#131825;color:#9aa4b8;border:0;padding:7px 14px;font-size:12px;cursor:pointer}
.seg button.on{background:#1a2030;color:#fff}
#upd{color:#8b94a7;font-size:12px}
.wrap{padding:14px 18px 70px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(248px,1fr));gap:12px}
.card{background:#131825;border:1px solid #1c222e;border-radius:12px;padding:13px;display:flex;flex-direction:column;gap:11px;transition:border-color .15s,transform .15s}
.card:hover{border-color:#2f3a4d;transform:translateY(-1px)}
.chead{display:flex;align-items:center;gap:10px;min-width:0}
.avw{position:relative;flex:none}
.avw .ph{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-weight:700;color:#5b6478;background:#0e131c}
.avw .ph,.avw img{border-radius:10px;border:1px solid #222a38}
.avw img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.avw.av{width:42px;height:42px;font-size:16px}
.avw.tav{width:22px;height:22px;font-size:11px;display:inline-block;vertical-align:middle;margin-right:7px}
.tt{min-width:0;flex:1}
.tname{font-weight:700;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ttick{color:#8b94a7;font-size:12px;font-family:ui-monospace,monospace}
.badge{background:#1c3a2a;color:#3ddc84;border:1px solid #2c5a42;border-radius:5px;font-size:10px;padding:1px 5px;flex:none}
.cstats{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}
.cell{background:#0e131c;border:1px solid #161b26;border-radius:8px;padding:6px 8px;overflow:hidden}
.cell .k{font-size:9px;color:#7c8699;text-transform:uppercase;letter-spacing:.05em}
.cell .v{font-size:13px;font-variant-numeric:tabular-nums;font-family:ui-monospace,monospace;margin-top:2px;white-space:nowrap}
.cfoot{display:flex;align-items:center;gap:7px}
.ico{color:#9aa4b8;display:inline-flex;padding:5px;border:1px solid #2a3242;border-radius:8px;line-height:0}
.ico:hover{color:#fff;background:#1a2030;border-color:#3a4763}
a.gm{color:#7aa2ff;text-decoration:none;border:1px solid #2a3242;padding:4px 9px;border-radius:8px;font-size:12px;margin-left:auto}
a.gm:hover{background:#1a2030}
.pos{color:#3ddc84}.neg{color:#ff5c5c}
table{border-collapse:collapse;width:100%}
th,td{padding:9px 12px;text-align:right;white-space:nowrap;border-bottom:1px solid #161b26}
th{position:sticky;top:118px;background:#11151f;cursor:pointer;user-select:none;font-size:12px;color:#9aa4b8}
th:hover{color:#fff}th.l,td.l{text-align:left}tr:hover td{background:#10141d}
td.num{font-variant-numeric:tabular-nums;font-family:ui-monospace,Menlo,monospace}
.sym{font-weight:700}.nm{color:#8b94a7;font-size:12px}
.arrow{font-size:10px;opacity:.6;margin-left:3px}.rank{color:#5b6478}
.hide{display:none}
</style></head><body>
<header><h1>Vortex Monitor</h1>
<div class="sub">Page live · auto-actualisée · benef = sell − buy (SOL) · ATH = market cap max (pump.fun)</div>
<div class="bar">
<div class="stat"><b id="n">-</b><span>tokens</span></div>
<div class="stat"><b id="tbn">-</b><span>benef total SOL</span></div>
<div class="stat"><b id="tw">-</b><span>wallets total</span></div>
<input id="q" placeholder="Rechercher symbole / nom / CA">
<div class="spacer"></div>
<div class="seg"><button id="vcards">Cartes</button><button id="vtable">Tableau</button></div>
<span id="upd"></span>
</div></header>
<div class="wrap">
<div id="cards" class="cards"></div>
<table id="t" class="hide"><thead><tr>
<th class="l">#</th><th class="l" data-k="sym">Token</th><th data-k="date">Date</th>
<th data-k="buy">Buy</th><th data-k="sell">Sell</th><th data-k="benef">Benef</th>
<th data-k="wallets">Wallets</th><th data-k="ath">ATH</th><th class="l">Liens</th>
</tr></thead><tbody id="b"></tbody></table>
</div>
<script>
let DATA=[],sortK="date",asc=false,view=localStorage.getItem("vmview")||"cards";
const fmtD=ms=>{if(!ms)return"-";const d=new Date(ms);return d.toLocaleString("fr-FR",{timeZone:"Europe/Paris",year:"2-digit",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"})};
const ago=ms=>{if(!ms)return"-";const s=(Date.now()-ms)/1000;if(s<3600)return Math.floor(s/60)+"min";if(s<86400)return Math.floor(s/3600)+"h";return Math.floor(s/86400)+"j"};
const fmtUsd=v=>{if(v==null||isNaN(v))return"-";v=+v;if(v>=1e9)return"$"+(v/1e9).toFixed(2)+"B";if(v>=1e6)return"$"+(v/1e6).toFixed(2)+"M";if(v>=1e3)return"$"+(v/1e3).toFixed(1)+"K";return"$"+v.toFixed(0)};
const esc=s=>(s==null?"":""+s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const SVG={web:'<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.6 2.7 2.6 15.3 0 18M12 3c-2.6 2.7-2.6 15.3 0 18"/></svg>',x:'<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M18.24 2.25h3.3l-7.22 8.26 8.5 11.24h-6.66l-5.21-6.82-5.97 6.82H1.68l7.73-8.84L1.25 2.25h6.83l4.71 6.23 5.45-6.23zm-1.16 17.52h1.83L7.01 4.13H5.05z"/></svg>',tg:'<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M21.95 4.3l-3.32 15.66c-.25 1.1-.9 1.37-1.83.85l-5.05-3.72-2.44 2.35c-.27.27-.5.5-1.02.5l.36-5.13L18.4 6.4c.4-.36-.09-.56-.62-.2L6.7 13.18l-4.98-1.56c-1.08-.34-1.1-1.08.23-1.6l19.46-7.5c.9-.33 1.69.2 1.4 1.78z"/></svg>'};
const avatar=(x,sz)=>`<div class="avw ${sz}"><div class="ph">${esc((x.sym||"?")[0])}</div>${x.image?`<img src="${esc(x.image)}" loading="lazy" onerror="this.remove()">`:""}</div>`;
const links=x=>{let h="";if(x.website)h+=`<a class="ico" href="${esc(x.website)}" target="_blank" rel="noopener" title="Website">${SVG.web}</a>`;if(x.twitter)h+=`<a class="ico" href="${esc(x.twitter)}" target="_blank" rel="noopener" title="Twitter">${SVG.x}</a>`;if(x.telegram)h+=`<a class="ico" href="${esc(x.telegram)}" target="_blank" rel="noopener" title="Telegram">${SVG.tg}</a>`;h+=`<a class="gm" href="https://gmgn.ai/sol/token/${esc(x.ca)}" target="_blank" rel="noopener">gmgn</a>`;return h};
function filtered(){let r=DATA.slice();const q=document.getElementById("q").value.toLowerCase().trim();if(q)r=r.filter(x=>(x.sym+" "+x.name+" "+x.ca).toLowerCase().includes(q));r.sort((a,c)=>{let va=a[sortK]??0,vc=c[sortK]??0;if(sortK=="sym"){va=(a.sym||"").toLowerCase();vc=(c.sym||"").toLowerCase()}if(va<vc)return asc?-1:1;if(va>vc)return asc?1:-1;return 0});return r}
function renderCards(r){document.getElementById("cards").innerHTML=r.map(x=>{const bc=x.benef>0?"pos":(x.benef<0?"neg":"");const sg=x.benef>0?"+":"";const isnew=(Date.now()-x.date)<3600000?'<span class="badge">NEW</span>':"";return `<div class="card"><div class="chead">${avatar(x,"av")}<div class="tt"><div class="tname">${esc(x.name||x.sym)}</div><div class="ttick">$${esc(x.sym)}</div></div>${isnew}</div><div class="cstats"><div class="cell"><div class="k">Buy</div><div class="v">${(x.buy||0).toFixed(2)}</div></div><div class="cell"><div class="k">Sell</div><div class="v">${(x.sell||0).toFixed(2)}</div></div><div class="cell"><div class="k">Benef</div><div class="v ${bc}">${sg}${(x.benef||0).toFixed(2)}</div></div><div class="cell"><div class="k">Wallets</div><div class="v">${x.wallets||0}</div></div><div class="cell"><div class="k">ATH MC</div><div class="v">${fmtUsd(x.ath)}</div></div><div class="cell"><div class="k">Âge</div><div class="v">${ago(x.date)}</div></div></div><div class="cfoot">${links(x)}</div></div>`}).join("")}
function renderTable(r){document.getElementById("b").innerHTML=r.map((x,i)=>{const bc=x.benef>0?"pos":(x.benef<0?"neg":"");const sg=x.benef>0?"+":"";const isnew=(Date.now()-x.date)<3600000?'<span class="badge">NEW</span>':"";return `<tr><td class="l rank">${i+1}</td><td class="l">${avatar(x,"tav")}<span class="sym">$${esc(x.sym)}</span> <span class="nm">${esc(x.name||"")}</span> ${isnew}</td><td class="num">${fmtD(x.date)}</td><td class="num">${(x.buy||0).toFixed(2)}</td><td class="num">${(x.sell||0).toFixed(2)}</td><td class="num ${bc}">${sg}${(x.benef||0).toFixed(2)}</td><td class="num">${x.wallets||0}</td><td class="num">${fmtUsd(x.ath)}</td><td class="l">${links(x)}</td></tr>`}).join("");document.querySelectorAll("th[data-k]").forEach(th=>{th.querySelector(".arrow")?.remove();if(th.dataset.k==sortK){const s=document.createElement("span");s.className="arrow";s.textContent=asc?"▲":"▼";th.appendChild(s)}})}
function render(){const r=filtered();const cards=document.getElementById("cards"),tbl=document.getElementById("t");if(view=="cards"){cards.classList.remove("hide");tbl.classList.add("hide");renderCards(r)}else{tbl.classList.remove("hide");cards.classList.add("hide");renderTable(r)}document.getElementById("vcards").classList.toggle("on",view=="cards");document.getElementById("vtable").classList.toggle("on",view=="table")}
async function load(){const j=await (await fetch("/data.json?_="+Date.now())).json();DATA=j.tokens;document.getElementById("n").textContent=DATA.length;const tbn=DATA.reduce((s,x)=>s+(x.benef||0),0),tw=DATA.reduce((s,x)=>s+(x.wallets||0),0);const e=document.getElementById("tbn");e.textContent=(tbn>=0?"+":"")+tbn.toFixed(1);e.className=tbn>=0?"pos":"neg";document.getElementById("tw").textContent=tw;document.getElementById("upd").textContent="Maj il y a "+ago(j.updated);render()}
document.querySelectorAll("th[data-k]").forEach(th=>th.onclick=()=>{const k=th.dataset.k;if(sortK==k)asc=!asc;else{sortK=k;asc=(k=="sym")}render()});
document.getElementById("q").oninput=render;
document.getElementById("vcards").onclick=()=>{view="cards";localStorage.setItem("vmview",view);render()};
document.getElementById("vtable").onclick=()=>{view="table";localStorage.setItem("vmview",view);render()};
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
