#!/usr/bin/env python3
"""
generate_graph_html.py — Genera neuron-graph.html autocontenuto + config JS.

Trova automaticamente la directory di installazione di Neuron, legge il grafo
dal database SQLite, e genera un HTML interattivo con vis-network embedded.

Usage:
    python scripts/generate_graph_html.py
"""

import json
import os
import sqlite3
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
# Graph export from SQLite
# ---------------------------------------------------------------------------
def export_graph(context: str = "default") -> dict:
    """Export graph data from Neuron's SQLite database."""
    db_path = GRAPHS_DIR / f"graph_{context}.db"
    if not db_path.exists():
        print(f"  [!] Database not found: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    try:
        # Meta
        meta = {}
        try:
            cursor = conn.execute("SELECT key, value FROM meta")
            meta = dict(cursor.fetchall())
        except Exception:
            pass

        # Nodes
        nodes = []
        cols_info = {c[1]: c[2] for c in conn.execute("PRAGMA table_info(nodes)").fetchall()}
        has_entities = "entities" in cols_info
        for row in conn.execute(
            "SELECT keyword, turn, topic, domain, sentiment, salience, "
            "COALESCE(entities,'[]'), COALESCE(tags,'[]'), COALESCE(refs,'[]') "
            "FROM nodes ORDER BY id"
        ):
            entities = json.loads(row[6]) if row[6] else []
            tags = json.loads(row[7]) if row[7] else []
            refs = json.loads(row[8]) if row[8] else []
            nodes.append({
                "keyword": row[0],
                "turn": row[1],
                "topic": row[2] or "",
                "domain": row[3] or "general",
                "sentiment": row[4] or "neutral",
                "salience": row[5] or 1,
                "entities": entities,
                "tags": tags,
                "references": refs,
            })

        # Links
        links = []
        for row in conn.execute(
            "SELECT source, target, link_type, weight, rationale, "
            "created_turn, last_active_turn, inactive_turns "
            "FROM links ORDER BY id"
        ):
            links.append({
                "source": row[0],
                "target": row[1],
                "link_type": row[2] or "deepening",
                "weight": row[3] or "medium",
                "rationale": row[4] or "",
                "created_turn": row[5] or 0,
                "last_active_turn": row[6] or 0,
                "inactive_turns": row[7] or 0,
            })

        return {
            "session_id": meta.get("session_id", context),
            "turn_count": int(meta.get("turn_count", "0")),
            "nodes": nodes,
            "links": links,
        }
    finally:
        conn.close()

# ---------------------------------------------------------------------------
# Download vis-network (cached)
# ---------------------------------------------------------------------------
VIS_NETWORK_URL = "https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"
VIS_NETWORK_FILE = OUTPUT_DIR / "vis-network.min.js"

def download_vis_network() -> str:
    """Download vis-network.min.js (cached locally)."""
    if VIS_NETWORK_FILE.exists():
        return VIS_NETWORK_FILE.read_text(encoding="utf-8")

    print(f"  Downloading vis-network from CDN (one-time)...")
    try:
        req = urllib.request.Request(VIS_NETWORK_URL, headers={"User-Agent": "NeuronGraph/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read().decode("utf-8")
        VIS_NETWORK_FILE.write_text(data, encoding="utf-8")
        print(f"  [OK] vis-network cached: {VIS_NETWORK_FILE}")
        return data
    except Exception as e:
        print(f"  [X] Download failed: {e}")
        print(f"      Download manually from {VIS_NETWORK_URL}")
        print(f"      Save as: {VIS_NETWORK_FILE}")
        sys.exit(1)

# ---------------------------------------------------------------------------
# Config JS generation
# ---------------------------------------------------------------------------
CONFIG_JS = """\
// ============================================================
// NEURON GRAPH — CONFIGURAZIONE COLORI
// ============================================================
// Modifica i valori qui sotto. Ogni riga ha un placeholder
// che spiega cosa controlla. Salva e ricarica il browser.
//
// SCALA IMPORTANZA:
//   Caldo = nodi/legami forti, alta importanza
//   Neutro = media importanza
//   Freddo = nodi/legami deboli, relazioni indirette
// ============================================================

window.NEURON_GRAPH_CONFIG = {

    // --- COLORI NODI (salienza) ---
    // alta salienza (8+)    -> caldo
    nodeHigh:     "#FF6B35",  // arancione vivo

    // media salienza (4-7)  -> neutro
    nodeMedium:   "#CE93D8",  // viola medio

    // bassa salienza (1-3)  -> freddo
    nodeLow:      "#4FC3F7",  // azzurro

    // --- COLORI LEGAMI (weight) ---
    // strong  -> caldo
    linkStrong:   "#FF4444",  // rosso

    // medium -> neutro
    linkMedium:   "#FFB74D",  // ambra

    // tangential -> freddo
    linkTangential: "#4DD0E1", // ciano

    // --- TEMA ---
    background:   "#0d1117",  // sfondo scuro
    panelBg:      "#161b22",  // pannello laterale
    text:         "#c9d1d9",  // testo principale
    textMuted:    "#8b949e",  // testo secondario
    border:       "#30363d",  // bordi pannelli

    // --- LAYOUT ---
    physics:      true,       // true = simulazione fisica, false = statico
    iterations:   2000,       // iterazioni physics
    gravity:      -8000,      // gravita repulsiva tra nodi (negativo = respinge)
};
"""

# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------
def generate_html(graph_data: dict, vis_network_code: str) -> str:
    """Generate the full HTML with embedded vis-network and graph data."""
    graph_json = json.dumps(graph_data, ensure_ascii=False, indent=2)

    return f"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Neuron Graph Visualizer</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    background: #0d1117;
    color: #c9d1d9;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
    overflow: hidden;
    height: 100vh;
}}

/* Header */
.header {{
    background: #161b22;
    border-bottom: 1px solid #30363d;
    padding: 8px 16px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 48px;
    z-index: 100;
}}
.header h1 {{
    font-size: 16px;
    font-weight: 600;
    color: #58a6ff;
}}
.header .stats {{
    font-size: 12px;
    color: #8b949e;
}}
.header .controls {{
    display: flex;
    gap: 8px;
}}
.header .controls button {{
    background: #21262d;
    border: 1px solid #30363d;
    color: #c9d1d9;
    padding: 4px 12px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 12px;
}}
.header .controls button:hover {{
    background: #30363d;
    border-color: #58a6ff;
}}

/* Main layout */
.main {{
    display: flex;
    height: calc(100vh - 48px);
}}

/* Graph container */
#graph-container {{
    flex: 1;
    position: relative;
    width: 100%;
    height: 100%;
}}

