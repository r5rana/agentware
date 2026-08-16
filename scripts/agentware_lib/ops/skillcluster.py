"""agentware_lib.ops.skillcluster -- skill-candidate CLUSTER DETECTION over the learnings corpus (layer: ops).

Owns: the detection half of auto skill-promotion -- the six algorithm thresholds
(`_SKILL_CLUSTER_*`), the pairwise edge weight (`_skill_edge_weight`), the ranked
per-cluster scores (`_skill_cluster_scores`), the density-gated quasi-clique
grower (`cluster_learnings`) and the operator-tunable threshold resolution that
feeds it (`resolve_skill_promote_thresholds`).
Does NOT own: the workorder EMITTER or the run that drives it ->
features.skillpropose (`_emit_skill_candidate_workorder` / `_skill_promote_run`);
the dream step that runs it on the routine cadence ->
features.dreamsteps.stepskillpropose; the mode/cap settings themselves ->
core.config; the shared tokenizer and corpus -> retrieval.corpus.

Detection-only and READ-ONLY (INV-2): nothing here writes the index, the KB, or
the network. That is the whole reason detection is a separate module from
emission -- a reader who lands on `cluster_learnings` should not have to check
whether it also proposes something.

WHY THE THRESHOLDS ARE DISTINCT FROM `_CONFLICT_JACCARD`
-------------------------------------------------------
The duplicate detector's bar is 0.6 because two entries SAYING THE SAME THING
share vocabulary. A real multi-step procedure does the opposite: its steps share
a tag spine but little running vocabulary, so a high edge bar would find nothing.
The edge bar is therefore LOWER (0.30), and what separates a genuine clique from
a single-linkage `A~B~C` chain is not similarity at all but DENSITY, enforced
DURING growth rather than after: `A!~C` gives density 2/3 = 0.67, below the 0.70
gate, while a true triangle scores 3/3 = 1.0 and survives. Size is clamped to
[3, 6] so a duplicate PAIR can never surface as a skill and a fact-pile can never
masquerade as one procedure -- which is also what keeps `cluster_learnings` and
`conflict_pairs` from ever double-flagging the same finding.

WHY THE RESOLVER LIVES HERE AND NOT IN core.config
--------------------------------------------------
`resolve_skill_promote_thresholds` resolves ALGORITHM defaults, three of which
(`edge_min`/`density_min`/`separation_min`) are the `_SKILL_CLUSTER_*` values
defined immediately above it. Homing it in `core.config` would force those
defaults up into the config layer and split one six-constant family across two
modules -- so the generic [0,1] resolution primitive lives in core
(`_resolve_unit_threshold`) and only the binding of keys to ALGORITHM defaults
lives here, beside the algorithm it tunes. The tag-spine and size gates are
deliberately NOT operator-tunable.

Layer deps: ops -> core (`core.config`: `_resolve_unit_threshold` + the three
`SKILL_PROMOTE_*_MIN_KEY` names + `core.frontmatter._entry_list_field`), ops ->
retrieval (`retrieval.corpus`: `build_corpus`/`tokenize`, imported BY NAME --
`cluster_learnings` binds a local `corpus` variable but never a local
`build_corpus`/`tokenize`, so there is no collision to alias around, and a
by-name import keeps the moved bodies byte-identical to the pre-move originals).
The token signal is the SHARED corpus, so cluster edges match recall exactly
rather than approximately.

Determinism: pure given (kdir, data) and the thresholds -- no clock, no random.
Clusters and their members come back in a stable ranked order, so two runs over
an unchanged corpus emit the identical candidate set (INV-1).
"""

from agentware_lib.core.config import (
    SKILL_PROMOTE_DENSITY_MIN_KEY,
    SKILL_PROMOTE_EDGE_MIN_KEY,
    SKILL_PROMOTE_SEPARATION_MIN_KEY,
    _resolve_unit_threshold,
)
from agentware_lib.core.frontmatter import _entry_list_field
from agentware_lib.retrieval.corpus import build_corpus, tokenize


def resolve_skill_promote_thresholds():
    """Resolve the operator-tunable detection thresholds for cluster_learnings.

    Each is env -> config.env -> the algorithm default (`_SKILL_CLUSTER_*`),
    passed as the edge/density/separation kwargs WITHOUT altering the algorithm.
    """
    return {
        "edge_min": _resolve_unit_threshold(
            SKILL_PROMOTE_EDGE_MIN_KEY, _SKILL_CLUSTER_EDGE_MIN),
        "density_min": _resolve_unit_threshold(
            SKILL_PROMOTE_DENSITY_MIN_KEY, _SKILL_CLUSTER_DENSITY_MIN),
        "separation_min": _resolve_unit_threshold(
            SKILL_PROMOTE_SEPARATION_MIN_KEY, _SKILL_CLUSTER_SEPARATION_MIN),
    }


