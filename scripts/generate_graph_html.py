#!/usr/bin/env python3
"""
generate_graph_html.py — Neuron Graph Visualizer (v2, T61).

Reads the graph through Neuron's OWN storage engine (neuron.models.Graph), so
it works on every tier — local SQLite, local Turso file, and **Turso Cloud**
(the previous version used raw sqlite3 and was blind to the cloud). Exports
EVERY context (nodes, links, episodes/facts) and generates a self-contained
interactive HTML: force-directed physics (kept), domain palette, salience
sizing, drift-link styling, neighborhood highlight, search, domain/type
filters, an insights panel (hubs, dormant, strongest synapses) and a
time-travel slider that replays the graph growing turn by turn.

Usage:
    python scripts/generate_graph_html.py [--context NAME] [--no-open]
"""

import argparse
import glob as _glob
import json
import os
import sys
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (single source of truth, same logic as _neuron_paths.ps1)
# ---------------------------------------------------------------------------
SLUG = os.environ.get("NEURON_SLUG", "neuron5")

def _default_graphs_dir() -> str:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, SLUG, "graphs")
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, SLUG, "graphs")

GRAPHS_DIR = Path(os.environ.get("NS_GRAPHS_DIR") or _default_graphs_dir())

def _default_install_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / "Programs" / SLUG
    return Path.home() / ".local" / "share" / SLUG

INSTALL_DIR = _default_install_dir()
OUTPUT_DIR = INSTALL_DIR / "NeuronGraphExportHTML"

# ---------------------------------------------------------------------------
# Extraction — through Neuron's engine (cloud-aware), sqlite3 fallback
# ---------------------------------------------------------------------------
# Import neuron: venv (installed) first, then repo src/ (source checkout).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
try:
    from neuron import db as _db                     # loads .env too (neuron/__init__)
    from neuron.models import Graph
    _ENGINE = getattr(_db, "ENGINE_NAME", "sqlite")
    _REMOTE = bool(getattr(_db, "REMOTE_TURSO", False))
    _HAVE_NEURON = True
except Exception as _e:                              # pragma: no cover
    _db = None
    _ENGINE, _REMOTE, _HAVE_NEURON = "sqlite3 (fallback)", False, False
    print(f"  [!] neuron package not importable ({_e}); local-only sqlite fallback.")


def list_contexts() -> list[str]:
    """Every context: local graph_*.db files + (on cloud) DISTINCT context."""
    ctxs: set[str] = set()
    for p in _glob.glob(str(GRAPHS_DIR / "graph_*.db")):
        name = Path(p).stem[len("graph_"):]
        ctxs.add(name.replace("__", "/"))            # registry path flattening
    if _HAVE_NEURON and _REMOTE:
        try:
            conn = _db.connect("")                    # remote tier ignores path
            try:
                for (c,) in conn.execute("SELECT DISTINCT context FROM nodes").fetchall():
                    if c:
                        ctxs.add(c)
            finally:
                conn.close()
        except Exception as e:
            print(f"  [!] cloud context listing failed: {e}")
    return sorted(ctxs) or ["default"]


def export_graph(context: str = "default") -> dict:
    """Export one context via the engine (any tier). Includes episodes/facts."""
    local_path = str(GRAPHS_DIR / f"graph_{context.replace('/', '__')}.db")
    if _HAVE_NEURON:
        g = Graph()
        g.load_sqlite(local_path, context=context)   # remote tier reads the cloud
        nodes = [{
            "keyword": nd.keyword, "turn": nd.turn, "topic": nd.topic or "",
            "domain": nd.domain or "general", "sentiment": nd.sentiment or "neutral",
            "salience": nd.salience or 1,
            "entities": nd.entities or [], "tags": nd.tags or [],
            "episodes": [e["text"] for e in
                         sorted(g.episodes.get(nd.keyword, []), key=lambda e: -e["turn"])][:5],
        } for nd in g.nodes]
        links = [{
            "source": lk.source, "target": lk.target,
            "link_type": lk.link_type or "deepening", "weight": lk.weight or "medium",
            "rationale": lk.rationale or "",
            "created_turn": lk.created_turn or 0,
            "last_active_turn": lk.last_active_turn or 0,
            "coact": getattr(lk, "co_activation_count", 0) or 0,
            "drift": getattr(lk, "target_context", None),
        } for lk in g.links]
        return {"context": context, "turn_count": g.turn_count,
                "nodes": nodes, "links": links}

    # --- raw sqlite3 fallback (no neuron import) ---
    import sqlite3
    if not os.path.exists(local_path):
        return {"context": context, "turn_count": 0, "nodes": [], "links": []}
    conn = sqlite3.connect(local_path)
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        nodes = [{
            "keyword": r[0], "turn": r[1], "topic": r[2] or "", "domain": r[3] or "general",
            "sentiment": r[4] or "neutral", "salience": r[5] or 1,
            "entities": json.loads(r[6] or "[]"), "tags": json.loads(r[7] or "[]"),
            "episodes": [],
        } for r in conn.execute(
            "SELECT keyword, turn, topic, domain, sentiment, salience, "
            "COALESCE(entities,'[]'), COALESCE(tags,'[]'), COALESCE(refs,'[]') "
            "FROM nodes ORDER BY id")]
        links = [{
            "source": r[0], "target": r[1], "link_type": r[2] or "deepening",
            "weight": r[3] or "medium", "rationale": r[4] or "",
            "created_turn": r[5] or 0, "last_active_turn": r[6] or 0,
            "coact": 0, "drift": None,
        } for r in conn.execute(
            "SELECT source, target, link_type, weight, rationale, "
            "created_turn, last_active_turn FROM links ORDER BY id")]
        return {"context": context, "turn_count": int(meta.get("turn_count", "0") or 0),
                "nodes": nodes, "links": links}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# vis-network (cached download; CDN <script src> as last resort)
