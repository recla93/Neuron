"""Stimulus engine: topic shift, auto-linking, context window, flashes (T57).

Logic moved verbatim out of server.py (ADR-006, same pattern as neuron.search):
STATE and CONFIG stay on the server module (``flash_enabled`` is toggled by the
`flash` tool, thresholds are patched by tests via ``_srv.*``), and everything
mutable/patchable is resolved through the server namespace AT CALL TIME via
``_S()`` — so every existing monkeypatch keeps working with zero test changes.
"""

from __future__ import annotations

from neuron.extraction import ExtractionResult
from neuron.models import Link


def _S():
    """The server module = the shared state/config namespace."""
    from neuron import server as _srv
    return _srv


def _keyword_overlap(a: list[str], b: list[str]) -> float:
    if not a or not b:
        return 0.0
    set_a, set_b = set(a), set(b)
    inter = set_a & set_b
    return len(inter) / max(len(set_a | set_b), 1)


def _detect_topic_shift(new_kw: list[str], graph=None) -> tuple[bool, float]:
    s = _S()
    g = graph or s._g.get()
    if not g.last_keywords:
        return False, 0.0
    overlap = _keyword_overlap(new_kw, g.last_keywords)
    return overlap < s.TOPIC_SHIFT_THRESHOLD, overlap


def _auto_link(new_kw: list[str], turn: int, graph=None) -> list[Link]:
    """Create automatic links between new keywords and existing keywords in the graph."""
    s = _S()
    g = graph or s._g.get()
    if not g.nodes:
        return []
    links: list[Link] = []
    added_pairs: set[tuple[str, str]] = set()
    MAX_AUTO_LINKS = 8

    for kw in new_kw:
        candidates = s._search_embeddings([kw], top_n=10, graph=g)
        for candidate_kw, sim in candidates:
            if candidate_kw == kw or candidate_kw in new_kw:
                continue
            if sim < 0.30:          # raised from 0.15 — cuts tangential noise
                continue
            pair = (kw, candidate_kw)
            rev_pair = (candidate_kw, kw)
            if pair in added_pairs or rev_pair in added_pairs:
                continue
            # also skip if link already exists in the graph (cross-call dedup)
            if any((lk.source == kw and lk.target == candidate_kw) or
                   (lk.source == candidate_kw and lk.target == kw)
                   for lk in g.links):
                continue
            weight = "strong" if sim > 0.65 else "medium" if sim > 0.45 else "tangential"
            links.append(Link(
                source=kw, target=candidate_kw,
                link_type="analogy",
                weight=weight,
                rationale=f"similarità vettoriale {sim:.2f}",
                created_turn=turn, last_active_turn=turn,
            ))
            added_pairs.add(pair)
        if len(links) >= MAX_AUTO_LINKS:
            break

    return links


