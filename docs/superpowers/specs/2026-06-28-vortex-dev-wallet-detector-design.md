# Vortex Dev-Wallet Detector — Design

**Date:** 2026-06-28
**Statut:** spec validé (design), prêt pour le plan d'implémentation
**Contexte projet:** extension de `vortex-monitor` (monitor.py + seed.json)

---

## 1. Objectif

Pour un token donné (lancé via Vortex Deployer sur pump.fun), identifier les wallets
contrôlés par le deployer (« dev wallets ») avec une **preuve par wallet** et un **score
de confiance**, intégré à la page web existante du monitor.

Ce que l'outil **ne** prétend **pas** faire : sortir la liste exacte et complète des N
wallets. L'investigation (voir §3) a montré que c'est impossible de façon fiable. L'outil
privilégie la **précision** (les wallets remontés sont quasi-certains) et **mesure** sa
couverture via l'oracle.

---

## 2. Existant

- `monitor.py` : service Python stdlib-only. Poll le sitemap de vortexdeployer.com,
  scrape chaque page token (buy/sell volume, wallets, benef), sert une page web live,
  push Discord. État dans `records.json` (seed `seed.json`).
- `seed.json` / `records.json` : `{ca: {ca, sym, name, date, buy, sell, benef, wallets}}`.
  - **`wallets` (= N), `buy`, `sell` constituent l'ORACLE** : totaux du dev d'après Vortex.
- Tous les tokens finissent en `pump` (pump.fun), metadata hébergée sur
  `api.vortexdeployer.com` (= signature Vortex).

---

## 3. Conclusions de l'investigation (8 prototypes, données réelles)

### Ce qui NE marche PAS (validé empiriquement)
- **Clustering par graphe impossible** (opsec délibérée du dev) :
  - Amont : les wallets sont financés par des **méga-funders partagés** (ex. `BmFdpr…` =
    1173+ destinataires, infra type service/CEX), funding **individuel** et **étalé sur
    30h+** avant le launch → pas de lien ni de fenêtre exploitable.
  - Aval : chaque wallet draine vers une **sortie unique** → pas de consolidation commune.
  - Fenêtre de launch (via pump-trades) : **saturée de bots snipers** (n=100 tx, 20-40
    autres tokens) ; les wallets dev frais n'y apparaissent pas.
- **Pas de signature Vortex on-chain unique** : fees non uniformes, pas de tip Jito
  constant, pas de compte fee dédié.

### Ce qui MARCHE (signaux par wallet, validés)
Un wallet **donné** se classe avec une précision quasi-certaine. Exemple `Gy2DKu…`
(dev COMOS confirmé) vs un bot sniper :

| Signal | Dev (Gy2DKu) | Bot |
|---|---|---|
| nb tx total (fraîcheur) | 17 | 100+ |
| autres tokens tradés (mono) | 0 | 20–40 |
| solde actuel (vidé) | 0.002 SOL | variable |
| buy → sell (profit) | 0.586 → 3.324 | petits scalps |

