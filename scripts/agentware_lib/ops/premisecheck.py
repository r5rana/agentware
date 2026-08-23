"""agentware_lib.ops.premisecheck — premise EVALUATORS against live state (layer: ops).

Owns: one evaluator per member of the closed check grammar and the dispatcher over
them — `_eval_check_cite_live` (with `_premise_cite_resolves`, the index lookup),
`_eval_check_config_eq` (a config read), `_eval_check_path` (with
`_premise_path_state` and `_parse_iso_to_epoch`, the only time comparison in the
system and an author-supplied one), `_eval_check_corpus_metric` (with
`_premise_corpus_metric_value` and the numeric comparator `_premise_num_cmp`), and
`evaluate_premise`, which routes a parsed record to its evaluator.

Does NOT own: the GRAMMAR or the parser — what a premise may SAY, and how a
`check:` string becomes a record, is `ops.planpremises`. That is the seam: this
module never parses and that one never touches live state, so the security
property "a check is rejected at parse and never stored as runnable" is provable
by reading one file, and "an evaluator only reads" by reading the other.

WHY THE SPLIT IS HERE
---------------------
The premise surface arrived from the monolith as one ~510-line block, which is
over the 600-line lib ceiling once it carries a module header. A file cap alone
would accept any cut; this one is chosen so the two halves have DIFFERENT
security obligations and can be audited separately. `planpremises` is the trust
boundary (untrusted plan text in, a closed-vocabulary record out, nothing
executable ever constructed). `premisecheck` is the read-only I/O half (a
validated record in, a verdict out, no parsing and therefore no way for plan text
to reach an evaluator un-normalised).

FAIL-OPEN IS A RETURN VALUE, NOT AN EXCEPTION
----------------------------------------------
Every evaluator distinguishes three outcomes, and the distinction is the whole
design: the premise HELD, the premise was FALSIFIED, or it is UNCHECKABLE because
the source is unavailable. Uncheckable returns `None` rather than raising or
defaulting to a boolean — a plan grounded at authoring time must never be able to
halt a later run merely because its evidence rotated away (D5), and a corpus that
is PRESENT but empty is a genuine 0, not an absence. Collapsing those two into one
value is the bug this shape exists to prevent.

Layer deps: ops.premisecheck -> core.config (`_read_config_key`,
`resolve_knowledge_dir`), knowledge.index (`load_index`) and
ops.sessionparse.minerfolds (the pure corpus folds) — all DOWNWARD or same-layer.
Imports NOTHING from `ops.planpremises`: the dependency runs one way only
(planpremises -> premisecheck), so there is no cycle to break.
Determinism: INV-1 (no clock, no network, no randomness in any decision) and
INV-2 (read-only; the corpus folds run over throwaway in-memory state).
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import os

from agentware_lib.core.config import (
    _read_config_key,
    resolve_knowledge_dir,
)
from agentware_lib.knowledge.index import load_index
from agentware_lib.ops.sessionparse.minerfolds import (
    MINER_RECURRENCE_N,
    _parse_prompts_records,
    _transcript_is_err_status,
    _transcript_list_session_ids,
    _transcript_norm_key,
    _transcript_read_live,
    _transcript_recurrence_gate,
    _transcript_recurrence_relevant,
)


def _cite_in_index(cite, kdir):
    """RESOLVER 1 — True iff `cite` matches a live index row's `id` or `path`.

    INV-2: `_allow_rebuild=False` — a read-only verb must NEVER trigger
    load_index's transparent index.json rebuild (which writes index.json /
    FEATURES.md / rosters / the BM25 cache). A missing or corrupt index is NOT
    an error here: it degrades to False so the caller falls through to
    `_cite_under_kdir`, which is the whole reason the two resolvers are a
    disjunction rather than a chain of guards.

    The bare `except Exception` is wider than the loader's own contract on
    purpose: `shared/` is the attacker-influenced half of an org corpus, so the
    index is UNTRUSTED input, and a premise gate the loop pre-hook runs must not
    be the thing that raises on it.

    The `isinstance(e, dict)` guard is load-bearing for the same reason — an
    index whose `entries` holds a bare string would otherwise raise
    AttributeError inside the gate.
    """
    try:
        data, err = load_index(kdir, _allow_rebuild=False)
    except Exception:
        return False
    if err is not None or not isinstance(data, dict):
        return False
    for e in data.get("entries") or []:
        if isinstance(e, dict) and (e.get("id") == cite
                                    or e.get("path") == cite):
            return True
    return False


def _cite_under_kdir(cite, kdir):
    """RESOLVER 2 — True iff `cite` names an existing path INSIDE the knowledge
    dir. Work artifacts (`work/<feature>/worklog.md`) are NOT indexed, so an RCA
    citation resolves by filesystem existence and by nothing else.

    Containment is checked BEFORE existence, and that order is the security
    property: a `cite:` escaping the knowledge dir is unresolvable REGARDLESS of
    whether the target exists, so the gate can never be used to probe for files
    outside the KB. `kdir` itself resolves (`cand == base`), which is what makes
    a `.` citation legal.
    """
    try:
        base = os.path.abspath(kdir)
        cand = os.path.abspath(os.path.join(base, cite))
        if not (cand == base or cand.startswith(base + os.sep)):
            return False
        return os.path.exists(cand)
    except (ValueError, OSError):
        return False


def _premise_cite_resolves(cite, kdir):
    """True iff `cite` resolves to a live KB entry (indexed id/path) OR a real file
    under the knowledge dir (work artifacts are NOT indexed, so an RCA under work/
    resolves by filesystem existence). Path traversal escaping the KB is refused
    (unresolvable). Read-only.

    The two resolvers are INDEPENDENT and the order between them is not a
    fallback ladder: an indexed entry whose file has been deleted still
    resolves, and an unindexed file still resolves. Only the empty-input guard
    is shared, because neither resolver can answer without both operands.
    """
    if not cite or not kdir:
        return False
    return _cite_in_index(cite, kdir) or _cite_under_kdir(cite, kdir)


def _eval_check_cite_live(rec, kdir):
    cite = rec.get("cite")
    if not cite:
        return {"verdict": "uncheckable", "detail": "premise has no cite"}
    if _premise_cite_resolves(cite, kdir):
        return {"verdict": "grounded", "detail": "citation resolves: %s" % cite}
    return {"verdict": "uncheckable", "unresolved_cite": True,
            "detail": "citation does not resolve to a live KB entry or file: %s"
                      % cite}


def _eval_check_config_eq(parsed):
    key, want = parsed["args"][0], parsed["args"][1]
    val = _read_config_key(key)
    if val is None:
        return {"verdict": "uncheckable", "expected": want,
                "detail": "config key %s is unset" % key}
    if val == want:
        return {"verdict": "grounded", "actual": val, "expected": want,
                "detail": "%s == %s" % (key, want)}
    return {"verdict": "falsified", "actual": val, "expected": want,
            "detail": "%s is %r, expected %r" % (key, val, want)}


def _parse_iso_to_epoch(iso):
    """Author-supplied ISO-8601 -> UTC epoch seconds, or None. Deterministic;
    NEVER calls now() (the only time input is the author's literal string)."""
    import calendar
    import datetime as _dt
    s = str(iso or "").strip().replace("Z", "").replace("z", "")
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return calendar.timegm(_dt.datetime.strptime(s, fmt).timetuple())
        except ValueError:
            continue
    return None


def _premise_path_state(path):
    """Tri-state existence via os.stat: 'exists' | 'absent' | 'error'. Unlike
    os.path.exists (which maps every OSError — including PermissionError — to
    False), a permission / I-O error yields 'error' so the caller can treat it as
    uncheckable (D5 fail-open) rather than let an unreadable target masquerade as
    'absent' and false-falsify a premise."""
    try:
        os.stat(path)
        return "exists"
    except (FileNotFoundError, NotADirectoryError):
        return "absent"
    except OSError:
        return "error"


def _path_verdict(ok, detail_true, detail_false):
    """The two-sided half of a path check: grounded/falsified plus the detail
    that says which way it went.

    Both details are computed by the CALLER and passed in, rather than being
    formatted here from a template, because the three path modes phrase the same
    binary differently ("exists" / "is ABSENT", "is absent" / "EXISTS",
    "mtime(x) < iso" / "mtime(x) >= iso") and a shared template would either
    lose that wording or grow a mode parameter that re-creates the dispatch this
    helper exists to remove. Note the deliberate asymmetry the wording carries:
    the FALSIFYING side is SHOUTED in the first two modes, because that string
    is what an operator reads when the loop halts.

    This helper NEVER returns `uncheckable`: every uncheckable arm is a
    different KIND of outcome (no kdir, escape, stat error, unparseable bound)
    and belongs to the caller that can name it.
    """
    return {"verdict": "grounded" if ok else "falsified",
            "detail": detail_true if ok else detail_false}


def _eval_path_mtime_before(cand, rel, iso, exists):
    """The `path mtime-before <ISO> <rel>` arm — the ONLY time comparison in the
    system, and an AUTHOR-SUPPLIED one (INV-1: never `now()`, so the same plan
    against the same corpus always decides the same way).

    Three separate uncheckable arms, and collapsing any pair of them is the bug
    this shape exists to prevent: an ABSENT target has no mtime to compare, an
    UNPARSEABLE bound is an authoring error, and a stat that fails on the SECOND
    call (the tri-state probe already succeeded) is a genuine I/O fault. None of
    the three is a falsification, because none of them is evidence that the
    premise stopped holding.
    """
    if not exists:
        return {"verdict": "uncheckable",
                "detail": "%s absent (cannot compare mtime)" % rel}
    bound = _parse_iso_to_epoch(iso)
    if bound is None:
        return {"verdict": "uncheckable", "detail": "unparseable ISO %r" % iso}
    try:
        mt = os.stat(cand).st_mtime
    except OSError as exc:
        return {"verdict": "uncheckable", "detail": "stat failed: %s" % exc}
    return _path_verdict(mt < bound,
                         "mtime(%s) < %s" % (rel, iso),
                         "mtime(%s) >= %s" % (rel, iso))


def _eval_check_path(parsed, kdir):
    """Dispatch a parsed `path` check over the three closed modes.

    Argument ORDER differs by mode and that is the grammar, not an accident:
    `mtime-before` reads `<ISO> <rel>` so the bound sits beside the mode it
    qualifies, while `exists`/`absent` read `<rel>` alone. The parser has
    already enforced the arity, so indexing here is total.
    """
    mode = parsed["args"][0]
    if mode == "mtime-before":
        iso, rel = parsed["args"][1], parsed["args"][2]
    else:
        rel, iso = parsed["args"][1], None
    if not kdir:
        return {"verdict": "uncheckable", "detail": "no knowledge dir resolved"}
    base = os.path.abspath(kdir)
    cand = os.path.abspath(os.path.join(base, rel))
    if not (cand == base or cand.startswith(base + os.sep)):
        return {"verdict": "uncheckable",
                "detail": "path escapes the knowledge dir: %s" % rel}
    # Tri-state via os.stat (NOT os.path.exists, which collapses EVERY OSError —
    # incl. PermissionError on an unreadable ancestor — to False and would
    # false-falsify a premise). A permission/other error is uncheckable (D5
    # fail-open), never a halt.
    state = _premise_path_state(cand)
    if state == "error":
        return {"verdict": "uncheckable",
                "detail": "cannot stat %s (permission or I/O error)" % rel}
    exists = state == "exists"
    if mode == "exists":
        return _path_verdict(exists, "%s exists" % rel, "%s is ABSENT" % rel)
    if mode == "absent":
        return _path_verdict(not exists, "%s is absent" % rel,
                             "%s EXISTS" % rel)
    return _eval_path_mtime_before(cand, rel, iso, exists)


def _corpus_metric_recurrence_keys(logs):
    """FOLD 1 — how many normalised prompt keys reach MINER_RECURRENCE_N DISTINCT
    sessions AND pass the F-C relevance gate. int, or None when the SOURCE is
    unavailable.

    Reuses the SAME folds the miner emits with (`_parse_prompts_records`,
    `_transcript_norm_key`, `_transcript_recurrence_relevant`,
    `MINER_RECURRENCE_N`, `_transcript_recurrence_gate`) WITHOUT building any
    record, so grounding a premise can never mutate real miner state (INV-2).

    THE NONE/0 SPLIT IS THE CONTRACT, and there are three ways to reach None and
    one way to reach 0. An absent `prompts.log` and an unreadable one are both
    ABSENCES of the source. The bare `except Exception` around the fold is the
    third: `_parse_prompts_records` is tolerant of content, but a fold that
    raises has produced a PARTIAL tally, and returning a partial count as if it
    were a total is how a grounded premise gets falsified by a corpus fault. A
    prompts.log that is present and parses to nothing is a genuine 0.
    """
    logp = os.path.join(logs, "prompts.log")
    if not os.path.isfile(logp):
        return None   # source absent -> uncheckable (fail-open), not a 0
    try:
        with open(logp, "rb") as f:
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    gate = _transcript_recurrence_gate()
    sessions_by_key = {}
    try:
        for hdr, body in _parse_prompts_records(raw):
            if hdr.get("origin") != "human":
                continue
            b = (body or "").strip()
            if not b:
                continue
            key = _transcript_norm_key(b)
            if not key:
                continue
            sessions_by_key.setdefault(key, set()).add(hdr.get("sid") or "")
    except Exception:
        return None
    return sum(1 for key, sids in sessions_by_key.items()
               if len(sids) >= MINER_RECURRENCE_N
               and _transcript_recurrence_relevant(key, gate))


def _corpus_metric_error_records(kdir, logs):
    """FOLD 2 — how many ERR-status records the KB's OWN session live.jsonl files
    carry. int, or None when NOT ONE session live.jsonl exists.

    `any_live` is the whole reason this is not a plain sum: a KB with sessions
    but no live.jsonl anywhere has an UNAVAILABLE source, and reporting that as
    0 would falsify every `claude-error-records >= n` premise ever written — the
    exact D5 DoS the fail-open rule exists to prevent. A live.jsonl that exists
    and carries no ERR record is a genuine 0.

    The `isinstance(r, dict)` guard is not defensive tidiness: `_transcript_read_live`
    yields whatever each line decodes to, so a JSON array line would otherwise
    raise AttributeError inside a gate the loop pre-hook runs.
    """
    n, any_live = 0, False
    for sid in _transcript_list_session_ids(kdir):
        lpath = os.path.join(logs, "sessions", sid, "live.jsonl")
        if not os.path.isfile(lpath):
            continue
        any_live = True
        for r in _transcript_read_live(lpath):
            if isinstance(r, dict) and _transcript_is_err_status(
                    r.get("status")):
                n += 1
    return n if any_live else None


def _premise_corpus_metric_value(metric, kdir):
    """Compute a corpus metric from LIVE state via the PURE miner folds. INV-2:
    read-only — never mutates the real miner state (folds run over throwaway
    in-memory structures). INV-1: deterministic over a fixed corpus.

    Returns an int (a PRESENT-but-empty corpus is a genuine 0), or None when the
    corpus SOURCE is UNAVAILABLE (logs/ absent, the specific source file absent, or
    a read error) so the caller fails OPEN (D5) — a plan grounded at authoring can
    never DoS a later run just because its source log rotated away.

    SCOPE CAVEAT (deliberate — see the worklog `> DECISION:`): `claude-error-records`
    counts ERR records in the KB's OWN logs/sessions/<sid>/live.jsonl ONLY. It does
    NOT reconcile the durable $HOME transcripts (the F-B is_error reconstruction) —
    that source is machine-specific and non-reproducible, which INV-1 (deterministic,
    offline, hermetic) forbids in a decision. The metric therefore measures the
    KB-local, deterministic error-record count (sufficient to detect signal
    PRESENCE), not the reconciled corpus-wide total."""
    if not kdir:
        return None
    logs = os.path.join(kdir, "logs")
    if not os.path.isdir(logs):
        return None
    if metric == "recurrence-keys-at-n":
        return _corpus_metric_recurrence_keys(logs)
    if metric == "claude-error-records":
        return _corpus_metric_error_records(kdir, logs)
    return None


def _premise_num_cmp(a, op, b):
    return {">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b,
            "==": a == b, "!=": a != b}.get(op, False)


def _eval_check_corpus_metric(parsed, kdir):
    metric, op, n = parsed["args"][0], parsed["args"][1], int(parsed["args"][2])
    actual = _premise_corpus_metric_value(metric, kdir)
    if actual is None:
        return {"verdict": "uncheckable",
                "detail": "corpus metric %s unavailable (logs/ absent)" % metric}
    ok = _premise_num_cmp(actual, op, n)
    return {"verdict": "grounded" if ok else "falsified",
            "actual": actual, "op": op, "expected": n,
            "detail": "%s = %d; check `%s %s %d` %s"
                      % (metric, actual, metric, op, n,
                         "holds" if ok else "FAILS")}


def evaluate_premise(rec, kdir):
    """Evaluate ONE premise record against live state. Pure/read-only.
    Returns {verdict ∈ grounded|falsified|uncheckable, detail, ...}.

    THE FALLBACK PARSE, AND WHY THE IMPORT IS FUNCTION-LOCAL
    -------------------------------------------------------
    A record that arrives WITHOUT a usable `parsed` is parsed here. In the
    monolith that needed no import at all: `_parse_premise_check` and this
    function were two globals in ONE namespace. The strangler split put the
    parser in `ops.planpremises` (the trust boundary) and the evaluator here
    (the read-only I/O half) and left the fallback naming a symbol this module
    does not bind — so from the split until task 99 wave 14 it raised
    `NameError` instead of parsing. Nothing caught it because nothing in-tree
    can reach it: `plan_premises` stamps `parsed` on EVERY record it emits, so
    the only callers that take this arm are external ones reaching
    `CLI.evaluate_premise` with a hand-built record, which is exactly the shape
    the facade's `_EXPORTS` list promises still works.

    The import is FUNCTION-LOCAL and stays that way. `ops.planpremises` imports
    THIS module at module level, so a module-level import here would close an
    import cycle and fix the package's load order; a call-time edge cannot
    (R-MOD-02's documented escape hatch, the same one `ops/audit.py` uses).
    The module's security seam is unaffected: this half still never DECIDES what
    a premise may say — it asks the trust boundary, which is the only thing that
    parses, so a check bearing a shell metacharacter is still rejected there and
    still arrives as `__rejected__`.

    Note the `or`, which is load-bearing: a FALSY `parsed` (None, or an empty
    dict) re-parses rather than dispatching on a `kind` of None. Only a parse
    result carrying a kind short-circuits it.
    """
    from agentware_lib.ops.planpremises import _parse_premise_check
    parsed = rec.get("parsed") or _parse_premise_check(rec.get("check") or "")
    kind = parsed.get("kind")
    if kind == "__rejected__":
        return {"verdict": "uncheckable", "rejected": True,
                "detail": parsed.get("error")}
    if kind in ("__unknown__", "__malformed__"):
        return {"verdict": "uncheckable", "detail": parsed.get("error")}
    if kind == "cite-live":
        return _eval_check_cite_live(rec, kdir)
    if kind == "config-eq":
        return _eval_check_config_eq(parsed)
    if kind == "path":
        return _eval_check_path(parsed, kdir)
    if kind == "corpus-metric":
        return _eval_check_corpus_metric(parsed, kdir)
    return {"verdict": "uncheckable", "detail": "unhandled check kind %r" % kind}


# Explicit facade export list (public AND underscore-private) — the entrypoint
# binds these onto its own namespace via the re-export loop so external consumers
# (tests/_fixtures.load_cli, the dashboard sidecar) keep reaching them as CLI.<name>.
_EXPORTS = [
    "_premise_cite_resolves",
    "_eval_check_cite_live",
    "_eval_check_config_eq",
    "_parse_iso_to_epoch",
    "_premise_path_state",
    "_eval_check_path",
    "_premise_corpus_metric_value",
    "_premise_num_cmp",
    "_eval_check_corpus_metric",
    "evaluate_premise",
]
__all__ = list(_EXPORTS)