# ---------------------------------------------------------------------------
VIS_NETWORK_URL = "https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"
VIS_NETWORK_FILE = OUTPUT_DIR / "vis-network.min.js"

def get_vis_network() -> "str | None":
    if VIS_NETWORK_FILE.exists() and VIS_NETWORK_FILE.stat().st_size > 100_000:
        return VIS_NETWORK_FILE.read_text(encoding="utf-8")
    try:
        print("  Downloading vis-network (one-time, cached)...")
        code = urllib.request.urlopen(VIS_NETWORK_URL, timeout=30).read().decode("utf-8")
        VIS_NETWORK_FILE.write_text(code, encoding="utf-8")
        return code
    except Exception as e:
        print(f"  [!] download failed ({e}) — the HTML will load it from the CDN instead.")
        return None


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def generate_html(all_graphs: dict, active_ctx: str, vis_code: "str | None") -> str:
    vis_tag = (f"<script>{vis_code}</script>" if vis_code
               else f'<script src="{VIS_NETWORK_URL}"></script>')
    data_json = json.dumps(all_graphs, ensure_ascii=False)
    engine = _ENGINE
    return HTML_TEMPLATE \
        .replace("__VIS_TAG__", vis_tag) \
        .replace("__DATA__", data_json) \
        .replace("__ACTIVE_CTX__", json.dumps(active_ctx)) \
        .replace("__ENGINE__", engine)


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Neuron — Memory Graph</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
__VIS_TAG__
<style>
:root{
  --bg0:#0b0e1a; --bg1:#121933; --panel:rgba(18,24,48,.82); --panel-br:rgba(120,140,255,.18);
  --tx:#e8ecff; --tx-dim:#8b93b8; --accent:#7c8cff;
}
*{box-sizing:border-box; margin:0}
html,body{height:100%; overflow:hidden}
body{font:14px/1.45 "Segoe UI",system-ui,-apple-system,sans-serif; color:var(--tx);
     background:radial-gradient(1200px 800px at 70% 20%, var(--bg1), var(--bg0) 70%);}
#net{position:absolute; inset:0}
.panel{position:absolute; background:var(--panel); border:1px solid var(--panel-br);
       border-radius:14px; backdrop-filter:blur(10px); box-shadow:0 8px 32px rgba(0,0,0,.45)}
#topbar{top:14px; left:14px; right:14px; padding:10px 14px; display:flex; gap:8px;
        align-items:center; flex-wrap:nowrap; z-index:10; overflow:hidden}
#topbar b{font-size:15px; letter-spacing:.4px}
#topbar .brain{font-size:18px}
#stats{color:var(--tx-dim); font-size:12px; margin-right:auto}
select,input[type=text]{background:#0e1430; color:var(--tx); border:1px solid var(--panel-br);
        border-radius:8px; padding:6px 10px; font:inherit; outline:none}
input[type=text]{width:160px}
input[type=text]:focus{border-color:var(--accent)}
button{background:#1a2350; color:var(--tx); border:1px solid var(--panel-br); border-radius:8px;
       padding:6px 12px; font:inherit; cursor:pointer}
button:hover{border-color:var(--accent)}
button.on{background:var(--accent); color:#0b0e1a; font-weight:600}
button.small{padding:4px 8px; font-size:11px; min-width:36px}
.chip{display:inline-flex; align-items:center; gap:6px; padding:3px 10px; border-radius:999px;
      border:1px solid var(--panel-br); cursor:pointer; font-size:12px; user-select:none}
.chip .dot{width:9px;height:9px;border-radius:50%}
.chip.off{opacity:.28}
#chips{display:flex; gap:6px; flex-wrap:nowrap; overflow-x:auto; scrollbar-width:none}
#chips::-webkit-scrollbar{display:none}
#insights{left:14px; top:82px; bottom:82px; width:265px; padding:14px; overflow-y:auto; z-index:9}
#insights h3{font-size:11px; text-transform:uppercase; letter-spacing:1.2px; color:var(--tx-dim);
             margin:14px 0 6px}
#insights h3:first-child{margin-top:0}
.item{padding:5px 8px; border-radius:8px; cursor:pointer; display:flex; justify-content:space-between; gap:8px}
.item:hover{background:rgba(124,140,255,.12)}
.item .v{color:var(--tx-dim); font-size:12px; white-space:nowrap}
.item .k{overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
#side{right:14px; top:82px; bottom:82px; width:300px; padding:16px;
      overflow-y:auto; display:none; z-index:9}
