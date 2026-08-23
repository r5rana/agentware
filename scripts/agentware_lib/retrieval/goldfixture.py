"""agentware_lib.retrieval.goldfixture - the frozen synthetic gold-fixture suite (layer: retrieval).

Owns: the DRIFT-FREE frozen gold-fixture regression suite (`eval --suite gold-fixture`) -
the suite name constant (GOLD_FIXTURE_SUITE), the repo-relative fixture path
(_GOLD_FIXTURE_REL) + its absolute resolver (gold_fixture_path), the fixture
loader/validator (load_gold_fixture -> (entries, gold) over its two sequential validation
phases _load_fixture_corpus / _load_fixture_gold, which share only the declared-id set;
full error taxonomy, and the CATEGORY
CONTRACT: every returned entry carries a `category` - declared, else _DEFAULT_CATEGORY - so the
R-RET-03 skill pin is REACHABLE over this corpus instead of silently inert), and the
scorer (evaluate_gold_fixture - score a strategy over the FROZEN synthetic corpus, tag
the result as the gold-fixture suite, and assert determinism by re-scoring). Because the
corpus ships frozen in the repo (tests/fixtures/gold_fixture_suite.json - synthetic, NO
operator data, R-LOC-03), its corpus_fingerprint is stable across runs AND independent of
any operator KB, so the absolute-score gate on this suite never false-FAILs on live-KB growth.
Does NOT own: the ranking/scoring engine it reuses (evaluate -> agentware_lib.retrieval.metrics_rank
via _metrics_rank); the repo-root anchor (REPO_ROOT -> agentware_lib.core.paths via paths);
the benchmark-suite ledger recorder (record_ledger_suite -> agentware_lib.retrieval.ledger,
wave 3 seam 12), the ablation instrument (evaluate_ablation, now
agentware_lib.retrieval.evalcmd), the ACR win gate, the regression + A/B gates, the scorecard and
the SWE bench (agentware_lib.retrieval.{acrgate,regressiongate,scorecard,bench}); the eval CLI
handler (cmd_eval, now agentware_lib.retrieval.evalcmd, wave-3 seam 16b - reaches these via the
_goldfixture alias) and the release gate's gold-fixture check (_gate2_lite_gold_fixture, resident -
calls gold_fixture_path / evaluate_gold_fixture as bare names via the facade bind on the entrypoint).
Layer deps: retrieval -> core (agentware_lib.core.paths as `paths` for REPO_ROOT) + retrieval ->
retrieval (agentware_lib.retrieval.metrics_rank as `_metrics_rank` for evaluate, so the
global-resolve gate never walks a foreign sibling function held in this module's __dict__).
Imports nothing above retrieval, nothing in knowledge/; nothing in core/ or knowledge/ imports from here.
Fan-in: 2 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

INV-1: evaluate_gold_fixture is deterministic at a fixed as_of - it re-scores the frozen
corpus and surfaces the ranked-order match as determinism_ok; the ONLY non-deterministic
field is latency_ms* (time.perf_counter), which downstream diffing strips. INV-2: read-only -
it builds a synthetic in-memory index (entries carry their text inline, no file I/O beyond
reading the package fixture, no network, no LLM) and never mutates the entry, index.json or the KB.
"""

import json
import os

from agentware_lib.core import paths
from agentware_lib.retrieval import metrics_rank as _metrics_rank


GOLD_FIXTURE_SUITE = "gold-fixture"
_GOLD_FIXTURE_REL = os.path.join(
    "tests", "fixtures", "gold_fixture_suite.json")

# The category a corpus entry gets when the fixture declares none. `references`
# is the codebase's own fallback (knowledge.index.indexread: an unrecognised path
# "infers category `references` (not `skills`)"), and it is INERT for ranking:
# only "skills" (the R-RET-03 pin in retrieval.pinpolicy) and "learnings" (the
# pin's never-evict guarantee + freshsignals.VOLATILE_CATEGORIES) are
# load-bearing. Defaulting HERE rather than in the frozen fixture keeps the
# contract a property of the LOADER — a corpus entry added later without a
# category still arrives categorised — and leaves the fixture bytes untouched.
_DEFAULT_CATEGORY = "references"