# --- Skill-candidate clustering (feature 260722-auto-skill-promotion) ---------
# Detection-only, read-only (INV-2): find cohesive, RECURRING learning clusters
# that look like a reusable multi-step procedure (a skill), NOT duplicate pairs.
#
# Named module-level thresholds (mirroring `_CONFLICT_JACCARD`) so Task 3 can wire
# env -> config.env -> default resolution over them WITHOUT touching the algorithm.
# Deliberately DISTINCT from `_CONFLICT_JACCARD` (0.6 = the DUPLICATE bar): real
# multi-step procedures have LOW cross-step vocabulary overlap, so the edge bar is
# lower and a DENSITY gate (not similarity) is what separates a true clique from a
# single-linkage `A~B~C` chain. Size 3..6 keeps a duplicate PAIR (size 2) out of
# the skill surface entirely, so `cluster_learnings` and `conflict_pairs` never
# double-flag the same finding.
_SKILL_CLUSTER_EDGE_MIN = 0.30       # min edge weight (max of token / tag Jaccard)


_SKILL_CLUSTER_DENSITY_MIN = 0.70    # min subgraph density enforced DURING growth


_SKILL_CLUSTER_SEPARATION_MIN = 0.10  # min (mean intra-edge - mean nearest-external)


_SKILL_CLUSTER_TAGSPINE_MIN = 0.60   # min share of members carrying one dominant tag


_SKILL_CLUSTER_MIN_SIZE = 3          # below this a "cluster" is a duplicate pair


_SKILL_CLUSTER_MAX_SIZE = 6          # above this it is a fact-pile, not one skill


def _skill_edge_weight(tok_a, tags_a, tok_b, tags_b):
    """Edge weight between two learnings = max(token-set Jaccard, tag-set Jaccard).

    The tag arm rescues genuine multi-step procedures whose steps share a tag-spine
    but little running vocabulary (the token arm alone would miss them). Pure,
    deterministic, read-only."""
    tu = tok_a | tok_b
    tok_jac = (len(tok_a & tok_b) / len(tu)) if tu else 0.0
    gu = tags_a | tags_b
    tag_jac = (len(tags_a & tags_b) / len(gu)) if gu else 0.0
    return tok_jac if tok_jac >= tag_jac else tag_jac


def _skill_cluster_scores(members, adjw, tagmap):
    """Compute (cohesion, density, separation, tag_spine_tag, tag_spine_share) for a
    member set given the weighted adjacency `adjw` (id -> {neighbor: weight}, edges
    only) and `tagmap` (id -> set(tags)). Deterministic, pure."""
    ml = sorted(members)
    n = len(ml)
    memberset = set(ml)
    intra = []
    for x in range(n):
        for y in range(x + 1, n):
            w = adjw[ml[x]].get(ml[y])
            if w is not None:
                intra.append(w)
    cohesion = (sum(intra) / len(intra)) if intra else 0.0
    density = (len(intra) / (n * (n - 1) / 2.0)) if n >= 2 else 1.0
    # nearest-external: for each member, its strongest edge to a NON-member (0 if
    # isolated from the outside). A well-separated cluster sits far above its halo.
    ext = []
    for m in ml:
        best_ext = 0.0
        for other, w in adjw[m].items():
            if other not in memberset and w > best_ext:
                best_ext = w
        ext.append(best_ext)
    mean_ext = (sum(ext) / len(ext)) if ext else 0.0
    separation = cohesion - mean_ext
    counts = {}
    for m in ml:
        for t in tagmap.get(m) or ():
            counts[t] = counts.get(t, 0) + 1
    if counts:
        # dominant tag: highest count, tie-break tag name asc (deterministic).
        tag_spine_tag = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        tag_spine_share = counts[tag_spine_tag] / float(n)
    else:
        tag_spine_tag, tag_spine_share = None, 0.0
    return {"cohesion": cohesion, "density": density, "separation": separation,
            "tag_spine_tag": tag_spine_tag, "tag_spine_share": tag_spine_share}