#side h2{font-size:17px; margin-bottom:2px; overflow-wrap:anywhere}
#side .sub{color:var(--tx-dim); font-size:12px; margin-bottom:10px}
#side .row{margin:7px 0; font-size:13px}
#side .lbl{color:var(--tx-dim); font-size:11px; text-transform:uppercase; letter-spacing:1px}
.salbar{height:6px; border-radius:4px; background:#0e1430; margin-top:4px; overflow:hidden}
.salbar>div{height:100%; border-radius:4px; background:linear-gradient(90deg,#4be1a0,#7c8cff)}
.fact{background:rgba(124,140,255,.08); border-left:3px solid var(--accent); border-radius:0 8px 8px 0;
      padding:6px 9px; margin:6px 0; font-size:12.5px}
.tag{display:inline-block; background:#1a2350; border-radius:6px; padding:1px 8px; margin:2px 3px 0 0;
     font-size:11.5px; color:var(--tx-dim)}
#timebar{bottom:14px; left:14px; right:14px; padding:10px 16px; display:flex; gap:14px;
         align-items:center; z-index:10}
#turnlbl{min-width:120px; color:var(--tx-dim); font-size:12.5px}
input[type=range]{flex:1; accent-color:var(--accent)}
#legend{position:absolute; right:20px; bottom:82px; font-size:11.5px; color:var(--tx-dim); z-index:8;
        text-align:right}
#legend span{margin-left:10px}
#toast{position:absolute; left:50%; bottom:90px; transform:translateX(-50%); background:var(--panel);
       border:1px solid var(--panel-br); border-radius:10px; padding:8px 16px; display:none; z-index:20}
#stylepanel{right:14px; top:82px; bottom:82px; width:290px; padding:16px;
            overflow-y:auto; display:none; z-index:11}
