"""agentware_lib.retrieval.recallcmd -- the `recall` verb: deterministic BM25
ranked retrieval CLI.

Owns: the canonical recall payload builder (build_recall_payload -- the single
source of truth shared with the MCP recall tool), the `recall` CLI handler
(cmd_recall, incl. the scope=work ephemeral-corpus path) and the `recall` parser
registrar (register_recall, declared in this module's `__verbs__` and resolved
from there by the assembler; handler+registrar travel together).

Does NOT own: the ranking core (recall_ranked/scope_entries_for_team ->
agentware_lib.retrieval.skillpin), the tokenizer/corpus builders (tokenize/
estimate_tokens/build_work_corpus -> agentware_lib.retrieval.corpus), Okapi BM25
scoring (bm25_scores -> agentware_lib.retrieval.bm25) or the gated default-strategy
resolver (active_recall_strategy -> agentware_lib.retrieval.ledger) -- all siblings
held via the _skillpin / _corpus / _bm25 / _ledger module aliases and referenced by
ATTRIBUTE so the bytecode global-resolve gate never walks a foreign sibling function
held in this module's __dict__. Nor the knowledge index -- cmd_recall imports it
LAZILY (`from agentware_lib.knowledge import index`) because retrieval->knowledge
must stay function-local (layer discipline).

Layer deps: retrieval -> core (config, dates; module level) + the sibling
retrieval.{skillpin,corpus,bm25,ledger} (by module alias, attribute reads) +
knowledge.index LAZILY (function-local, cmd_recall only). No ops/features/cli import.
_EXPORTS lists build_recall_payload + cmd_recall for the entrypoint by-name facade
(the MCP recall tool reaches build_recall_payload as a bare name; the flat legacy
oracles reach both as CLI.<name>); register_recall is intentionally excluded (importlib).
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import json
import os
import sys

import agentware_lib.core.config as config
import agentware_lib.core.dates as dates
import agentware_lib.knowledge.freshsignals as _fresh
from agentware_lib.knowledge.index import entry_last_verified
from agentware_lib.retrieval.acr import date_age_days
from agentware_lib.retrieval import bm25 as _bm25
from agentware_lib.retrieval import corpus as _corpus
from agentware_lib.retrieval import ledger as _ledger
from agentware_lib.retrieval import skillpin as _skillpin

def _freshness_fields(entry, kdir, as_of):
    """The 4 self-defending point-in-time fields (R-FAIL-08) for a recall/query
    result: abs_path (the entry's relative path anchored to the LIVE knowledge
    dir), created, last_verified (back-compat: == created when absent, via
    entry_last_verified), and age_days (whole days from last_verified to as_of;
    None when the date is unknown/invalid). Pure + read-only — computes, never
    mutates."""
    rel = entry.get("path") or ""
    lv = entry_last_verified(entry)
    return {
        "abs_path": os.path.join(kdir, rel) if rel else None,
        "created": entry.get("created"),
        "last_verified": lv,
        "age_days": date_age_days(lv, as_of),
    }


def _scoped_corpus(kdir, data, scoped_entries):
    """Apply the R-SEC-02 refusal and return `(data_view, entries, suppressed)`.

    Split out of `build_recall_payload` rather than inlined: that function sits
    against its declared branch ceiling, and folding the refusal in would have
    widened a debt row to cover branches this change introduced — which is the
    one thing the ratchet exists to prevent.

    The refusal runs BEFORE ranking so a poisoned entry can never be injected.
    `org lint` already REPORTS these, but reporting happens when a human runs the
    lint and injection happens now. SHARED entries only: that is where the trust
    boundary is (another team wrote them), and scanning the caller's own corpus
    would suppress every learning that legitimately quotes an injection string,
    including the ones ABOUT prompt injection.
    """
    from agentware_lib.knowledge import readguard as _readguard
    kept, suppressed = _readguard.filter_for_injection(kdir, scoped_entries)
    view = data
    if len(kept) != len(data.get("entries") or []):
        view = dict(data)
        view["entries"] = kept
    return view, kept, suppressed


def _budget_walk(ranked, kdir, token_budget, as_of, signals):
    """The greedy token-budget walk. Returns `(results, overflow, used)`.

    SKIP, NEVER BREAK (INV-5). An entry that would exceed the budget is recorded
    in `overflow` and the walk CONTINUES, so a smaller lower-ranked entry still
    fills the remaining budget — graceful degradation that reduces injected
    context without ever increasing it, and preserves rank order among the
    entries that are included. A `break` would pass every test whose oversized
    entry happens to rank last, which is why the fixtures put one in the middle.

    `overflow` stays POINTERS-ONLY by contract — id, path, category, score and
    size, never a body and never a judgment — so a count=0 or partial result is
    actionable instead of a silent miss, without smuggling the very content the
    budget refused.

    `signals` is the whole-index trust map, computed ONCE by the caller: it is a
    property of the index rather than of any result, and re-deriving it per row
    would re-walk every supersede edge N times. Both trust and freshness merge
    into the record AFTER ranking, so neither can reorder anything.
    """
    results, overflow, used = [], [], 0
    for entry, score, doc_text in ranked:
        cost = _corpus.estimate_tokens(doc_text)
        if used + cost > token_budget:
            overflow.append({
                "id": entry.get("id"),
                "path": entry.get("path"),
                "category": entry.get("category"),
                "score": round(score, 6),
                "estimated_tokens": cost,
            })
            continue
        used += cost
        rec = {
            "id": entry.get("id"),
            "path": entry.get("path"),
            "category": entry.get("category"),
            "score": round(score, 6),
            "summary": entry.get("summary") or "",
            "estimated_tokens": cost,
        }
        rec.update(_freshness_fields(entry, kdir, as_of))
        # Results only — `overflow` stays pointers-only by contract (id/path/
        # category/score/estimated_tokens; never a body, never a judgment).
        rec.update(_fresh._trust_record(signals, entry.get("id")))
        # Org provenance (additive keys only — recall payload schema stays a
        # superset; INV recall-payload-stability). Emitted only when present so a
        # flat-KB payload is byte-identical to today.
        for k in ("origin", "team", "author"):
            v = entry.get(k)
            if v:
                rec[k] = v
        results.append(rec)
    return results, overflow, used


def _team_scope_block(caller_team, team, all_teams, include_shared, team_scope):
    """The resolved team scope to publish in the payload, or None.

    None on a flat KB — where no entry carries a team facet and no scoping flag
    was passed — so a single-operator payload stays byte-identical to what it
    was before org mode existed (INV-1). The five-way disjunction is the whole
    definition of "org mode is in play here" and lives in ONE place for that
    reason: a caller that tested four of the five would publish a scope block
    for some invocations and not others, and the loop reads this key.
    """
    if not (caller_team or team or all_teams or include_shared or team_scope):
        return None
    return {
        "caller_team": caller_team,
        "team": team,
        "all_teams": bool(all_teams),
        "include_shared": bool(include_shared),
        "scope": team_scope,
    }


def build_recall_payload(kdir, data, query, top_k=5, token_budget=1500,
                         category=None, strategy=None, as_of=None,
                         team=None, all_teams=False, include_shared=False,
                         team_scope=None):
    """Build the canonical `recall --format json` payload dict. Read-only (INV-2).

    Single source of truth shared by `cmd_recall` and the MCP `recall` tool so
    both surfaces are byte-identical (INV-1): same greedy token-budget walk
    (skip-not-break graceful degradation), same `{id,path,category,score,summary,
    estimated_tokens}` records, same scoring via `recall_ranked`. `strategy=None`
    resolves the gated onboarding A/B default via `active_recall_strategy` exactly
    like bare `recall`; `as_of` defaults to today only at this boundary (PIN it for
    reproducible runs). Returns (payload, mode_notice); the caller decides what to
    do with the notice — cmd_recall writes it to stderr, the MCP tool drops it so
    JSON capture stays clean. Never mutates the KB/index (only reads).
    """
    if not as_of:
        as_of = dates._today()
    if strategy:
        resolved, mode_notice = strategy, ""
    else:
        resolved, mode_notice = _ledger.active_recall_strategy(kdir)
    # Org team-scoping: filter entries BEFORE build_corpus so
    # df/avgdl are computed over the in-scope sub-corpus (R2 perf fix) and only the
    # in-scope files are read. caller_team is inferred from the kdir path; a flat
    # non-org KB has no team facet -> scope_entries_for_team returns entries
    # unchanged -> byte-identical to today (INV-1).
    caller_team = config._infer_team_from_kdir(kdir)
    scoped_entries = _skillpin.scope_entries_for_team(
        data.get("entries") or [], caller_team, team=team, all_teams=all_teams,
        include_shared=include_shared, scope=team_scope)
    # R-SEC-02 refusal, applied BEFORE ranking so a poisoned entry can never be
    # injected. `org lint` already REPORTS these — but reporting happens when a
    # human runs the lint, and injection happens now. Shared entries only: that is
    # where the trust boundary is (another team wrote them), and scanning the
    # caller's own corpus would suppress every learning that legitimately quotes
    # an injection string, including the ones ABOUT prompt injection.
    scoped_data, scoped_entries, suppressed = _scoped_corpus(
        kdir, data, scoped_entries)
    ranked = _skillpin.recall_ranked(kdir, scoped_data, query, top_k=top_k,
                           category=category, strategy=resolved, as_of=as_of)
    # Trust surface (feature 260713-p3-trust-staleness): computed ONCE per
    # payload — `_freshness_fields` has no index access, and the supersede
    # reverse-map + stale window are whole-index facts, so re-deriving them per
    # result would re-walk every edge N times. Additive only: merged into each
    # result record AFTER ranking, so it can never reorder anything.
    signals = _fresh.trust_signals(data, as_of)
    results, overflow, used = _budget_walk(
        ranked, kdir, token_budget, as_of, signals)
    payload = {
        "query": query,
        "strategy": resolved,
        "as_of": as_of,
        "top_k": top_k,
        "token_budget": token_budget,
        # Reported, never silent: "3 results, 1 suppressed" is a different
        # statement from "3 results", and the case where an operator most
        # needs to know their corpus was tampered with is exactly the case
        # a silent filter hides. Absent when nothing was suppressed, so a
        # clean payload stays byte-identical to today.
        **({"suppressed": suppressed} if suppressed else {}),
        "category": category,
        "context_tokens": used,
        "count": len(results),
        "results": results,
        "overflow": overflow,
        "overflow_count": len(overflow),
    }
    # Surface the resolved team-scope only in org mode (additive; absent on flat KB).
    scope_block = _team_scope_block(caller_team, team, all_teams,
                                    include_shared, team_scope)
    if scope_block is not None:
        payload["team_scope"] = scope_block
    return payload, mode_notice

def _work_scope_ranked(work_corpus, query, top_k):
    """Rank the ephemeral work corpus: BM25, plan-status weighted, then sorted.

    The multiplier (feature 260710) downweights by the FEATURE's plan state —
    superseded/cancelled ×0.3, done ×0.7, draft/ready/running/MISSING ×1.0 — and
    siblings inherit their feature's value through the pseudo-entry. It is
    confined to `--scope work`: the default/curated ranking is byte-unchanged,
    which the gold-fixture gate holds.

    The three sorts run LAST-KEY-FIRST because Python's sort is stable, so the
    effective order is score desc, then created desc, then id asc — the reverse
    of the order they are written in. Zero-scoring entries are dropped AFTER the
    weighting, not before: a `done` feature scoring 0.0 and one that never
    matched are the same answer, but a live feature whose weighted score is
    still positive must survive.
    """
    scored = _bm25.bm25_scores(work_corpus, _corpus.tokenize(query))
    scored = [(e, s * e.get("status_mult", 1.0)) for (e, s) in scored]
    # Sort: score desc -> created desc -> id asc
    scored.sort(key=lambda es: es[0].get("id") or "")
    scored.sort(key=lambda es: es[0].get("created") or "", reverse=True)
    scored.sort(key=lambda es: es[1], reverse=True)
    scored = [(e, s) for (e, s) in scored if s > 0]
    if top_k:
        return scored[:top_k]
    return scored


def _work_scope_budget_walk(scored, work_corpus, token_budget):
    """The work-scope greedy budget walk. Returns `(results, overflow, used)`.

    Same SKIP-NOT-BREAK contract as the curated walk (INV-5) over a different
    record shape: a work document has no summary of its own, so its heading
    stands in, and `category` is the literal "work" because these entries are
    EPHEMERAL and were never indexed under one.
    """
    results, overflow, used = [], [], 0
    for entry, score in scored:
        doc_text = next((t for (e, t) in work_corpus
                         if e.get("id") == entry.get("id")), "")
        cost = _corpus.estimate_tokens(doc_text)
        if used + cost > token_budget:
            overflow.append({"id": entry.get("id"), "path": entry.get("path"),
                             "category": "work", "score": round(score, 6),
                             "estimated_tokens": cost})
            continue
        used += cost
        results.append({"id": entry.get("id"), "path": entry.get("path"),
                        "category": "work", "score": round(score, 6),
                        "summary": entry.get("title") or "",
                        "estimated_tokens": cost})
    return results, overflow, used


def _work_scope_payload(args, kdir, as_of):
    """Build the `--scope work` payload from an EPHEMERAL corpus of work/*.md.

    Never touches index.json — `work/` stays UNINDEXED, and that is the point:
    plans and worklogs are the operator's in-flight record, not curated
    knowledge, so they are ranked on demand and never promoted. An empty corpus
    is a well-formed payload with count 0 rather than an error: "no work
    documents matched" and "there is no work/ directory" are both answers a
    caller can act on, and neither is a failure of the command.
    """
    work_corpus = _corpus.build_work_corpus(kdir)
    results, overflow, used = [], [], 0
    if work_corpus:
        scored = _work_scope_ranked(work_corpus, args.query, args.top_k)
        results, overflow, used = _work_scope_budget_walk(
            scored, work_corpus, args.token_budget)
    return {"query": args.query, "strategy": "bm25", "scope": "work",
            "as_of": as_of, "top_k": args.top_k,
            "token_budget": args.token_budget, "category": None,
            "context_tokens": used, "count": len(results),
            "results": results, "overflow": overflow,
            "overflow_count": len(overflow)}


def _run_work_scope_recall(args, kdir, as_of):
    """The whole `--scope work` command: build, render, return the exit code.

    A second command inlined into `cmd_recall` is what this was — ten of that
    function's nineteen branches, sharing only its argument validation with the
    curated path and none of its ranking, its payload shape or its renderer.
    The work view is deliberately THINNER than the curated one (no age, no
    trust, no staleness footer): those fields describe a curated entry's
    provenance, and a plan document has none to report.
    """
    payload = _work_scope_payload(args, kdir, as_of)
    if args.format == "json":
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    print("recall (scope=work): query=%r  -> %d entries"
          % (args.query, len(payload["results"])))
    for r in payload["results"]:
        print("  [%.4f] %s" % (r["score"], r["id"]))
        print("    %s" % r["path"])
    return 0


def cmd_recall(args):
    """Deterministic BM25 ranked retrieval, token-budgeted (replaces MAIN.md dump).

    Returns the highest-ranked entries (id, path, score, summary) trimmed so the
    cumulative estimated token footprint stays <= --token-budget. The greedy pass
    walks ranked order and SKIPS (continue, not break) any entry that would exceed
    the budget, so smaller lower-ranked entries still fill the remaining budget
    (graceful degradation), preserving rank order among included entries (INV-5:
    reduce injected context, never increase it). Each skipped entry is recorded in
    an `overflow` pointer list (id/path/category/score/estimated_tokens — never a
    body) so a count=0 or partial result is actionable instead of a silent miss.
    Read-only (INV-2). --format json emits a stable schema for the loop.
    """
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    from agentware_lib.knowledge import index  # lazy: retrieval -> knowledge (layer discipline)
    data, err = index.load_index(kdir)
    if err is not None:
        sys.stderr.write("agentware: %s\n" % err)
        return 1
    if args.top_k < 0:
        sys.stderr.write("agentware: --top-k must be >= 0\n")
        return 1
    if args.token_budget < 0:
        sys.stderr.write("agentware: --token-budget must be >= 0\n")
        return 1
    as_of = getattr(args, "as_of", None) or dates._today()
    if not dates._is_valid_date(as_of):
        sys.stderr.write("agentware: --as-of must be a YYYY-MM-DD date\n")
        return 1

    # Resolve scope: default (None) = curated only (byte-unchanged from pre-scope);
    # 'curated' = same; 'work' = only work/ files; 'all' = both.
    scope = getattr(args, "scope", None)

    # For 'work' scope: build ephemeral corpus from work/*.md, score with BM25,
    # return ranked results (never touches index.json).
    if scope == "work":
        return _run_work_scope_recall(args, kdir, as_of)

    # Resolve the ranking strategy + build the canonical payload via the shared
    # builder (single source of truth; the MCP `recall` tool reuses it so both
    # surfaces are byte-identical, INV-1). An explicit --strategy/--acr always
    # wins; otherwise build_recall_payload resolves the gated A/B default.
    explicit_strategy = getattr(args, "strategy", None)
    payload, mode_notice = build_recall_payload(
        kdir, data, args.query, top_k=args.top_k,
        token_budget=args.token_budget, category=args.category,
        strategy=explicit_strategy, as_of=as_of,
        team=getattr(args, "team", None),
        all_teams=getattr(args, "all_teams", False),
        include_shared=getattr(args, "include_shared", False),
        team_scope=("shared" if getattr(args, "scope", None) == "shared" else None))
    if mode_notice:
        sys.stderr.write("agentware: %s\n" % mode_notice)
    strategy = payload["strategy"]
    results = payload["results"]
    overflow = payload["overflow"]
    used = payload["context_tokens"]

    if args.format == "json":
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0

    _print_recall_text(args, strategy, results, overflow, used)
    return 0


def _print_recall_text(args, strategy, results, overflow, used):
    """Render the human view of a recall result.

    Extracted from `cmd_recall` rather than inlined: that function carries a
    declared ceiling row, and restoring the full renderer (the live-anchored
    path, the age line, the trust line and the staleness footer) alongside the
    donor's org-scoping block would have pushed it past the row — widening a debt
    row to cover a change of mine is the one thing the ratchet exists to stop.

    Everything printed here is POINT-IN-TIME, which is why the footer is
    unconditional and appears exactly once per invocation rather than per result:
    a reader who acts on a stale id, path or version without re-checking is the
    failure R-FAIL-08 names, and repeating the warning per row would train them
    to skip it.
    """
    print("recall: query=%r  strategy=%s  top_k=%d  budget=%d  -> %d entr%s (~%d tokens)"
          % (args.query, strategy, args.top_k, args.token_budget, len(results),
             "y" if len(results) == 1 else "ies", used))
    if args.category:
        print("  category: %s" % args.category)
    for r in results:
        print("  [%.4f] %s" % (r["score"], r["id"]))
        # the LIVE-anchored absolute path, not the repo-relative one: a recall
        # result is read by an agent that may not share this process's cwd, and a
        # relative path silently resolves somewhere else for it (R-FAIL-08).
        print("    %s" % r["abs_path"])
        if r["summary"]:
            print("    %s" % r["summary"])
        print("    age: %s days (created %s, last_verified %s)"
              % ("?" if r.get("age_days") is None else r["age_days"],
                 r.get("created") or "?", r.get("last_verified") or "?"))
        print("    trust: %s%s"
              % (r.get("trust") or "current",
                 (" (superseded by %s)" % ", ".join(r["superseded_by"]))
                 if r.get("superseded_by") else ""))
    _print_recall_overflow(overflow)
    # ONE static footer per invocation, not per result: every value above is
    # point-in-time, and a reader who acts on a stale id/path/version without
    # re-checking is the failure R-FAIL-08 exists to name.
    print("  point-in-time values may be stale — verify against live state (R-FAIL-08)")


def _print_recall_overflow(overflow):
    """Name what the token budget SKIPPED, as pointers only.

    A budget that silently drops ranked entries makes "no result" and "the result
    did not fit" the same output, and only one of those means the knowledge is
    absent. So the skipped entries are listed — id, score, size and path, never a
    body — and a partial answer becomes actionable instead of a silent miss.
    """
    if not overflow:
        return
    print("  note: %d ranked entr%s exceeded the token budget and %s skipped "
          "(pointers only) — inspect with `agentware query --id <id>`:"
          % (len(overflow), "y" if len(overflow) == 1 else "ies",
             "was" if len(overflow) == 1 else "were"))
    for o in overflow:
        print("    [%.4f] %s (~%d tokens) %s"
              % (o["score"], o["id"], o["estimated_tokens"], o["path"]))


#: Verb declaration — the ONE place this verb is registered. Discovered by
#: `agentware_lib.verbs`, whose docstring explains the dunder name and the
#: sparse order key (the key IS the top-level --help position).
__verbs__ = (
    ("recall", 1800, "register_recall"),
)


def register_recall(sub, fmt_parent):
    # recall -----------------------------------------------------------------
    p_recall = sub.add_parser(
        "recall", parents=[fmt_parent],
        help="deterministic BM25 ranked retrieval, token-budgeted "
             "(budget-bounded output — never pipe through head)")
    p_recall.add_argument("query", help="free-text query to rank KB entries against")
    p_recall.add_argument("--top-k", type=int, default=5, dest="top_k",
                          help="max entries to return before budgeting (default: 5)")
    # 1500, NOT the donor's 10000 (surface-union adjudication, winner: local).
    # The retrieval ladder is TWO stages: R-RET-03 runs stage 1 at top-5 /
    # budget-1500, and R-RET-04 WIDENS to top-10 / budget-3000 when stage 1 is
    # insufficient. A 10000 default collapses that — stage 1 already returns more
    # than the widened stage would, so the escalation stops meaning anything and
    # every recall pays the widened context cost whether it needed it or not.
    p_recall.add_argument("--token-budget", type=int, default=1500,
                          dest="token_budget",
                          help="trim so cumulative est. tokens <= budget "
                               "(default: 1500); output is already "
                               "budget-bounded — never pipe recall through "
                               "`head`")
    p_recall.add_argument("--category", default=None,
                          help="restrict ranking to a single category")
    p_recall.add_argument("--strategy", choices=("bm25", "bm25+acr"),
                          default=None,
                          help="ranking strategy (default: the gated default — "
                               "bm25+acr iff the recorded ACR win gate passed, "
                               "else bm25; bm25+acr applies the ACR "
                               "provenance+freshness prior over BM25)")
    p_recall.add_argument("--acr", action="store_const", const="bm25+acr",
                          dest="strategy",
                          help="shorthand for --strategy bm25+acr (opt into the "
                               "ACR re-rank; reorders within the BM25 set only)")
    p_recall.add_argument("--as-of", default=None, dest="as_of",
                          help="YYYY-MM-DD date for ACR freshness decay "
                               "(default: today; PIN it for reproducible runs)")
    p_recall.add_argument("--scope", choices=("curated", "work", "all", "shared"),
                          default=None,
                          help="corpus scope: curated (indexed entries only, "
                               "default), work (plans/worklogs in work/), "
                               "all (both), shared (org-shared entries only). "
                               "work/ entries are ephemeral (never indexed).")
    # Org team-scoping. Default = caller-team knowledge ∪ shared
    # skills. These select broader scopes on demand. Unset => byte-identical to
    # a flat single-team KB (no entry carries a team facet -> no filtering).
    p_recall.add_argument("--team", default=None,
                          help="restrict recall to a single team's entries "
                               "('shared' selects org-shared entries).")
    # `--all-teams` is DELIBERATELY ABSENT (parity-manifest exclusion
    # `surface-flag:recall:--all-teams`, operator-approved). Fleet-wide recall
    # needs either an org-root-spanning index rebuild or an N-index merge, and
    # BM25 scores are NOT comparable across shards — df and avgdl are computed
    # per corpus, so merging ranked lists from several indexes produces an order
    # that looks authoritative and is arithmetically meaningless. Carrying the
    # flag across would not give a wrong ERROR, it would give a wrong ANSWER,
    # silently. The scoping FUNCTION still supports all_teams for callers that
    # have one corpus (see retrieval.skillpin.scope_entries_for_team); what is
    # withheld is the CLI promise that this can be done correctly today.
    p_recall.add_argument("--include-shared", action="store_true",
                          dest="include_shared",
                          help="add ALL org-shared knowledge (not just shared "
                               "skills) to the caller-team default scope.")
    p_recall.set_defaults(func=cmd_recall)


_EXPORTS = [
    "build_recall_payload",
    "cmd_recall",
]