def cluster_learnings(kdir, data,
                      edge_min=_SKILL_CLUSTER_EDGE_MIN,
                      density_min=_SKILL_CLUSTER_DENSITY_MIN,
                      separation_min=_SKILL_CLUSTER_SEPARATION_MIN,
                      tagspine_min=_SKILL_CLUSTER_TAGSPINE_MIN,
                      min_size=_SKILL_CLUSTER_MIN_SIZE,
                      max_size=_SKILL_CLUSTER_MAX_SIZE):
    """Return ranked skill-candidate clusters over `category==learnings` entries.

    Detection-only and read-only (INV-2): NO index / KB / network write. Reuses the
    shared BM25 corpus (`build_corpus` + `tokenize`) so the token signal matches
    recall exactly. Edge weight = max(token-set Jaccard, tag-set Jaccard) >= edge_min.

    Clusters form by DENSITY-GATED quasi-clique growth (density enforced DURING
    formation, NOT union-find): a candidate is admitted only if the resulting
    subgraph's density stays >= density_min, which mechanically REJECTS a
    single-linkage `A~B~C` chain (`A!~C` => density 2/3 = 0.67 < 0.70) while a true
    triangle (3/3 = 1.0) survives. A cluster qualifies only when size in
    [min_size, max_size] AND separation margin >= separation_min AND a dominant tag
    shares >= tagspine_min of members.

    Returns [{members:[ids], size, cohesion, density, separation, tag_spine,
    tag_spine_share}, ...] ranked by (separation, density, cohesion) desc then
    members asc — fully deterministic given the KB."""
    corpus = build_corpus(kdir, data)
    nodes = []
    for (e, text) in corpus:
        if (e.get("category") or "") != "learnings":
            continue
        eid = e.get("id")
        if not eid:
            continue
        nodes.append((eid, set(tokenize(text)),
                      set(_entry_list_field(e, "tags"))))
    nodes.sort(key=lambda n: n[0])                       # deterministic id order
    ids = [n[0] for n in nodes]
    tokmap = {n[0]: n[1] for n in nodes}
    tagmap = {n[0]: n[2] for n in nodes}
    # Weighted adjacency (edges only) over the qualifying-weight pairs.
    adjw = {i: {} for i in ids}
    for a in range(len(ids)):
        ia = ids[a]
        for b in range(a + 1, len(ids)):
            ib = ids[b]
            w = _skill_edge_weight(tokmap[ia], tagmap[ia], tokmap[ib], tagmap[ib])
            if w >= edge_min:
                adjw[ia][ib] = w
                adjw[ib][ia] = w

    def _density(mem):
        n = len(mem)
        if n < 2:
            return 1.0
        ml = list(mem)
        actual = 0
        for x in range(len(ml)):
            for y in range(x + 1, len(ml)):
                if ml[y] in adjw[ml[x]]:
                    actual += 1
        return actual / (n * (n - 1) / 2.0)

    used = set()
    out = []
    for seed in ids:
        if seed in used or not adjw[seed]:
            continue
        cluster = {seed}
        while len(cluster) < max_size:
            best, best_score = None, None
            for cand in ids:                            # id order => stable ties
                if cand in cluster or cand in used:
                    continue
                if not any(m in adjw[cand] for m in cluster):
                    continue                            # must connect to cluster
                if _density(cluster | {cand}) < density_min:
                    continue                            # density gate DURING growth
                score = sum(adjw[cand].get(m, 0.0) for m in cluster)
                if best is None or score > best_score:
                    best, best_score = cand, score
            if best is None:
                break
            cluster.add(best)
        if len(cluster) < min_size:
            continue
        sc = _skill_cluster_scores(cluster, adjw, tagmap)
        if (sc["density"] < density_min or sc["separation"] < separation_min
                or sc["tag_spine_share"] < tagspine_min):
            continue
        used |= cluster
        out.append({
            "members": sorted(cluster),
            "size": len(cluster),
            "cohesion": round(sc["cohesion"], 6),
            "density": round(sc["density"], 6),
            "separation": round(sc["separation"], 6),
            "tag_spine": sc["tag_spine_tag"],
            "tag_spine_share": round(sc["tag_spine_share"], 6),
        })
    out.sort(key=lambda c: tuple(c["members"]))
    out.sort(key=lambda c: (c["separation"], c["density"], c["cohesion"]),
             reverse=True)
    return out


_EXPORTS = [
    "_SKILL_CLUSTER_EDGE_MIN",
    "_SKILL_CLUSTER_DENSITY_MIN",
    "_SKILL_CLUSTER_SEPARATION_MIN",
    "_SKILL_CLUSTER_TAGSPINE_MIN",
    "_SKILL_CLUSTER_MIN_SIZE",
    "_SKILL_CLUSTER_MAX_SIZE",
    "_skill_edge_weight",
    "_skill_cluster_scores",
    "cluster_learnings",
    "resolve_skill_promote_thresholds",
]

__all__ = list(_EXPORTS)
