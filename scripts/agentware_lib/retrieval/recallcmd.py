"""agentware_lib.retrieval.recallcmd -- the `recall` verb: deterministic BM25
ranked retrieval CLI.

Owns: the canonical recall payload builder (build_recall_payload -- the single
source of truth shared with the MCP recall tool), the `recall` CLI handler
(cmd_recall, incl. the scope=work ephemeral-corpus path) and the `recall` parser
registrar (register_recall, resolved by the assembler via importlib from
_PARSER_REGISTRATION; handler+registrar travel together).

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
import sys

import agentware_lib.core.config as config
import agentware_lib.core.dates as dates
from agentware_lib.retrieval import bm25 as _bm25
from agentware_lib.retrieval import corpus as _corpus
from agentware_lib.retrieval import ledger as _ledger
from agentware_lib.retrieval import skillpin as _skillpin

def build_recall_payload(kdir, data, query, top_k=5, token_budget=10000,
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
    scoped_data = data
    if len(scoped_entries) != len(data.get("entries") or []):
        scoped_data = dict(data)
        scoped_data["entries"] = scoped_entries
    ranked = _skillpin.recall_ranked(kdir, scoped_data, query, top_k=top_k,
                           category=category, strategy=resolved, as_of=as_of)
    results = []
    overflow = []
    used = 0
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
        # Org provenance (additive keys only — recall payload schema stays a
        # superset; INV recall-payload-stability). Emitted only when present so a
        # flat-KB payload is byte-identical to today.
        for k in ("origin", "team", "author"):
            v = entry.get(k)
            if v:
                rec[k] = v
        results.append(rec)
    payload = {
        "query": query,
        "strategy": resolved,
        "as_of": as_of,
        "top_k": top_k,
        "token_budget": token_budget,
        "category": category,
        "context_tokens": used,
        "count": len(results),
        "results": results,
        "overflow": overflow,
        "overflow_count": len(overflow),
    }
    # Surface the resolved team-scope only in org mode (additive; absent on flat KB).
    if caller_team or team or all_teams or include_shared or team_scope:
        payload["team_scope"] = {
            "caller_team": caller_team,
            "team": team,
            "all_teams": bool(all_teams),
            "include_shared": bool(include_shared),
            "scope": team_scope,
        }
    return payload, mode_notice

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
        work_corpus = _corpus.build_work_corpus(kdir)
        if not work_corpus:
            payload = {"query": args.query, "strategy": "bm25", "scope": "work",
                       "as_of": as_of, "top_k": args.top_k,
                       "token_budget": args.token_budget, "category": None,
                       "context_tokens": 0, "count": 0, "results": [],
                       "overflow": [], "overflow_count": 0}
        else:
            query_tokens = _corpus.tokenize(args.query)
            scored = _bm25.bm25_scores(work_corpus, query_tokens)
            # Sort: score desc -> created desc -> id asc
            scored.sort(key=lambda es: es[0].get("id") or "")
            scored.sort(key=lambda es: es[0].get("created") or "", reverse=True)
            scored.sort(key=lambda es: es[1], reverse=True)
            scored = [(e, s) for (e, s) in scored if s > 0]
            if args.top_k:
                scored = scored[:args.top_k]
            results, overflow, used = [], [], 0
            for entry, score in scored:
                doc_text = next((t for (e, t) in work_corpus
                                 if e.get("id") == entry.get("id")), "")
                cost = _corpus.estimate_tokens(doc_text)
                if used + cost > args.token_budget:
                    overflow.append({"id": entry.get("id"), "path": entry.get("path"),
                                     "category": "work", "score": round(score, 6),
                                     "estimated_tokens": cost})
                    continue
                used += cost
                results.append({"id": entry.get("id"), "path": entry.get("path"),
                                "category": "work", "score": round(score, 6),
                                "summary": entry.get("title") or "",
                                "estimated_tokens": cost})
            payload = {"query": args.query, "strategy": "bm25", "scope": "work",
                       "as_of": as_of, "top_k": args.top_k,
                       "token_budget": args.token_budget, "category": None,
                       "context_tokens": used, "count": len(results),
                       "results": results, "overflow": overflow,
                       "overflow_count": len(overflow)}
        if args.format == "json":
            json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
            return 0
        print("recall (scope=work): query=%r  -> %d entries" % (args.query, len(payload["results"])))
        for r in payload["results"]:
            print("  [%.4f] %s" % (r["score"], r["id"]))
            print("    %s" % r["path"])
        return 0

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

    print("recall: query=%r  strategy=%s  top_k=%d  budget=%d  -> %d entr%s (~%d tokens)"
          % (args.query, strategy, args.top_k, args.token_budget, len(results),
             "y" if len(results) == 1 else "ies", used))
    if args.category:
        print("  category: %s" % args.category)
    for r in results:
        print("  [%.4f] %s" % (r["score"], r["id"]))
        print("    %s" % r["path"])
        if r["summary"]:
            print("    %s" % r["summary"])
    if overflow:
        print("  note: %d ranked entr%s exceeded the token budget and %s skipped "
              "(pointers only) — inspect with `agentware query --id <id>`:"
              % (len(overflow), "y" if len(overflow) == 1 else "ies",
                 "was" if len(overflow) == 1 else "were"))
        for o in overflow:
            print("    [%.4f] %s (~%d tokens) %s"
                  % (o["score"], o["id"], o["estimated_tokens"], o["path"]))
    return 0

def register_recall(sub, fmt_parent):
    # recall -----------------------------------------------------------------
    p_recall = sub.add_parser(
        "recall", parents=[fmt_parent],
        help="deterministic BM25 ranked retrieval, token-budgeted")
    p_recall.add_argument("query", help="free-text query to rank KB entries against")
    p_recall.add_argument("--top-k", type=int, default=5, dest="top_k",
                          help="max entries to return before budgeting (default: 5)")
    p_recall.add_argument("--token-budget", type=int, default=10000,
                          dest="token_budget",
                          help="trim so cumulative est. tokens <= budget (default: 10000)")
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
    p_recall.add_argument("--all-teams", action="store_true", dest="all_teams",
                          help="recall across EVERY team + shared (fleet-wide).")
    p_recall.add_argument("--include-shared", action="store_true",
                          dest="include_shared",
                          help="add ALL org-shared knowledge (not just shared "
                               "skills) to the caller-team default scope.")
    p_recall.set_defaults(func=cmd_recall)


_EXPORTS = [
    "build_recall_payload",
    "cmd_recall",
]