### Sources de données retenues (minimise la charge Helius)
| Besoin | Source | Note |
|---|---|---|
| creator, bonding curve, date, confirmation Vortex | pump.fun `GET /coins/{mint}` | 1 appel ; `metadata_uri` host = `api.vortexdeployer.com` |
| Participants complets du launch | Helius RPC `getSignaturesForAddress(mint)` (paginé jusqu'au plus ancien) | **source complète** (inclut les wallets frais) ; lourd sur token chaud → cacher |
| Trades (seed/contexte) | pump.fun `GET swap-api.pump.fun/v2/coins/{mint}/trades` (curseur craftable au slot) | gratuit ; plafonné ~2000 en pagination « newest→old » |
| buy/sell par wallet, funder, cashout, fraîcheur | Helius **Enhanced** `GET /v0/addresses/{w}/transactions` | parsé ; trades Vortex apparaissent en `type=TRANSFER`, token rattaché au token account via champ `userAccount` |
| solde actuel (drained) | Helius RPC `getBalance` | 1 appel/wallet |
| compte programme vs wallet (denylist) | Helius RPC `getAccountInfo.owner` (≠ system = programme) | pour filtrer comptes AMM/pump |

Contraintes API : clé Helius **Dev plan**, RPC + Enhanced OK. **Pas de batch JSON-RPC**
(413), **pas** d'ancienne API enhanced sur free. Appels **unitaires**, concurrence limitée.

---

## 4. Architecture

Modules isolés, testables séparément (le gros de la logique hors du poll loop de monitor.py) :

- **`helius.py`** — client RPC + Enhanced : retries/backoff, gestion 429/413/403, appels
  unitaires, petit pool de concurrence. Entrée : méthode+params. Sortie : JSON.
- **`pumpfun.py`** — client pump.fun : `coins(mint)`, `trades(mint, cursor)`, helper de
  craft de curseur au slot de launch.
- **`features.py`** — extraction **pure** des features d'un wallet à partir des données
  déjà fetchées (n_tx, n_other_mints, buy/sell sur le mint, funder, solde, lifespan,
  drain dest). Pas d'I/O → testable avec fixtures.
- **`classifier.py`** — fonction **pure** : features + poids → `dev_score` + label +
  raisons. Pas d'I/O.
- **`registry.py`** — JSON persistant : méga-funders connus (infra partagée à ignorer,
  seuil de destinataires), wallets/funders déjà vus. Mise à jour incrémentale.
- **`detector.py`** — orchestration des phases pour un token → objet résultat + cache
  (`detections.json`).
- **web** (dans `monitor.py` ou `web.py`) — endpoint `/analyze?ca=…` (déclenche un job),
  endpoint `/detection?ca=…` (résultat JSON), page détail par token. File d'attente
  mono-worker pour ne pas bloquer le serveur.

---

## 5. Pipeline de détection (pour un CA)

1. **Contexte token** — pump.fun `/coins` → creator, bonding_curve, created_timestamp,
   confirme Vortex (host metadata). Oracle (N/buy/sell) depuis `records.json` si présent.
2. **Énumérer les participants** — RPC `getSignaturesForAddress(mint)` paginé jusqu'au
   plus ancien (cache le slot de launch + la liste). Participants = signers distincts dans
   `[slot_launch, slot_launch + W]`. Inclut les wallets frais (contrairement à pump-trades).
3. **Features par participant** — Enhanced (paginé si n=100) + getBalance :
   `n_tx`, `n_other_mints`, `buy_sol`, `sell_sol`, `funder`, `balance`, `lifespan`,
   `drain_dest` (plus grand sortant vers un compte system-owned).
4. **Classer** — `dev_score` pondéré : mono/peu-d'autres-tokens (fort), frais (fort),
   vidé (fort), a tradé (requis), profit (moyen), financé peu avant le 1er achat par un
   funder **non méga-partagé** (moyen). Seuil → set dev haute-confiance.
5. **Confiance via oracle** — compare `|dev|` à N, `Σbuy` à buy, `Σsell` à sell →
   couverture % (ex. « 24 wallets, buy 78%, sell 71% »). Sans oracle : score interne seul.
6. **Sortie** — par wallet : adresse + preuves (frais/mono/vidé/buy-sell-profit/funder/
   drain) + score. Token : creator, Vortex confirmé, funders observés (flag infra
   partagée), drains (pour pivot manuel), confiance vs oracle.

---

## 6. Intégration web

- `/analyze?ca=…` met le token en file ; un worker exécute le pipeline (30–60 s) et écrit
  dans `detections.json` (cache, avec timestamp ; ré-analyse à la demande).
- `/detection?ca=…` renvoie le résultat (ou `pending`).
- La page tableau existante : un bouton « Analyser » par ligne → page détail affichant
  les wallets classés, leurs preuves, et la jauge de confiance vs oracle.
- **Hors** poll loop (analyse on-demand uniquement).

---

## 7. Gestion d'erreurs

- Échecs API : retries+backoff ; résultats partiels marqués `partial:true` ; ne jamais
  faire planter le poll loop ni le serveur.
- Pagination plafonnée (token chaud) : cacher le slot de launch ; si non atteint →
  `launch_slot: estimated/partial`.
- Token absent de l'oracle : classer quand même, pas de couverture %.
- Rate limits : concurrence bornée, appels unitaires.

---

## 8. Tests

- **Unitaires** `features.py` / `classifier.py` avec fixtures JSON réelles issues de
  l'investigation : `Gy2DKu` ⇒ dev ; un bot sniper (n=100, nother≈30) ⇒ non-dev.
- **Intégration** sur COMOS : creator correct, `Gy2DKu` présent, confiance cohérente.
- **Golden** : quelques wallets labellisés (dev / bot) pour non-régression du scorer.

---

## 9. Limites (assumées, pas de « à peu près »)

- **Recall partiel** : on attrape les wallets dev disciplinés/early ; on peut rater les
  achats dev très tardifs ou atypiques. L'oracle **quantifie** la couverture — l'outil ne
  ment pas sur ce qu'il ignore.
- **Précision** : priorité ; chaque wallet remonté vient avec sa preuve.
- Attribution **opérateur** cross-token non fiable (funders partagés) → hors v1.

---

## 10. Hors scope (YAGNI v1)

- Copy-trading / alertes temps réel « relance d'un opérateur connu » (futur, s'appuiera
  sur `registry.py`).
- Identification d'opérateur entre tokens.
- Denylist exhaustive de comptes protocole au-delà du check `owner` programme.