#stylepanel h3{font-size:11px; text-transform:uppercase; letter-spacing:1.2px; color:var(--tx-dim); margin:12px 0 6px}
#stylepanel h3:first-child{margin-top:0}
.sl{display:flex; align-items:center; gap:10px; margin:6px 0; font-size:12.5px}
.sl label{width:110px; color:var(--tx-dim)}
.sl input[type=range]{flex:1}
.sl .val{width:38px; text-align:right; color:var(--tx-dim); font-size:11.5px}
.colrow{display:flex; align-items:center; gap:8px; margin:4px 0; font-size:12.5px}
.colrow input[type=color]{width:26px; height:22px; border:none; border-radius:6px; background:none; padding:0; cursor:pointer}
#stylepanel .foot{display:flex; gap:8px; margin-top:14px}
::-webkit-scrollbar{width:8px} ::-webkit-scrollbar-thumb{background:#26305e; border-radius:4px}
@keyframes nodeAppear{from{opacity:0;transform:scale(0)} to{opacity:1;transform:scale(1)}}
</style>
</head>
<body>
<div id="net"></div>

<div id="topbar" class="panel">
  <span class="brain">🧠</span><b>Neuron</b>
  <select id="ctxsel" title="context"></select>
  <button id="btn-allctx" title="show all contexts merged">🌐 All</button>
  <span id="stats"></span>
  <div id="chips"></div>
  <input type="text" id="search" placeholder="🔍 find a concept…">
  <button id="btn-insights" class="on" title="toggle insights panel">Insights</button>
  <button id="btn-physics" class="on" title="toggle physics">Physics</button>
  <button id="btn-labels" class="on" title="toggle labels">Labels</button>
  <button id="btn-pulse" class="on" title="heartbeat on the most salient nodes">Pulse</button>
  <button id="btn-style" title="edit appearance (Obsidian-style)">🎨 Style</button>
</div>

<div id="insights" class="panel"></div>
<div id="side" class="panel"></div>
<div id="stylepanel" class="panel"></div>

<div id="timebar" class="panel">
  <button id="btn-play" title="replay the memory growing">▶ Replay</button>
  <button id="btn-speed" title="replay speed">1×</button>
  <input type="range" id="timeline" min="1" max="1" value="1">
  <span id="turnlbl"></span>
</div>
<div id="legend"></div>
<div id="toast"></div>

<script>
const GRAPHS = __DATA__;
let CTX = __ACTIVE_CTX__;
const ENGINE = "__ENGINE__";

// ---- theme ---------------------------------------------------------------
const DOMAIN_COLORS = {
  AI:"#b07cff", backend:"#4be1a0", frontend:"#ffb84d", gaming:"#ff6b9d",
  architecture:"#53c7ff", general:"#8b93b8", devops:"#4be1a0", finance:"#ffd166",
};
function domColor(d){
  if(DOMAIN_COLORS[d]) return DOMAIN_COLORS[d];
  let h=0; for(const c of d) h=(h*31+c.charCodeAt(0))>>>0;   // stable hash → hue
  return `hsl(${h%360} 70% 62%)`;
}
const TYPE_COLORS = {"cause-effect":"#ff6b6b", analogy:"#53c7ff", evolution:"#4be1a0",
                     contrast:"#ffb84d", deepening:"#8b93b8", "instance-of":"#b07cff"};
const WEIGHT_W = {tangential:1, medium:2.4, strong:4.2};

// ---- editable style (Obsidian-like, persisted in localStorage) -------------
const STYLE_DEFAULTS = {nodeScale:1, edgeScale:1, fontScale:1, springLength:110,
                        gravity:-42, colors:{}};
let STYLE = loadStyle();
function loadStyle(){
  try{ return {...STYLE_DEFAULTS, colors:{}, ...JSON.parse(localStorage.getItem("neuron-graph-style")||"{}")}; }
  catch(e){ return {...STYLE_DEFAULTS, colors:{}}; }
}
function saveStyle(){ try{ localStorage.setItem("neuron-graph-style", JSON.stringify(STYLE)); }catch(e){} }
function styledDomColor(d){ return (STYLE.colors && STYLE.colors[d]) || domColor(d); }

// ---- state ---------------------------------------------------------------
let G, network, nodesDS, edgesDS, maxSal=1, maxTurn=1;
let domainOff = new Set(), typeOff = new Set(), tMax = 1e9;
let pulseOn = true, labelsOn = true, pulseTick = 0, selected = null;

function nodeSize(s){ return (10 + Math.sqrt(s/maxSal)*26) * STYLE.nodeScale; }
function isHot(n){ return (maxTurn - n.turn) <= 2; }
function isDormant(n){ return (maxTurn - n.turn) >= 6 && n.salience >= 2; }

function visNode(n){
  const c = styledDomColor(n.domain), hot = isHot(n), dorm = isDormant(n);
  return {
    id:n.keyword, label: labelsOn ? n.keyword : " ",
    // NO `value:` here — with value set, vis-network switches to value-based
    // scaling (nodes.scaling min/max) and SILENTLY IGNORES `size`, which made
    // the 🎨 node-size slider a no-op. We size nodes ourselves.
    size:nodeSize(n.salience), shape:"dot",
    color:{background:c, border: hot ? "#ffffff" : (dorm ? "#39406b" : c),
           highlight:{background:c, border:"#ffffff"},
           hover:{background:c, border:"#c9d2ff"}},
    borderWidth: hot ? 3 : 1.5,
    opacity: dorm ? 0.55 : 1,
    font:{color: dorm ? "#6b739a" : "#e8ecff",
          size: (12 + Math.sqrt(n.salience/maxSal)*10) * STYLE.fontScale,
          strokeWidth:4, strokeColor:"#0b0e1a"},
    title:undefined, _n:n,
  };
}
function visEdge(l,i){
  const drift = !!l.drift;
  const bridge = !!l._bridge;
  const c = bridge ? "#ffffff" : (drift ? "#e17cff" : (TYPE_COLORS[l.link_type]||"#8b93b8"));
  return {
    id:"e"+i, from:l.source, to:l.target,
    width:((WEIGHT_W[l.weight]||2) + Math.min(l.coact*0.35, 3)) * STYLE.edgeScale,   // Hebbian: co-activation thickens
    color:{color:c, opacity: l.weight==="tangential"?0.35:0.7, highlight:"#ffffff"},
    dashes: bridge ? [3,3] : (drift ? [2,6] : (l.weight==="tangential" ? [4,4] : false)),
    arrows: drift ? {to:{enabled:true, scaleFactor:.5}} : undefined,
    smooth:{type:"continuous", roundness:.35}, _l:l,
  };
}

// `light` (used by timeline scrubbing): skip the insights recompute, which is
// invariant to the time cursor (hubs/salience/dormant come from the full graph,
// not tMax) and only wastes O(links + N·logN) on every drag tick.
function rebuild(light){
  if(allCtxMode){
    if(!allCtxGraph) buildAllCtxGraph();
    G = allCtxGraph;
  } else {
    allCtxGraph = null;
    G = GRAPHS[CTX];
  }
  maxSal = Math.max(1, ...G.nodes.map(n=>n.salience));
  maxTurn = Math.max(1, G.turn_count, ...G.nodes.map(n=>n.turn));
  const tl = document.getElementById("timeline");
  tl.max = maxTurn; if(+tl.value>maxTurn || +tl.value===1) tl.value = maxTurn;
  tMax = +tl.value;
  const kept = G.nodes.filter(n => !domainOff.has(n.domain) && n.turn<=tMax);  // one pass, reused below
  const nset = new Set(kept.map(n=>n.keyword));
  nodesDS = new vis.DataSet(kept.map(visNode));
  edgesDS = new vis.DataSet(G.links
    .map((l,i)=>[l,i]).filter(([l])=> nset.has(l.source)&&nset.has(l.target)
      && !typeOff.has(l.drift?"drift":l.link_type) && (l.created_turn||0)<=tMax)
    .map(([l,i])=>visEdge(l,i)));
  if(network){ network.setData({nodes:nodesDS, edges:edgesDS}); }
  renderStats(); if(!light) renderInsights(); renderTurnLabel();
}

// ---- boot ------------------------------------------------------------------
function boot(){
  const sel = document.getElementById("ctxsel");
  for(const c of Object.keys(GRAPHS)){
    const o=document.createElement("option"); o.value=c; o.textContent="ctx: "+c;
    if(c===CTX) o.selected=true; sel.appendChild(o);
  }
  sel.onchange = e => { CTX=e.target.value; selected=null; hideSide(); domainOff.clear(); typeOff.clear(); renderChips(); rebuild(); network.fit(); };

  G = GRAPHS[CTX];
  rebuildChipsData(); renderChips();
  nodesDS=new vis.DataSet(); edgesDS=new vis.DataSet();
  network = new vis.Network(document.getElementById("net"),
    {nodes:nodesDS, edges:edgesDS},
    { physics:{ solver:"forceAtlas2Based",
        forceAtlas2Based:{gravitationalConstant:STYLE.gravity, springLength:STYLE.springLength,
                          springConstant:0.07, damping:0.5, avoidOverlap:0.6},
        stabilization:{iterations:180, fit:true}},
      interaction:{hover:true, tooltipDelay:120, multiselect:false, navigationButtons:false},
    });
  rebuild(); wire(); setInterval(pulse, 900);
}

function rebuildChipsData(){
  window._domains = [...new Set(Object.values(GRAPHS).flatMap(g=>g.nodes.map(n=>n.domain)))].sort();
  window._types = [...new Set(Object.values(GRAPHS).flatMap(g=>g.links.map(l=>l.drift?"drift":l.link_type)))].sort();
}
function renderChips(){
  const box=document.getElementById("chips"); box.innerHTML="";
  for(const d of window._domains){
    const el=document.createElement("span");
    el.className="chip"+(domainOff.has(d)?" off":"");
    el.innerHTML=`<span class="dot" style="background:${domColor(d)}"></span>${d}`;
    el.onclick=()=>{ domainOff.has(d)?domainOff.delete(d):domainOff.add(d); renderChips(); rebuild(); };
    box.appendChild(el);
  }
  for(const t of window._types){
    const el=document.createElement("span");
    el.className="chip"+(typeOff.has(t)?" off":"");
    const c = t==="drift" ? "#e17cff" : (TYPE_COLORS[t]||"#8b93b8");
    el.innerHTML=`<span class="dot" style="background:${c}; border-radius:2px"></span>${t}`;
    el.onclick=()=>{ typeOff.has(t)?typeOff.delete(t):typeOff.add(t); renderChips(); rebuild(); };
    box.appendChild(el);
  }
  document.getElementById("legend").innerHTML =
    `<span>⚪ border = active in the last 2 turns</span><span>faded = dormant</span>` +
    `<span>edge width = weight + co-activations</span><span style="color:#e17cff">- - → = drift (cross-context)</span>`;
}

function renderStats(){
  const visN=nodesDS.length, visE=edgesDS.length;
  document.getElementById("stats").textContent =
    `${visN}/${G.nodes.length} nodes · ${visE}/${G.links.length} links · turn ${G.turn_count} · ${ENGINE}`;
}
function renderTurnLabel(){
  document.getElementById("turnlbl").textContent = tMax>=maxTurn ? `now (turn ${maxTurn})` : `turn ${tMax} / ${maxTurn}`;
}

// ---- insights ---------------------------------------------------------------
function renderInsights(){
  const deg={};
  for(const l of G.links){ deg[l.source]=(deg[l.source]||0)+1; deg[l.target]=(deg[l.target]||0)+1; }
  const item=(k,v)=>`<div class="item" onclick="focusNode('${k.replace(/'/g,"\\'")}')"><span class="k">${k}</span><span class="v">${v}</span></div>`;
  const hubs=Object.entries(deg).sort((a,b)=>b[1]-a[1]).slice(0,6);
  const sal=[...G.nodes].sort((a,b)=>b.salience-a.salience).slice(0,6);
  const dorm=G.nodes.filter(isDormant).sort((a,b)=>b.salience-a.salience).slice(0,6);
  const syn=[...G.links].filter(l=>l.coact>0).sort((a,b)=>b.coact-a.coact).slice(0,5);
  const drift=G.links.filter(l=>l.drift).slice(0,5);
  let h="";
  h+="<h3>🕸 Hubs (most connected)</h3>"+(hubs.map(([k,v])=>item(k,v+" links")).join("")||"<div class='item'><span class='v'>—</span></div>");
  h+="<h3>⭐ Most salient</h3>"+sal.map(n=>item(n.keyword,"sal "+n.salience)).join("");
  if(dorm.length) h+="<h3>💤 Dormant (worth revisiting)</h3>"+dorm.map(n=>item(n.keyword,(maxTurn-n.turn)+" turns")).join("");
  if(syn.length) h+="<h3>⚡ Strongest synapses</h3>"+syn.map(l=>item(l.source+" ↔ "+l.target,"×"+l.coact)).join("");
  if(drift.length) h+="<h3>🌉 Cross-context bridges</h3>"+drift.map(l=>item(l.source+" → "+l.target,"@"+l.drift)).join("");
  document.getElementById("insights").innerHTML=h;
}

// ---- side panel ---------------------------------------------------------------
function showNode(id){
  const n = G.nodes.find(x=>x.keyword===id); if(!n) return;
  toggleStyle(false);
  selected=id;
  const deg = G.links.filter(l=>l.source===id||l.target===id).length;
  const ctxLabel = n._ctx ? `<span style="color:${n._ctxColor}">●</span> ${n._ctx} · ` : "";
  let h=`<h2>${id.includes("::")?id.split("::")[1]:id}</h2><div class="sub">
    <span class="dot" style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${domColor(n.domain)}"></span>
    ${ctxLabel}${n.domain} · ${n.sentiment} · ${deg} links</div>`;
  h+=`<div class="row"><span class="lbl">salience ${n.salience}</span>
      <div class="salbar"><div style="width:${Math.round(100*n.salience/maxSal)}%"></div></div></div>`;
  h+=`<div class="row"><span class="lbl">last seen</span> turn ${n.turn}`
     +((maxTurn-n.turn)>=6?` <i style="color:#8b93b8">(dormant ${maxTurn-n.turn} turns)</i>`:"")+`</div>`;
  if(n.topic) h+=`<div class="row"><span class="lbl">topic</span> ${n.topic}</div>`;
  if(n.episodes && n.episodes.length){
    h+=`<div class="row"><span class="lbl">facts (episodes)</span>`+
       n.episodes.map(e=>`<div class="fact">${e}</div>`).join("")+`</div>`;
  }
  if(n.entities?.length) h+=`<div class="row"><span class="lbl">entities</span><br>`+n.entities.map(t=>`<span class="tag">${t}</span>`).join("")+`</div>`;
  if(n.tags?.length) h+=`<div class="row"><span class="lbl">tags</span><br>`+n.tags.map(t=>`<span class="tag">${t}</span>`).join("")+`</div>`;
  const nb = G.links.filter(l=>l.source===id||l.target===id).slice(0,10);
  if(nb.length){
    h+=`<div class="row"><span class="lbl">connections</span>`+
      nb.map(l=>{const other=l.source===id?l.target:l.source;
        return `<div class="item" onclick="focusNode('${other.replace(/'/g,"\\'")}')">
          <span class="k">${other}</span><span class="v">${l.drift?"drift":l.link_type}</span></div>`;}).join("")+`</div>`;
  }
  const el=document.getElementById("side"); el.innerHTML=h; el.style.display="block";
}
function showEdge(eid){
  const e=edgesDS.get(eid); if(!e) return; const l=e._l;
  toggleStyle(false);
  let h=`<h2>${l.source} → ${l.target}</h2><div class="sub">${l.drift?("drift → "+l.drift):l.link_type} · ${l.weight}`+
        (l.coact?` · co-activated ×${l.coact}`:"")+`</div>`;
  if(l.rationale) h+=`<div class="fact">${l.rationale}</div>`;
  h+=`<div class="row"><span class="lbl">born</span> turn ${l.created_turn} · <span class="lbl">last active</span> turn ${l.last_active_turn}</div>`;
  const el=document.getElementById("side"); el.innerHTML=h; el.style.display="block";
}
function hideSide(){ document.getElementById("side").style.display="none"; selected=null; }
function focusNode(id){
  if(!nodesDS.get(id)){ toast(`"${id}" is filtered out right now`); return; }
  network.focus(id,{scale:1.25, animation:{duration:600, easingFunction:"easeInOutQuad"}});
  network.selectNodes([id]); showNode(id); dimOthers(id);
}
function toast(msg){ const t=document.getElementById("toast"); t.textContent=msg; t.style.display="block";
  clearTimeout(t._h); t._h=setTimeout(()=>t.style.display="none", 1800); }

// ---- neighborhood highlight -----------------------------------------------
function dimOthers(id){
  const keep=new Set([id]);
  for(const e of edgesDS.get()){ if(e.from===id) keep.add(e.to); if(e.to===id) keep.add(e.from); }
  nodesDS.update(nodesDS.get().map(n=>({id:n.id, opacity: keep.has(n.id)?1:0.12,
    font:{...n.font, color: keep.has(n.id)?"#e8ecff":"rgba(139,147,184,.25)"}})));
}
function undim(){ rebuildNodesOnly(); }
function rebuildNodesOnly(){
  for(const n of nodesDS.get()){ const src=n._n; nodesDS.update(visNode(src)); }
}

// ---- heartbeat pulse ---------------------------------------------------------
function pulse(){
  if(!pulseOn || selected || document.hidden) return;   // don't animate a hidden tab
  pulseTick^=1;
  const top=[...G.nodes].sort((a,b)=>b.salience-a.salience).slice(0,3);
  for(const n of top){
    if(!nodesDS.get(n.keyword)) continue;
    nodesDS.update({id:n.keyword, borderWidth: pulseTick?5:1.5,
      color:{background:domColor(n.domain), border: pulseTick?"#ffffff":domColor(n.domain)}});
  }
}

// ---- replay -----------------------------------------------------------------
let playing=null;
let replaySpeed = 450;
let nodesByTurn={};
function precomputeTimeline(){
  nodesByTurn={};
  for(const n of G.nodes){
    if(!nodesByTurn[n.turn]) nodesByTurn[n.turn]=[];
    nodesByTurn[n.turn].push(n);
  }
}
function togglePlay(){
  const btn=document.getElementById("btn-play"), tl=document.getElementById("timeline");
  if(playing){ clearInterval(playing); playing=null; btn.textContent="▶ Replay"; return; }
  nodesDS.clear(); edgesDS.clear();
  if(network) network.setData({nodes:nodesDS, edges:edgesDS});
  precomputeTimeline();
  const addedEdges=new Set();
  let cur=1; tMax=1; tl.value=1;
  btn.textContent="⏸ Stop";
  renderTurnLabel();
  function step(){
    cur++; tMax=cur; tl.value=cur;
    const fresh=(nodesByTurn[cur]||[]).filter(n=>!domainOff.has(n.domain));
    if(fresh.length){
      nodesDS.add(fresh.map(n=>{
        const v=visNode(n);
        v.borderWidth=5; v.color={...v.color, border:"#ffffff"};
        return v;
      }));
      setTimeout(()=>{
        for(const n of fresh){
          if(!nodesDS.get(n.keyword)) continue;
          const hot=isHot(n);
          nodesDS.update({id:n.keyword, borderWidth:hot?3:1.5,
            color:{background:domColor(n.domain), border:hot?"#ffffff":domColor(n.domain),
                   highlight:{background:domColor(n.domain),border:"#ffffff"},
                   hover:{background:domColor(n.domain),border:"#c9d2ff"}}});
        }
      }, 350);
    }
    // Check ALL edges: add any whose endpoints exist now and created_turn<=cur
    const nset=new Set(nodesDS.getIds());
    const newEdges=[];
    for(let i=0;i<G.links.length;i++){
      if(addedEdges.has(i)) continue;
      const l=G.links[i];
      if(nset.has(l.source)&&nset.has(l.target)&&(l.created_turn||0)<=cur
         &&!typeOff.has(l.drift?"drift":l.link_type)){
        newEdges.push(visEdge(l,i));
        addedEdges.add(i);
      }
    }
    if(newEdges.length) edgesDS.add(newEdges);
    renderStats(); renderInsights(); renderTurnLabel();
    if(cur>=maxTurn){ clearInterval(playing); playing=null; btn.textContent="▶ Replay"; }
  }
  step();
  playing=setInterval(step, replaySpeed);
}

// ---- all-contexts view ------------------------------------------------------
let allCtxMode = false;
let allCtxGraph = null;   // merged {nodes, links, turn_count}
function toggleAllCtx(){
  allCtxMode = !allCtxMode;
  const btn = document.getElementById("btn-allctx");
  const sel = document.getElementById("ctxsel");
  btn.classList.toggle("on", allCtxMode);
  sel.disabled = allCtxMode;
  selected=null; hideSide();
  rebuild();
  network.fit();
}
function buildAllCtxGraph(){
  const merged = {nodes:[], links:[], turn_count:0};
  const ctxPalette = ["#7c8cff","#4be1a0","#ffb84d","#ff6b9d","#b07cff","#53c7ff"];
  const ctxList = Object.keys(GRAPHS);
  ctxList.forEach((c,i)=>{
    const gd = GRAPHS[c];
    const pfx = c.replace(/[^a-zA-Z0-9]/g,"_");
    const cColor = ctxPalette[i % ctxPalette.length];
    merged.turn_count = Math.max(merged.turn_count, gd.turn_count);
    for(const n of gd.nodes){
      merged.nodes.push({...n, keyword: pfx+"::"+n.keyword, _ctx:c, _ctxColor:cColor});
    }
    for(const l of gd.links){
      merged.links.push({...l,
        source: pfx+"::"+l.source, target: pfx+"::"+l.target,
        _ctx:c, _edgeId: pfx+"::"+l.source+"→"+pfx+"::"+l.target,
      });
    }
  });
  // Cross-context bridges: same keyword in different contexts
  const kwCtx = {};
  for(const n of merged.nodes){
    const kw = n.keyword.split("::")[1];
    if(!kwCtx[kw]) kwCtx[kw]=[];
    kwCtx[kw].push(n.keyword);
  }
  let bi = 0;
  for(const [kw, ids] of Object.entries(kwCtx)){
    if(ids.length < 2) continue;
    for(let i=1;i<ids.length;i++){
      merged.links.push({
        source:ids[0], target:ids[i], link_type:"bridge", weight:"tangential",
        rationale:"shared concept across contexts", created_turn:1, last_active_turn:merged.turn_count,
        coact:0, drift:null, _bridge:true, _edgeId:"bridge::"+bi++,
      });
    }
  }
  allCtxGraph = merged;
}

// ---- style editor (🎨, Obsidian-like) ----------------------------------------
function renderStylePanel(){
  const sl=(id,label,min,max,step,val)=>`
    <div class="sl"><label>${label}</label>
      <input type="range" id="st-${id}" min="${min}" max="${max}" step="${step}" value="${val}">
      <span class="val" id="stv-${id}">${val}</span></div>`;
  let h="<h3>🎛 Display</h3>";
  h+=sl("nodeScale","node size",0.4,2.5,0.05,STYLE.nodeScale);
  h+=sl("edgeScale","link thickness",0.3,3,0.05,STYLE.edgeScale);
  h+=sl("fontScale","label size",0.4,2.2,0.05,STYLE.fontScale);
  h+="<h3>🧲 Forces</h3>";
  h+=sl("springLength","link distance",40,320,5,STYLE.springLength);
  h+=sl("gravity","repel force",-160,-8,2,STYLE.gravity);
  h+="<h3>🎨 Domain colors</h3>";
  for(const d of window._domains){
    h+=`<div class="colrow"><input type="color" id="stc-${d}" value="${toHex(styledDomColor(d))}">
        <span>${d}</span></div>`;
  }
  h+=`<div class="foot"><button id="st-reset">Reset defaults</button>
      <button id="st-close">Close</button></div>
      <div style="color:var(--tx-dim); font-size:11px; margin-top:8px">
      Saved automatically in this browser (localStorage).</div>`;
  const p=document.getElementById("stylepanel"); p.innerHTML=h;
  for(const id of ["nodeScale","edgeScale","fontScale"]){
    const el=document.getElementById("st-"+id);
    el.oninput=()=>{ STYLE[id]=+el.value; document.getElementById("stv-"+id).textContent=el.value;
                     saveStyle(); rebuildNodesEdges(); };
  }
  for(const id of ["springLength","gravity"]){
    const el=document.getElementById("st-"+id);
    el.oninput=()=>{ STYLE[id]=+el.value; document.getElementById("stv-"+id).textContent=el.value;
                     saveStyle(); applyPhysics(); };
  }
  for(const d of window._domains){
    const el=document.getElementById("stc-"+d);
    el.oninput=()=>{ STYLE.colors[d]=el.value; saveStyle(); rebuildNodesEdges(); renderChips(); renderInsights(); };
  }
  document.getElementById("st-reset").onclick=()=>{
    STYLE={...STYLE_DEFAULTS, colors:{}}; saveStyle();
    renderStylePanel(); rebuildNodesEdges(); applyPhysics(); renderChips(); };
  document.getElementById("st-close").onclick=()=>toggleStyle(false);
}
function rebuildNodesEdges(){
  rebuildNodesOnly();
  for(const e of edgesDS.get()) edgesDS.update(visEdge(e._l, e.id.slice(1)));
}
function applyPhysics(){
  network.setOptions({physics:{forceAtlas2Based:{
    gravitationalConstant:STYLE.gravity, springLength:STYLE.springLength,
    springConstant:0.07, damping:0.5, avoidOverlap:0.6}}});
}
function toHex(c){
  if(c.startsWith("#")) return c;
  const m=c.match(/hsl\((\d+)/); if(!m) return "#8b93b8";
  const h=+m[1]/360, f=(n,k=(n+h*12)%12)=>0.62-0.35*Math.max(-1,Math.min(k-3,9-k,1));
  const to=x=>Math.round(x*255).toString(16).padStart(2,"0");
  return "#"+to(f(0))+to(f(8))+to(f(4));
}
function toggleStyle(force){
  const p=document.getElementById("stylepanel"), b=document.getElementById("btn-style");
  const show = force!==undefined ? force : p.style.display!=="block";
  if(show){ hideSide(); renderStylePanel(); }
  p.style.display=show?"block":"none"; b.classList.toggle("on", show);
}

// ---- wiring -----------------------------------------------------------------
function wire(){
  document.getElementById("btn-style").onclick=()=>toggleStyle();
  network.on("click", p=>{
    if(p.nodes.length){ showNode(p.nodes[0]); dimOthers(p.nodes[0]); }
    else if(p.edges.length){ showEdge(p.edges[0]); }
    else { hideSide(); undim(); }
  });
  network.on("doubleClick", p=>{ if(p.nodes.length) focusNode(p.nodes[0]); });
  document.getElementById("search").addEventListener("keydown", e=>{
    if(e.key!=="Enter") return;
    const q=e.target.value.trim().toLowerCase(); if(!q) return;
    const hit=G.nodes.find(n=>n.keyword.toLowerCase()===q)
          || G.nodes.find(n=>n.keyword.toLowerCase().includes(q));
    hit ? focusNode(hit.keyword) : toast("no concept matches "+JSON.stringify(q));
  });
  const tl=document.getElementById("timeline");
  // Scrubbing fires 'input' on every pixel of drag. Coalesce those into at most
  // one rebuild per animation frame (was a synchronous full DataSet rebuild +
  // physics restart PER pixel — the main source of timeline jank). The 'change'
  // event (fired once on release) does a full rebuild to refresh the panels.
  let _tlRaf=0;
  tl.addEventListener("input", ()=>{
    if(_tlRaf) return;
    _tlRaf=requestAnimationFrame(()=>{ _tlRaf=0; rebuild(true); });
  });
  tl.addEventListener("change", ()=>{ if(_tlRaf){ cancelAnimationFrame(_tlRaf); _tlRaf=0; } rebuild(); });
  bindToggle("btn-physics", on=>network.setOptions({physics:{enabled:on}}));
  bindToggle("btn-labels", on=>{ labelsOn=on; rebuildNodesOnly(); });
  bindToggle("btn-pulse", on=>{ pulseOn=on; if(!on) rebuildNodesOnly(); });
  bindToggle("btn-insights", on=>{ document.getElementById("insights").style.display=on?"block":"none"; });
  document.getElementById("btn-play").onclick=togglePlay;
  document.getElementById("btn-allctx").onclick=toggleAllCtx;
  // Speed cycle: 450ms (1×) → 900ms (0.5×) → 225ms (2×)
  const speeds = [{label:"1×", ms:450},{label:"0.5×", ms:900},{label:"2×", ms:225}];
  let speedIdx = 0;
  document.getElementById("btn-speed").onclick=()=>{
    speedIdx = (speedIdx+1) % speeds.length;
    replaySpeed = speeds[speedIdx].ms;
    document.getElementById("btn-speed").textContent = speeds[speedIdx].label;
    if(playing){ clearInterval(playing); playing=null; togglePlay(); }
  };
}
function bindToggle(id, fn){
  const b=document.getElementById(id);
  b.onclick=()=>{ b.classList.toggle("on"); fn(b.classList.contains("on")); };
}

boot();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--context", default=None, help="context to open first (default: 'default')")
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    print("\n  Neuron Graph Visualizer — generator (v2, cloud-aware)\n")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  Engine: {_ENGINE}{' (cloud)' if _REMOTE else ''}")
    print(f"  Output: {OUTPUT_DIR}")

    contexts = list_contexts()
    print(f"  Contexts: {', '.join(contexts)}")
    graphs = {}
    for c in contexts:
        gd = export_graph(c)
        if gd["nodes"] or c == "default":
            graphs[c] = gd
            print(f"    [OK] {c}: {len(gd['nodes'])} nodes, {len(gd['links'])} links, turn {gd['turn_count']}")
    if not graphs:
        print("  [!] no graphs found — nothing to visualize."); sys.exit(1)

    active = args.context if args.context in graphs else \
             ("default" if "default" in graphs else next(iter(graphs)))
    vis_code = get_vis_network()
    html = generate_html(graphs, active, vis_code)
    html_path = OUTPUT_DIR / "neuron-graph.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"\n  [SUCCESS] {html_path}")

    if not args.no_open:
        try:
            if os.name == "nt":
                os.startfile(str(html_path))
            else:
                import subprocess
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(html_path)])
        except Exception as e:
            print(f"  [!] could not auto-open: {e}\n      Open manually: {html_path}")


if __name__ == "__main__":
    main()