def gold_fixture_path():
    """Absolute path to the frozen gold-fixture file shipped with the package."""
    return os.path.join(paths.REPO_ROOT, _GOLD_FIXTURE_REL)


def _corpus_entry(eid, raw):
    """Build ONE index-style corpus entry from a validated fixture record.

    Split out of `load_gold_fixture` so the field-defaulting lives in one named
    place instead of inside an already branch-heavy validator: it is the single
    point that decides what a corpus entry LOOKS like, including the category
    contract (declared category, else `_DEFAULT_CATEGORY`, so no entry ever
    reaches the scorer uncategorised and the skill pin stays reachable). `eid` is
    passed in already validated; `raw` is the fixture's record. Pure.
    """
    return {
        "id": eid,
        "title": raw.get("title") or "",
        "summary": raw.get("summary") or "",
        "tags": list(raw.get("tags") or []),
        "category": raw.get("category") or _DEFAULT_CATEGORY,
    }


def _load_fixture_corpus(corpus):
    """Validate the fixture's `corpus` list into (entries, seen_ids).

    The FIRST of `load_gold_fixture`'s two sequential validation phases, split
    out at task 99 wave 10. The two phases share exactly one value -- the set of
    ids the corpus declared, which the gold phase resolves its `expected_ids`
    against -- so the seam is where that value is PRODUCED, and the split moves
    branches rather than removing any: every refusal below is still raised from
    here, with its message byte-identical.

    Returns `(entries, seen)`. `entries` are index-style dicts built by
    `_corpus_entry` (so the category contract holds for every one of them);
    `seen` is the id set, returned rather than recomputed because a second pass
    over `entries` would be a SECOND rule about what an id is. Raises ValueError
    with the fixture's own message on any malformed record. Pure; deterministic.
    """
    if not isinstance(corpus, list) or not corpus:
        raise ValueError("gold-fixture 'corpus' must be a non-empty list")
    entries = []
    seen = set()
    for i, e in enumerate(corpus):
        if not isinstance(e, dict):
            raise ValueError("gold-fixture corpus entry #%d is not an object" % i)
        eid = e.get("id")
        if not isinstance(eid, str) or not eid.strip():
            raise ValueError("gold-fixture corpus entry #%d missing 'id'" % i)
        if eid in seen:
            raise ValueError("gold-fixture corpus has duplicate id %r" % eid)
        seen.add(eid)
        entries.append(_corpus_entry(eid, e))
    return entries, seen


def _load_fixture_gold(gold_rows, seen):
    """Validate the fixture's `gold` list against the corpus ids `seen`.

    The SECOND of `load_gold_fixture`'s two validation phases. It is the half
    that depends on the first: an `expected_ids` entry is resolved against the
    ids the corpus declared, which is why `seen` is a parameter rather than
    something this function could derive.

    Note the id comparison is `str(x) not in seen` and the stored value is
    `str(x)`: a fixture may write an id as a NUMBER, and both the membership
    test and the returned row coerce it, so a numeric id resolves instead of
    being reported as unknown. Raises ValueError with the fixture's own message.
    Pure; deterministic.
    """
    if not isinstance(gold_rows, list) or not gold_rows:
        raise ValueError("gold-fixture 'gold' must be a non-empty list")
    gold = []
    for i, row in enumerate(gold_rows):
        if not isinstance(row, dict):
            raise ValueError("gold-fixture gold entry #%d is not an object" % i)
        q = row.get("query")
        exp = row.get("expected_ids")
        if not isinstance(q, str) or not q.strip():
            raise ValueError("gold-fixture gold entry #%d missing 'query'" % i)
        if not isinstance(exp, list) or not exp:
            raise ValueError(
                "gold-fixture gold entry #%d missing 'expected_ids'" % i)
        for x in exp:
            if str(x) not in seen:
                raise ValueError(
                    "gold-fixture gold entry #%d expects unknown id %r" % (i, x))
        gold.append({"query": q, "expected_ids": [str(x) for x in exp]})
    return gold