/* Sidebar */
.sidebar {{
    width: 360px;
    background: #161b22;
    border-left: 1px solid #30363d;
    overflow-y: auto;
    display: none;
}}
.sidebar.active {{
    display: block;
}}
.sidebar-header {{
    padding: 12px 16px;
    border-bottom: 1px solid #30363d;
    display: flex;
    justify-content: space-between;
    align-items: center;
}}
.sidebar-header h2 {{
    font-size: 14px;
    color: #58a6ff;
}}
.sidebar-close {{
    background: none;
    border: none;
    color: #8b949e;
    cursor: pointer;
    font-size: 18px;
}}
.sidebar-close:hover {{ color: #c9d1d9; }}
.sidebar-content {{
    padding: 16px;
}}
.detail-section {{
    margin-bottom: 16px;
}}
.detail-section h3 {{
    font-size: 11px;
    text-transform: uppercase;
    color: #8b949e;
    margin-bottom: 8px;
    letter-spacing: 0.5px;
}}
.detail-row {{
    display: flex;
    justify-content: space-between;
    padding: 4px 0;
    font-size: 13px;
}}
.detail-row .label {{
    color: #8b949e;
}}
.detail-row .value {{
    color: #c9d1d9;
    text-align: right;
    max-width: 200px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}}
.link-item {{
    background: #0d1117;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 8px 12px;
    margin-bottom: 6px;
    font-size: 12px;
}}
.link-item .link-header {{
    display: flex;
    justify-content: space-between;
    margin-bottom: 4px;
}}
.link-item .link-type {{
    font-weight: 600;
}}
.link-item .link-weight {{
    font-size: 11px;
    padding: 1px 6px;
    border-radius: 10px;
}}
.link-item .link-rationale {{
    color: #8b949e;
    font-style: italic;
}}
.weight-strong {{ background: #FF444422; color: #FF4444; }}
.weight-medium {{ background: #FFB74D22; color: #FFB74D; }}
.weight-tangential {{ background: #4DD0E122; color: #4DD0E1; }}

/* Bottom panel: filters + legend */
.bottom-panel {{
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    background: #161b22ee;
    border-top: 1px solid #30363d;
    padding: 8px 16px;
    display: flex;
    gap: 24px;
    align-items: center;
    font-size: 12px;
    z-index: 50;
    backdrop-filter: blur(8px);
}}
.filter-group {{
    display: flex;
    gap: 12px;
    align-items: center;
    flex-wrap: wrap;
}}
.filter-item {{
    display: flex;
    align-items: center;
    gap: 4px;
}}
.filter-item input[type="checkbox"] {{
    accent-color: #58a6ff;
}}
.legend {{
    display: flex;
    gap: 16px;
    margin-left: auto;
    color: #8b949e;
}}
.legend-item {{
    display: flex;
    align-items: center;
    gap: 4px;
}}
.legend-line {{
    width: 24px;
    height: 2px;
    border-radius: 1px;
}}
.legend-line.strong {{ background: #FF4444; height: 3px; }}
.legend-line.medium {{ background: #FFB74D; }}
.legend-line.tangential {{ background: #4DD0E1; height: 1px; border-top: 1px dashed #4DD0E1; background: none; }}

/* Tooltip */
.tooltip {{
    position: absolute;
    background: #1c2128;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 12px;
    pointer-events: none;
    z-index: 200;
    max-width: 300px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.4);
    display: none;
}}
.tooltip .tt-keyword {{
    font-weight: 600;
    color: #58a6ff;
    margin-bottom: 4px;
}}
.tooltip .tt-row {{
    display: flex;
    justify-content: space-between;
    gap: 12px;
    padding: 1px 0;
}}
.tooltip .tt-label {{
    color: #8b949e;
}}
.tooltip .tt-value {{
    color: #c9d1d9;
}}
</style>
</head>
<body>

<div class="header">
    <h1>Neuron Graph Visualizer</h1>
    <span class="stats" id="stats"></span>
    <div class="controls">
        <button onclick="fitGraph()">Fit</button>
        <button onclick="togglePhysics()">Physics</button>
    </div>
</div>

<div class="main">
    <div id="graph-container"></div>
    <div class="sidebar" id="sidebar">
        <div class="sidebar-header">
            <h2 id="sidebar-title">Dettaglio</h2>
            <button class="sidebar-close" onclick="closeSidebar()">&times;</button>
        </div>
        <div class="sidebar-content" id="sidebar-content"></div>
    </div>
</div>

<div class="bottom-panel">
    <div class="filter-group" id="filters"></div>
    <div class="legend">
        <span class="legend-item"><span class="legend-line strong"></span> strong</span>
        <span class="legend-item"><span class="legend-line medium"></span> medium</span>
        <span class="legend-item"><span class="legend-line tangential"></span> tangential</span>
    </div>
</div>

<div class="tooltip" id="tooltip"></div>

<script src="./vis-network.min.js"></script>

<script>
// ============================================================
// CONFIG
// ============================================================
</script>
<script src="./neuron-graph-config.js"></script>

<script>
// ============================================================
// GRAPH DATA (embedded)
// ============================================================
const GRAPH_DATA = {graph_json};

// ============================================================
// COLOR HELPERS
// ============================================================
function getConfig() {{
    const def = {{
        nodeHigh: "#FF6B35", nodeMedium: "#CE93D8", nodeLow: "#4FC3F7",
        linkStrong: "#FF4444", linkMedium: "#FFB74D", linkTangential: "#4DD0E1",
        background: "#0d1117", panelBg: "#161b22", text: "#c9d1d9",
        textMuted: "#8b949e", border: "#30363d",
        physics: true, iterations: 2000, gravity: -8000
    }};
    if (typeof window.NEURON_GRAPH_CONFIG !== 'undefined') {{
        return Object.assign(def, window.NEURON_GRAPH_CONFIG);
    }}
    return def;
}}

const CFG = getConfig();

function nodeColor(salience) {{
    if (salience >= 8) return CFG.nodeHigh;
    if (salience >= 4) return CFG.nodeMedium;
    return CFG.nodeLow;
}}

function linkColor(weight) {{
    if (weight === "strong") return CFG.linkStrong;
    if (weight === "tangential") return CFG.linkTangential;
    return CFG.linkMedium;
}}

function linkWidth(weight) {{
    if (weight === "strong") return 3;
    if (weight === "tangential") return 1;
    return 2;
}}

function linkDash(weight) {{
    if (weight === "tangential") return [5, 5];
    return undefined;
}}

// ============================================================
// BUILD GRAPH
// ============================================================
const nodeMap = {{}};
const nodes = new vis.DataSet(GRAPH_DATA.nodes.map(n => {{
    const id = n.keyword;
    nodeMap[id] = n;
    const sal = n.salience || 1;
    const size = Math.max(8, Math.min(40, sal * 4));
    return {{
        id,
        label: id,
        title: id,
        size,
        color: {{
            background: nodeColor(sal),
            border: nodeColor(sal),
            highlight: {{ background: nodeColor(sal), border: "#ffffff" }},
            hover: {{ background: nodeColor(sal), border: "#58a6ff" }}
        }},
        font: {{ color: CFG.text, size: Math.max(10, Math.min(14, sal + 6)) }},
        borderWidth: 2,
        shadow: true,
        domain: n.domain,
        salience: sal
    }};
}}));

const edges = new vis.DataSet(GRAPH_DATA.links.map(l => ({{
    id: `${{l.source}}->${{l.target}}`,
    from: l.source,
    to: l.target,
    label: l.link_type,
    color: {{ color: linkColor(l.weight), highlight: "#FF0000", opacity: 0.6 }},
    width: linkWidth(l.weight),
    dashes: linkDash(l.weight),
    arrows: {{ to: {{ enabled: true, scaleFactor: 0.5 }} }},
    font: {{ color: CFG.textMuted, size: 9, strokeWidth: 0 }},
    smooth: {{ type: "continuous", roundness: 0.2 }},
    link_type: l.link_type,
    weight: l.weight,
    rationale: l.rationale,
    created_turn: l.created_turn,
    inactive_turns: l.inactive_turns
}})));

// ============================================================
// NETWORK
// ============================================================
const container = document.getElementById('graph-container');
const network = new vis.Network(container, {{ nodes, edges }}, {{
    physics: {{
        enabled: CFG.physics,
        solver: 'barnesHut',
        barnesHut: {{
            gravitationalConstant: CFG.gravity,
            centralGravity: 0.1,
            springLength: 150,
            springConstant: 0.02,
            damping: 0.4
        }},
        stabilization: {{ iterations: CFG.iterations }}
    }},
    interaction: {{
        hover: true,
        tooltipDelay: 100,
        zoomView: true,
        dragView: true,
        navigationButtons: false,
        keyboard: false
    }},
    edges: {{
        smooth: {{ type: "continuous" }}
    }}
}});

// Stats
document.getElementById('stats').textContent =
    `${{GRAPH_DATA.nodes.length}} nodes  |  ${{GRAPH_DATA.links.length}} links  |  turn ${{GRAPH_DATA.turn_count}}`;

// ============================================================
// TOOLTIP
// ============================================================
const tooltip = document.getElementById('tooltip');

network.on("hoverNode", function(params) {{
    const nodeId = params.node;
    const n = nodeMap[nodeId];
    if (!n) return;
    let html = `<div class="tt-keyword">${{n.keyword}}</div>`;
    html += `<div class="tt-row"><span class="tt-label">Domain</span><span class="tt-value">${{n.domain}}</span></div>`;
    html += `<div class="tt-row"><span class="tt-label">Salience</span><span class="tt-value">${{n.salience}}</span></div>`;
    html += `<div class="tt-row"><span class="tt-label">Turn</span><span class="tt-value">${{n.turn}}</span></div>`;
    if (n.topic) html += `<div class="tt-row"><span class="tt-label">Topic</span><span class="tt-value">${{n.topic}}</span></div>`;
    if (n.entities && n.entities.length) html += `<div class="tt-row"><span class="tt-label">Entities</span><span class="tt-value">${{n.entities.join(', ')}}</span></div>`;
    if (n.tags && n.tags.length) html += `<div class="tt-row"><span class="tt-label">Tags</span><span class="tt-value">${{n.tags.join(', ')}}</span></div>`;
    tooltip.innerHTML = html;
    tooltip.style.display = 'block';
}});

network.on("hoverEdge", function(params) {{
    const edgeId = params.edge;
    const e = edges.get(edgeId);
    if (!e) return;
    let html = `<div class="tt-keyword">${{e.from}} &rarr; ${{e.to}}</div>`;
    html += `<div class="tt-row"><span class="tt-label">Type</span><span class="tt-value">${{e.link_type}}</span></div>`;
    html += `<div class="tt-row"><span class="tt-label">Weight</span><span class="tt-value">${{e.weight}}</span></div>`;
    if (e.rationale) html += `<div class="tt-row"><span class="tt-label">Rationale</span><span class="tt-value">${{e.rationale}}</span></div>`;
    html += `<div class="tt-row"><span class="tt-label">Created</span><span class="tt-value">turn ${{e.created_turn}}</span></div>`;
    if (e.inactive_turns) html += `<div class="tt-row"><span class="tt-label">Inactive</span><span class="tt-value">${{e.inactive_turns}} turns</span></div>`;
    tooltip.innerHTML = html;
    tooltip.style.display = 'block';
}});

network.on("blurNode", function() {{ tooltip.style.display = 'none'; }});
network.on("blurEdge", function() {{ tooltip.style.display = 'none'; }});

document.addEventListener("mousemove", function(e) {{
    if (tooltip.style.display === 'block') {{
        tooltip.style.left = (e.clientX + 12) + 'px';
        tooltip.style.top = (e.clientY + 12) + 'px';
    }}
}});

// ============================================================
// CLICK NODE -> SIDEBAR
// ============================================================
network.on("click", function(params) {{
    if (params.nodes.length > 0) {{
        const nodeId = params.nodes[0];
        showNodeDetail(nodeId);
    }} else if (params.edges.length > 0) {{
        const edgeId = params.edges[0];
        showEdgeDetail(edgeId);
    }} else {{
        closeSidebar();
        resetHighlight();
    }}
}});

function showNodeDetail(nodeId) {{
    const n = nodeMap[nodeId];
    if (!n) return;

    document.getElementById('sidebar-title').textContent = n.keyword;
    const content = document.getElementById('sidebar-content');

    let html = '';
    // Info section
    html += '<div class="detail-section">';
    html += '<h3>Info</h3>';
    html += `<div class="detail-row"><span class="label">Domain</span><span class="value">${{n.domain}}</span></div>`;
    html += `<div class="detail-row"><span class="label">Salience</span><span class="value">${{n.salience}}</span></div>`;
    html += `<div class="detail-row"><span class="label">Turn</span><span class="value">${{n.turn}}</span></div>`;
    html += `<div class="detail-row"><span class="label">Sentiment</span><span class="value">${{n.sentiment}}</span></div>`;
    if (n.topic) html += `<div class="detail-row"><span class="label">Topic</span><span class="value">${{n.topic}}</span></div>`;
    if (n.entities && n.entities.length) html += `<div class="detail-row"><span class="label">Entities</span><span class="value">${{n.entities.join(', ')}}</span></div>`;
    if (n.tags && n.tags.length) html += `<div class="detail-row"><span class="label">Tags</span><span class="value">${{n.tags.join(', ')}}</span></div>`;
    html += '</div>';

    // Links OUT
    const outLinks = GRAPH_DATA.links.filter(l => l.source === nodeId);
    if (outLinks.length) {{
        html += '<div class="detail-section">';
        html += `<h3>Links OUT (${{outLinks.length}})</h3>`;
        outLinks.forEach(l => {{
            html += `<div class="link-item">
                <div class="link-header">
                    <span class="link-type">${{l.link_type}}</span>
                    <span class="link-weight weight-${{l.weight}}">${{l.weight}}</span>
                </div>
                <div>&rarr; ${{l.target}}</div>
                ${{l.rationale ? '<div class="link-rationale">' + l.rationale + '</div>' : ''}}
            </div>`;
        }});
        html += '</div>';
    }}

    // Links IN
    const inLinks = GRAPH_DATA.links.filter(l => l.target === nodeId);
    if (inLinks.length) {{
        html += '<div class="detail-section">';
        html += `<h3>Links IN (${{inLinks.length}})</h3>`;
        inLinks.forEach(l => {{
            html += `<div class="link-item">
                <div class="link-header">
                    <span class="link-type">${{l.link_type}}</span>
                    <span class="link-weight weight-${{l.weight}}">${{l.weight}}</span>
                </div>
                <div>&larr; ${{l.source}}</div>
                ${{l.rationale ? '<div class="link-rationale">' + l.rationale + '</div>' : ''}}
            </div>`;
        }});
        html += '</div>';
    }}

    content.innerHTML = html;
    document.getElementById('sidebar').classList.add('active');

    // Highlight connected nodes
    highlightNode(nodeId);
}}

function showEdgeDetail(edgeId) {{
    const e = edges.get(edgeId);
    if (!e) return;

    document.getElementById('sidebar-title').textContent = `${{e.from}} → ${{e.to}}`;
    const content = document.getElementById('sidebar-content');

    let html = '<div class="detail-section">';
    html += '<h3>Edge Info</h3>';
    html += `<div class="detail-row"><span class="label">Source</span><span class="value">${{e.from}}</span></div>`;
    html += `<div class="detail-row"><span class="label">Target</span><span class="value">${{e.to}}</span></div>`;
    html += `<div class="detail-row"><span class="label">Type</span><span class="value">${{e.link_type}}</span></div>`;
    html += `<div class="detail-row"><span class="label">Weight</span><span class="value">${{e.weight}}</span></div>`;
    if (e.rationale) html += `<div class="detail-row"><span class="label">Rationale</span><span class="value">${{e.rationale}}</span></div>`;
    html += `<div class="detail-row"><span class="label">Created</span><span class="value">turn ${{e.created_turn}}</span></div>`;
    if (e.inactive_turns) html += `<div class="detail-row"><span class="label">Inactive</span><span class="value">${{e.inactive_turns}} turns</span></div>`;
    html += '</div>';

    content.innerHTML = html;
    document.getElementById('sidebar').classList.add('active');

    // Highlight edge
    highlightEdge(e.from, e.to);
}}

function closeSidebar() {{
    document.getElementById('sidebar').classList.remove('active');
}}

// ============================================================
// HIGHLIGHT
// ============================================================
function highlightNode(nodeId) {{
    const connected = new Set([nodeId]);
    GRAPH_DATA.links.forEach(l => {{
        if (l.source === nodeId) connected.add(l.target);
        if (l.target === nodeId) connected.add(l.source);
    }});

    nodes.forEach(n => {{
        nodes.update({{
            id: n.id,
            opacity: connected.has(n.id) ? 1.0 : 0.15
        }});
    }});
    edges.forEach(e => {{
        const isConnected = (e.from === nodeId || e.to === nodeId);
        edges.update({{
            id: e.id,
            opacity: isConnected ? 1.0 : 0.05,
            width: isConnected ? linkWidth(e.weight) * 1.5 : linkWidth(e.weight)
        }});
    }});
}}

function highlightEdge(fromId, toId) {{
    nodes.forEach(n => {{
        nodes.update({{
            id: n.id,
            opacity: (n.id === fromId || n.id === toId) ? 1.0 : 0.15
        }});
    }});
    edges.forEach(e => {{
        const isTarget = (e.from === fromId && e.to === toId);
        edges.update({{
            id: e.id,
            opacity: isTarget ? 1.0 : 0.05,
            width: isTarget ? linkWidth(e.weight) * 2 : linkWidth(e.weight)
        }});
    }});
}}

function resetHighlight() {{
    nodes.forEach(n => nodes.update({{ id: n.id, opacity: 1.0 }}));
    edges.forEach(e => edges.update({{ id: e.id, opacity: 0.6, width: linkWidth(e.weight) }}));
}}

// ============================================================
// CONTROLS
// ============================================================
function fitGraph() {{
    network.fit({{ animation: true }});
}}

let physicsEnabled = CFG.physics;
function togglePhysics() {{
    physicsEnabled = !physicsEnabled;
    network.setOptions({{ physics: {{ enabled: physicsEnabled }} }});
}}

// ============================================================
// FILTERS (dynamic from domains)
// ============================================================
const allDomains = [...new Set(GRAPH_DATA.nodes.map(n => n.domain))].sort();
const activeDomains = new Set(allDomains);

function buildFilters() {{
    const container = document.getElementById('filters');
    container.innerHTML = '<span style="color:#8b949e">Domain:</span>';
    allDomains.forEach(d => {{
        const count = GRAPH_DATA.nodes.filter(n => n.domain === d).length;
        const item = document.createElement('label');
        item.className = 'filter-item';
        item.innerHTML = `<input type="checkbox" checked onchange="toggleDomain('${{d}}', this.checked)"> ${{d}} (${{count}})`;
        container.appendChild(item);
    }});
}}

function toggleDomain(domain, show) {{
    if (show) {{
        activeDomains.add(domain);
    }} else {{
        activeDomains.delete(domain);
    }}
    nodes.forEach(n => {{
        const visible = activeDomains.has(n.domain);
        nodes.update({{ id: n.id, hidden: !visible }});
    }});
    edges.forEach(e => {{
        const fromNode = nodeMap[e.from];
        const toNode = nodeMap[e.to];
        const visible = (fromNode && activeDomains.has(fromNode.domain)) &&
                        (toNode && activeDomains.has(toNode.domain));
        edges.update({{ id: e.id, hidden: !visible }});
    }});
}}

buildFilters();
</script>
</body>
</html>"""

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("\n  Neuron Graph Visualizer - Generator\n")

    # Ensure output directory exists
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  Output: {OUTPUT_DIR}")

    # Export graph from default context
    print("  Exporting graph from database...")
    graph_data = export_graph("default")
    print(f"  [OK] {len(graph_data['nodes'])} nodes, {len(graph_data['links'])} links, turn {graph_data['turn_count']}")

    # Download vis-network (cached)
    vis_code = download_vis_network()

    # Write config JS
    config_path = OUTPUT_DIR / "neuron-graph-config.js"
    config_path.write_text(CONFIG_JS, encoding="utf-8")
    print(f"  [OK] Config: {config_path}")

    # Write HTML
    html = generate_html(graph_data, vis_code)
    html_path = OUTPUT_DIR / "neuron-graph.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"  [OK] HTML: {html_path}")

    # Success
    print(f"\n  {'='*60}")
    print(f"  [SUCCESS] Graph generated!")
    print(f"  Location: {OUTPUT_DIR}")
    print(f"  Open: {html_path}")
    print(f"  {'='*60}\n")

    # Open the folder
    try:
        if os.name == "nt":
            os.startfile(str(OUTPUT_DIR))
        else:
            import subprocess
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(OUTPUT_DIR)])
    except Exception as e:
        print(f"  [!] Could not open folder: {e}")
        print(f"      Open manually: {OUTPUT_DIR}")

if __name__ == "__main__":
    main()
