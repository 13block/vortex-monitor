#!/usr/bin/env python3
# Vortex Deployer - monitor de nouveaux launches (stdlib only).
# Poll le sitemap (10-15 min, jitter) -> diff -> scrape -> Discord, et sert une page web auto-actualisee.
import os, re, json, time, random, threading, urllib.request, urllib.error, ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------- Config (variables d'environnement) ----------
PORT         = int(os.environ.get("PORT", "8080"))
DISCORD_RAW  = os.environ.get("DISCORD_WEBHOOK", "")
WEBHOOKS     = [w.strip().strip("'\"").strip() for w in re.split(r"[,\s]+", DISCORD_RAW) if w.strip()]
WEBHOOKS     = [w for w in WEBHOOKS if w.startswith("http")]
POLL_MIN     = int(os.environ.get("POLL_MIN", "600"))      # delai mini (s) -> 600 = 10 min
POLL_JITTER  = int(os.environ.get("POLL_JITTER", "300"))   # alea (s)       -> +0..300 = jusqu'a 15 min
DATA_DIR     = os.environ.get("DATA_DIR", "/data")
PROXY        = os.environ.get("PROXY", "").strip()         # ex: http://user:pass@host:port (optionnel)
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

def post_discord(d):
    if not WEBHOOKS: return
    color = 0x3ddc84 if d["benef"] > 0 else (0xff5c5c if d["benef"] < 0 else 0x8b94a7)
    ca = d["ca"]
    payload = {"username": "Vortex Monitor", "embeds": [{
        "title": f"Nouveau launch : ${d['sym']}" + (f" - {d['name']}" if d['name'] else ""),
        "url": f"https://gmgn.ai/sol/token/{ca}", "color": color,
        "fields": [
            {"name": "Buy", "value": f"{d['buy']:.2f} SOL", "inline": True},
            {"name": "Sell", "value": f"{d['sell']:.2f} SOL", "inline": True},
            {"name": "Benef", "value": f"{'+' if d['benef']>0 else ''}{d['benef']:.2f} SOL", "inline": True},
            {"name": "Wallets", "value": str(d["wallets"]), "inline": True},
            {"name": "CA", "value": f"`{ca}`", "inline": False},
            {"name": "Liens", "value": f"[gmgn](https://gmgn.ai/sol/token/{ca}) | [vortex]({SITE}/token/{ca})", "inline": False},
        ],
        "timestamp": __import__("datetime").datetime.fromtimestamp(d["date"]/1000, __import__("datetime").timezone.utc).isoformat()}]}
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
        with LOCK:
            RECORDS[ca] = d; save_state()
        if BASELINE_DONE:
            post_discord(d); log("NEW", d["sym"], ca)
        time.sleep(0.4)
    if not BASELINE_DONE:
        BASELINE_DONE = True
        log("baseline etablie (silencieuse):", len(RECORDS))
    LAST_CHECK = int(time.time() * 1000)

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
.bar{display:flex;gap:14px;flex-wrap:wrap;margin-top:12px;align-items:center}
.stat{background:#131825;border:1px solid #1c222e;border-radius:8px;padding:7px 12px}
.stat b{display:block;font-size:15px}.stat span{font-size:11px;color:#8b94a7}
input{background:#131825;border:1px solid #2a3242;color:#e6e9ef;border-radius:8px;padding:8px 12px;font-size:13px;width:240px}
.wrap{padding:0 12px 60px}table{border-collapse:collapse;width:100%;margin-top:8px}
th,td{padding:9px 12px;text-align:right;white-space:nowrap;border-bottom:1px solid #161b26}
th{position:sticky;top:120px;background:#11151f;cursor:pointer;user-select:none;font-size:12px;color:#9aa4b8}
th:hover{color:#fff}th.l,td.l{text-align:left}tr:hover td{background:#10141d}
td.num{font-variant-numeric:tabular-nums;font-family:ui-monospace,Menlo,monospace}
.pos{color:#3ddc84}.neg{color:#ff5c5c}.sym{font-weight:700}.nm{color:#8b94a7;font-size:12px}
a.gm{color:#7aa2ff;text-decoration:none;border:1px solid #2a3242;padding:3px 8px;border-radius:6px;font-size:12px}
a.gm:hover{background:#1a2030}.arrow{font-size:10px;opacity:.6;margin-left:3px}.rank{color:#5b6478}
.badge{background:#1c3a2a;color:#3ddc84;border:1px solid #2c5a42;border-radius:5px;font-size:10px;padding:1px 5px;margin-left:6px}
#upd{color:#8b94a7;font-size:12px}
</style></head><body>
<header><h1>Vortex Monitor</h1>
<div class="sub">Page live - auto-actualisee. Volume/wallets = pages vortex - benef = sell-buy (SOL) - liens gmgn.</div>
<div class="bar">
<div class="stat"><b id="n">-</b><span>tokens</span></div>
<div class="stat"><b id="tbn">-</b><span>benef total SOL</span></div>
<div class="stat"><b id="tw">-</b><span>wallets total</span></div>
<input id="q" placeholder="Rechercher symbole / nom / CA"><span id="upd"></span>
</div></header>
<div class="wrap"><table id="t"><thead><tr>
<th class="l">#</th><th class="l" data-k="sym">Token</th><th data-k="date">Date</th>
<th data-k="buy">Buy</th><th data-k="sell">Sell</th><th data-k="benef">Benef</th>
<th data-k="wallets">Wallets</th><th class="l">Lien</th>
</tr></thead><tbody id="b"></tbody></table></div>
<script>
let DATA=[],sortK="date",asc=false;
const fmtD=ms=>{if(!ms)return"-";const d=new Date(ms);return d.toLocaleString("fr-FR",{timeZone:"Europe/Paris",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"})};
const ago=ms=>{const s=(Date.now()-ms)/1000;if(s<3600)return Math.floor(s/60)+"min";if(s<86400)return Math.floor(s/3600)+"h";return Math.floor(s/86400)+"j"};
function render(){
 let r=DATA.slice();const q=document.getElementById("q").value.toLowerCase().trim();
 if(q)r=r.filter(x=>(x.sym+" "+x.name+" "+x.ca).toLowerCase().includes(q));
 r.sort((a,c)=>{let va=a[sortK],vc=c[sortK];if(sortK=="sym"){va=(a.sym||"").toLowerCase();vc=(c.sym||"").toLowerCase()}if(va<vc)return asc?-1:1;if(va>vc)return asc?1:-1;return 0});
 document.getElementById("b").innerHTML=r.map((x,i)=>{
  const bc=x.benef>0?"pos":(x.benef<0?"neg":"");const sg=x.benef>0?"+":"";
  const isnew=(Date.now()-x.date)<3600000?'<span class="badge">NEW</span>':"";
  return `<tr><td class="l rank">${i+1}</td>
  <td class="l"><span class="sym">$${x.sym}</span> <span class="nm">${x.name||""}</span>${isnew}</td>
  <td class="num">${fmtD(x.date)}</td><td class="num">${x.buy.toFixed(2)}</td>
  <td class="num">${x.sell.toFixed(2)}</td><td class="num ${bc}">${sg}${x.benef.toFixed(2)}</td>
  <td class="num">${x.wallets}</td>
  <td class="l"><a class="gm" href="https://gmgn.ai/sol/token/${x.ca}" target="_blank">gmgn</a></td></tr>`}).join("");
 document.querySelectorAll("th[data-k]").forEach(th=>{th.querySelector(".arrow")?.remove();if(th.dataset.k==sortK){const s=document.createElement("span");s.className="arrow";s.textContent=asc?"▲":"▼";th.appendChild(s)}});
}
async function load(){
 const j=await (await fetch("/data.json?_="+Date.now())).json();
 DATA=j.tokens;
 document.getElementById("n").textContent=DATA.length;
 const tbn=DATA.reduce((s,x)=>s+x.benef,0),tw=DATA.reduce((s,x)=>s+(x.wallets||0),0);
 const e=document.getElementById("tbn");e.textContent=(tbn>=0?"+":"")+tbn.toFixed(1);e.className=tbn>=0?"pos":"neg";
 document.getElementById("tw").textContent=tw;
 document.getElementById("upd").textContent="Maj sitemap il y a "+ago(j.updated);
 render();
}
document.querySelectorAll("th[data-k]").forEach(th=>th.onclick=()=>{const k=th.dataset.k;if(sortK==k)asc=!asc;else{sortK=k;asc=(k=="sym")}render()});
document.getElementById("q").oninput=render;
load();setInterval(load,60000);
</script></body></html>"""

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith("/data.json"):
            with LOCK:
                toks = sorted(RECORDS.values(), key=lambda x: x.get("date", 0), reverse=True)
                body = json.dumps({"updated": LAST_CHECK, "tokens": toks}, ensure_ascii=False).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers(); self.wfile.write(body)
        elif self.path in ("/health", "/healthz"):
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