def load_gold_fixture(path):
    """Load the frozen gold-fixture: returns (entries, gold). Raises ValueError.

    `entries` is the synthetic corpus as index-style dicts
    (id/title/summary/tags/category) ready for build_corpus; `gold` is the
    {query, expected_ids[]} list validated by the shared load_gold rules. The
    file is package-shipped synthetic data with NO operator content (R-LOC-03).
    Pure read; deterministic.

    EVERY returned entry carries a `category` (declared, else `_DEFAULT_CATEGORY`).
    This is load-bearing, not cosmetic: `category` is the key the R-RET-03 skill
    pin reads (`retrieval.pinpolicy._apply_skill_pin`: `entry.get("category") ==
    "skills"`), and this loader previously rebuilt each entry from a fixed
    four-key whitelist that SILENTLY dropped it. The fixture's one skills
    document therefore reached the scorer as an uncategorised entry, so the pin
    could never fire on the gold-fixture corpus -- while `pinpolicy`'s own
    docstring names "a self-contained gold-fixture Recall@5 no-regression check"
    as the gate that measures the pin. The instrument was blind to the thing it
    was documented to measure. Passing `category` through restores it.

    Score-neutral by construction: `corpus._entry_doc_text` joins only
    title+summary+tags+body, and `corpus.eval_corpus_fingerprint` hashes only
    (id, doc_text) -- so no category value can move a BM25 score or the corpus
    fingerprint. It changes only which POLICY branches are reachable.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except FileNotFoundError:
        raise ValueError("gold-fixture not found: %s" % path)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid JSON in gold-fixture %s: %s" % (path, exc))
    if not isinstance(doc, dict):
        raise ValueError("gold-fixture must be a JSON object")
    entries, seen = _load_fixture_corpus(doc.get("corpus"))
    return entries, _load_fixture_gold(doc.get("gold"), seen)


def evaluate_gold_fixture(kdir, fixture_path, strategy, top_k, as_of=None):
    """Score `strategy` over the FROZEN synthetic gold-fixture corpus.

    Reuses evaluate() over a synthetic in-memory index (entries carry their text
    inline, so no file I/O, network, or LLM), then tags the result as the
    `gold-fixture` suite. The whole-corpus fingerprint is stable across runs/
    machines (the corpus is frozen in the repo), so the absolute gate on this
    suite is DRIFT-FREE. Determinism (INV-1) is asserted by re-scoring and
    comparing ranked ordering; surfaced as `determinism_ok`. Read-only (INV-2).
    """
    entries, gold = load_gold_fixture(fixture_path)
    synth = {"entries": entries}
    result = _metrics_rank.evaluate(kdir, synth, gold, strategy, top_k, as_of=as_of)
    # Determinism check (INV-1): re-score and compare the ranked ordering of every
    # query. The fixture is tiny, so a full re-run is cheap. Latency is the only
    # non-deterministic field and is excluded from the comparison.
    again = _metrics_rank.evaluate(kdir, synth, gold, strategy, top_k, as_of=as_of)
    result["determinism_ok"] = (
        [q["ranked"] for q in result["per_query"]]
        == [q["ranked"] for q in again["per_query"]])
    result["suite"] = GOLD_FIXTURE_SUITE
    result["as_of"] = as_of
    return result


_EXPORTS = [
    "GOLD_FIXTURE_SUITE",
    "_GOLD_FIXTURE_REL",
    "_DEFAULT_CATEGORY",
    "_corpus_entry",
    "_load_fixture_corpus",
    "_load_fixture_gold",
    "gold_fixture_path",
    "load_gold_fixture",
    "evaluate_gold_fixture",
]