def _build_context_window(extraction: ExtractionResult, turn: int, graph=None) -> str:
    """Build the optimal context window: active links + salient nodes + semantic flashes.

    Flash semantici (3 types, only when flash_enabled and turn > 3):
      1. Dormant pulse — high-salience node not mentioned in ≥ TANGENTIAL_EXPIRY_TURNS turns,
         semantically close to current keywords. Surfaces forgotten knowledge.
      2. Cross-domain spark — semantically similar node from a *different* loaded context graph.
         Bridges separate knowledge domains.
      3. Creative leap — a node reachable in exactly 2 hops from current keywords whose domain
         differs from the active domain. The most unexpected association.
    """
    s = _S()
    g = graph or s._g.get()
    parts: list[str] = []
    active_links = g.get_active_links()
    if active_links:
        top = sorted(
            active_links,
            key=lambda lk: (s.WEIGHT_ORDER[lk.weight], -lk.inactive_turns),
            reverse=True,
        )[:6]
        parts.append("Active links:")
        for lk in top:
            parts.append(f"  {lk.source} ->({lk.link_type})-> {lk.target} [{lk.weight}]")

    top_nodes = sorted(g.nodes, key=lambda nd: -nd.salience)[:8]
    if top_nodes:
        parts.append(f"\nSalient nodes (topic: {extraction.topic}):")
        for nd in top_nodes:
            parts.append(f"  {nd.keyword} (salience={nd.salience}, domain={nd.domain})")

    if turn > 1:
        overlap = _keyword_overlap(extraction.keywords, g.last_keywords)
        parts.append(f"\nContinuità col turno precedente: {overlap:.0%}")

    # --- Semantic flashes (E2.4) ---
    # The three heuristics (dormant pulse / cross-domain spark / creative leap)
    # GENERATE candidates; the stimulus engine (spreading_activation, E2.3)
    # SCORES the in-graph ones, and only the top-2 by activation are emitted —
    # "which association is strongest", not a dump of three.
    # NOTE (future, "Option B"): make spreading_activation the PRIMARY generator —
    # the highest-activation non-obvious node IS the stimulus, with dormant/leap as
    # emergent properties — a bolder reshape kept as a maybe, to revisit on real data.
    if s.flash_enabled and turn > 3:
        active_kws = set(extraction.keywords)
        act_map = dict(g.spreading_activation(list(active_kws), k=2))
        max_act = max(act_map.values(), default=1.0) or 1.0
        candidates: list[tuple[float, str]] = []   # (score ~0..1, text)

        # 1. Dormant pulse: salient node silent for ≥ threshold, close to the query
        sleep_threshold = max(s.TANGENTIAL_EXPIRY_TURNS, 4)
        dormant = [
            nd for nd in g.nodes
            if (turn - nd.turn) >= sleep_threshold
            and nd.salience >= 2
            and nd.keyword not in active_kws
        ]
        if dormant:
            try:
                sims = s._search_embeddings(extraction.keywords, top_n=8, graph=g)
            except Exception:
                # Fallback: pick most salient dormant node directly without vector search
                sims = [(nd.keyword, 0.5) for nd in sorted(dormant, key=lambda n: -n.salience)]
            dormant_set = {nd.keyword for nd in dormant}
            for kw, sim in sims:
                if kw in dormant_set and sim > 0.38:
                    nd = g.get_node(kw)
                    dormant_since = turn - nd.turn if nd else "?"
                    score = max(act_map.get(kw, 0.0) / max_act, sim)
                    candidates.append((score,
                        f"💤 Dormant pulse: '{kw}' (sim={sim:.2f}, "
                        f"silent {dormant_since} turns, salience={nd.salience if nd else '?'})"))
                    break  # one dormant flash is enough

        # 2. Cross-domain spark: semantically close node from a different context
        # graph. The engine is single-graph, so this stays a distinct signal,
        # scored by its own similarity.
        if hasattr(s._g, "_graphs"):
            for other_ctx, other_g in list(s._g._graphs.items()):
                if other_ctx == s._g.active or not other_g.nodes:
                    continue
                cross = s._search_embeddings(extraction.keywords, top_n=2, graph=other_g)
                for kw, sim in cross:
                    if sim > 0.48 and kw not in active_kws:
                        nd = other_g.get_node(kw)
                        dom = nd.domain if nd else other_ctx
                        candidates.append((sim,
                            f"🔗 Cross-domain spark [{other_ctx}]: '{kw}' "
                            f"(sim={sim:.2f}, domain={dom})"))
                        # E3.1: persist this cross-context co-occurrence as an
                        # implicit drift link (other_ctx is loaded → visited;
                        # born tangential, cooldown 5, pruned fast).
                        if extraction.keywords:
                            g.form_drift_link(extraction.keywords[0], kw, other_ctx, turn)
                        break  # one spark per other context

        # 3. Creative leap: 2-hop path from active keywords to a node in a different
        # domain, scored by that far node's activation.
        adjacency: dict[str, set[str]] = {}
        for lk in g.links:
            if lk.weight in ("strong", "medium"):
                adjacency.setdefault(lk.source, set()).add(lk.target)
                adjacency.setdefault(lk.target, set()).add(lk.source)

        leap: "tuple[float, str] | None" = None
        for kw in active_kws:
            for mid in adjacency.get(kw, set()):
                if mid in active_kws:
                    continue
                for far in adjacency.get(mid, set()):
                    if far in active_kws or far == kw:
                        continue
                    nd = g.get_node(far)
                    if nd and nd.domain != extraction.domain:
                        leap = (act_map.get(far, 0.0) / max_act,
                                f"⚡ Creative leap: '{kw}' → '{mid}' → '{far}' [{nd.domain}]")
                        break
                if leap:
                    break
            if leap:
                break
        if leap:
            candidates.append(leap)

        if candidates:
            candidates.sort(key=lambda x: -x[0])
            parts.append("\nFlash semantici:")
            for _score, fl in candidates[:2]:   # top-2 by activation (E2.4)
                parts.append(f"  {fl}")

    return "\n".join(parts) if parts else ""


def _stimulus_block(g, keywords) -> str:
    """Compact one-line associative stimulus for piggybacking on tool responses
    that don't already carry the full flash block (E2.5). It is the top
    spreading-activation node from this turn's keywords — continuous stimulation
    without MCP push. Empty when nothing clears STIMULUS_MIN_ACTIVATION, so
    responses aren't padded with noise; hard-capped to ~40 tokens."""
    s = _S()
    if not s.flash_enabled:
        return ""
    ranked = g.spreading_activation(list(keywords), k=2)
    if not ranked or ranked[0][1] < s.STIMULUS_MIN_ACTIVATION:
        return ""
    kw, act = ranked[0]
    nd = g.get_node(kw)
    dom = f", {nd.domain}" if nd else ""
    return f"\n🧠 stimulus: {kw} (act={act:.2f}{dom})"[:s.STIMULUS_MAX_CHARS]
