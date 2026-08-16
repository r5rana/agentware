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


def _premise_cite_resolves(cite, kdir):
    """True iff `cite` resolves to a live KB entry (indexed id/path) OR a real file
    under the knowledge dir (work artifacts are NOT indexed, so an RCA under work/
    resolves by filesystem existence). Path traversal escaping the KB is refused
    (unresolvable). Read-only."""
    if not cite or not kdir:
        return False
    # INV-2: _allow_rebuild=False — a read-only verb must NEVER trigger load_index's
    # transparent index.json rebuild (which writes index.json / FEATURES.md /
    # rosters / the BM25 cache). A missing/corrupt index simply degrades to the
    # filesystem-existence fallback below.
    try:
        data, err = load_index(kdir, _allow_rebuild=False)
    except Exception:
        data, err = None, "load error"
    if err is None and isinstance(data, dict):
        for e in data.get("entries") or []:
            if isinstance(e, dict) and (e.get("id") == cite
                                        or e.get("path") == cite):
                return True
    try:
        base = os.path.abspath(kdir)
        cand = os.path.abspath(os.path.join(base, cite))
        if (cand == base or cand.startswith(base + os.sep)) \
                and os.path.exists(cand):
            return True
    except (ValueError, OSError):
        pass
    return False


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


def _eval_check_path(parsed, kdir):
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
        return {"verdict": "grounded" if exists else "falsified",
                "detail": "%s %s" % (rel, "exists" if exists else "is ABSENT")}
    if mode == "absent":
        return {"verdict": "grounded" if not exists else "falsified",
                "detail": "%s %s" % (rel, "is absent" if not exists else "EXISTS")}
    # mtime-before ISO — author-supplied bound, never now()
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
    ok = mt < bound
    return {"verdict": "grounded" if ok else "falsified",
            "detail": "mtime(%s) %s %s" % (rel, "<" if ok else ">=", iso)}


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
        # Reuse the SAME folds the miner emits with — _parse_prompts_records,
        # _transcript_norm_key, _transcript_recurrence_relevant, MINER_RECURRENCE_N,
        # _transcript_recurrence_gate — counting keys that reach N DISTINCT sessions
        # AND pass the F-C relevance gate, WITHOUT building records (no side effects).
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
    if metric == "claude-error-records":
        # Count ERR-status records across the KB's own live.jsonl files (the fold
        # _transcript_is_err_status). KB-scoped -> hermetic + deterministic. If NO
        # session live.jsonl exists at all, the source is unavailable -> None
        # (uncheckable / fail-open), never a 0 that could falsify a grounded premise.
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
    Returns {verdict ∈ grounded|falsified|uncheckable, detail, ...}."""
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
