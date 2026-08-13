"""agentware_lib.ops.fleet.fleetreport -- the `fleet issues|impact` reporter (layer: ops).

Owns: the deterministic fleet observability REPORT surface -- cmd_fleet (the
`fleet issues` recurrence ranking over the committed terminal sink joined with the
rebuilt index's learning/decision tags, and the `fleet impact` correlational
before/after failure-rate delta at a date-or-steering-sha boundary), the
sufficiency floor that suppresses a delta computed from too few events
(FLEET_IMPACT_FLOOR_N), and the `fleet` argparse registrar register_fleet.
Does NOT own: the sink itself -- the shard paths, the tolerant reader and the
org-root resolver it aggregates over live in
agentware_lib.ops.fleet.fleetshard and are reached through that OWNER module so
monkeypatching them still propagates; the shared-write guard + org read guards
(agentware_lib.ops.fleet.fleetguard); the per-team template contract
(agentware_lib.ops.fleet.fleettemplates); the OLS primitive (agentware_lib.ops.alerts
-- imported as _alerts, a legal same-layer ops->ops edge); the index read
(agentware_lib.knowledge.index).
Layer deps: ops -> core (config/dates) + knowledge (index) + the SAME-LAYER
ops.alerts sibling + the SAME-PACKAGE sibling agentware_lib.ops.fleet.fleetshard.
All downward or sideways; nothing above ops imports it.
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

Determinism: read-only aggregation (INV-2) that degrades to empty rather than
fabricating before any sink data exists, and NEVER presents the before/after delta
as causal -- it reports n-per-side plus the correlational caveat (R-ORG-07).
"""

import json
import sys

import agentware_lib.core.config as config
import agentware_lib.core.dates as dates
import agentware_lib.core.frontmatter as frontmatter
import agentware_lib.knowledge.index as index
import agentware_lib.ops.alerts as _alerts
import agentware_lib.ops.fleet.fleetshard as fleetshard


FLEET_IMPACT_FLOOR_N = 5


def _fleet_top_learning_tags(kdir, team, limit=15):
    """The `fleet issues` tag histogram over learnings + decisions, ranked.

    Split out of `cmd_fleet` so the poisoned-index guard has a home that is not
    inside an already-over-cap function. Degrades to `[]` when there is no
    resolvable knowledge dir or no index yet — a fleet REPORT may never be the
    thing that fails.

    `tags` is read through `frontmatter._entry_list_field` because `tag_counts[t]`
    is an `unhashable type: 'dict'` crash on a poisoned index entry whose `tags`
    list carries a mapping, which every is-a-list guard passes.

    `_allow_rebuild=False` because `fleet` is a REPORT (INV-2, read-only): with the
    auto-rebuild recovery this load site would WRITE index.json while merely
    reporting on fleet events, and "degrades when there is no index yet" above
    would be false — it would build one instead.
    """
    data = {}
    if kdir:
        loaded, _err = index.load_index(kdir, _allow_rebuild=False)
        if isinstance(loaded, dict):
            data = loaded
    tag_counts = {}
    for ent in (data.get("entries") or []):
        if not isinstance(ent, dict):
            continue
        if team and ent.get("team") != team:
            continue
        if ent.get("category") not in ("learnings", "decisions"):
            continue
        for t in frontmatter._entry_list_field(ent, "tags"):
            tag_counts[t] = tag_counts.get(t, 0) + 1
    return sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]


def cmd_fleet(args):
    """`agentware fleet issues|impact` — deterministic fleet observability (Task 9).

    Aggregates the committed per-user terminal sink across ALL teams (one co-resident
    tree, no git fan-out). Pure/stdlib/deterministic; degrades to empty (never
    fabricates) before any sink data exists. Read-only (INV-2).
    """
    kdir = config.require_knowledge_dir()
    org_root = fleetshard._fleet_org_root(kdir) if kdir else config.resolve_org_root()
    if not org_root:
        sys.stderr.write("agentware fleet: no org root resolvable "
                         "(set AGENTWARE_ORG_ROOT or run inside a teams/<team> KB)\n")
        return 1
    events = fleetshard.fleet_read_all(org_root)
    since = getattr(args, "since", None)
    team = getattr(args, "team", None)

    def _in_window(e):
        if team and e.get("team") != team:
            return False
        if since and (e.get("ts") or "") < since:
            return False
        return True

    sub = getattr(args, "fleet_cmd", None)
    if sub == "issues":
        windowed = [e for e in events if _in_window(e)]
        # Top recurring failure outcomes (rank by recurrence).
        outcome_counts = {}
        for e in windowed:
            o = e.get("outcome") or "unknown"
            outcome_counts[o] = outcome_counts.get(o, 0) + 1
        ranked = sorted(outcome_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        # Top learning tags/error-signatures from the ONE rebuilt index (by team).
        top_tags = _fleet_top_learning_tags(kdir, team)
        payload = {
            "command": "issues", "since": since, "team": team,
            "n_events": len(windowed),
            "top_outcomes": [{"outcome": o, "count": c} for o, c in ranked],
            "top_learning_tags": [{"tag": t, "count": c} for t, c in top_tags],
        }
        if getattr(args, "format", "text") == "json":
            json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            print("fleet issues%s%s: %d events" % (
                (" since %s" % since) if since else "",
                (" team %s" % team) if team else "", len(windowed)))
            for r in payload["top_outcomes"]:
                print("  %-22s %d" % (r["outcome"], r["count"]))
            if top_tags:
                print("  top learning tags: " +
                      ", ".join("%s(%d)" % (t["tag"], t["count"])
                                for t in payload["top_learning_tags"][:8]))
        return 0

    if sub == "impact":
        # Causal before/after: split by ts (and/or steering_sha equality) at t0.
        t0 = since
        if not t0:
            sys.stderr.write("agentware fleet impact: --since <date|steering-sha> required\n")
            return 1
        # If t0 looks like a steering_sha, resolve its earliest ts as the boundary.
        boundary_ts = t0
        if not dates._is_valid_date(t0):
            shas_ts = sorted(e.get("ts") for e in events
                             if e.get("steering_sha") == t0 and e.get("ts"))
            if shas_ts:
                boundary_ts = shas_ts[0]
            else:
                sys.stderr.write("agentware fleet impact: steering_sha %r not found "
                                "in the sink\n" % t0)
                return 1
        before = [e for e in events if (e.get("ts") or "") < boundary_ts
                  and (not team or e.get("team") == team)]
        after = [e for e in events if (e.get("ts") or "") >= boundary_ts
                 and (not team or e.get("team") == team)]

        def _fail_rate(evs):
            if not evs:
                return None
            fails = sum(1 for e in evs if e.get("outcome") not in ("completed",))
            return fails / len(evs)

        nb, na = len(before), len(after)
        rb, ra = _fail_rate(before), _fail_rate(after)
        # OLS slope over daily failure counts split at t0 (same semantics as alerts).
        def _daily_points(evs):
            by_day = {}
            for e in evs:
                d = (e.get("ts") or "")[:10]
                if not d:
                    continue
                by_day.setdefault(d, [0, 0])
                by_day[d][1] += 1
                if e.get("outcome") not in ("completed",):
                    by_day[d][0] += 1
            days = sorted(by_day)
            return [(i, by_day[d][0]) for i, d in enumerate(days)]
        slope = _alerts._ols_slope(_daily_points(before + after))
        insufficient = (nb < FLEET_IMPACT_FLOOR_N or na < FLEET_IMPACT_FLOOR_N)
        payload = {
            "command": "impact", "since": t0, "boundary_ts": boundary_ts,
            "team": team, "n_before": nb, "n_after": na,
            "fail_rate_before": (round(rb, 4) if rb is not None else None),
            "fail_rate_after": (round(ra, 4) if ra is not None else None),
            "fail_rate_delta": (round(ra - rb, 4)
                                if (rb is not None and ra is not None) else None),
            "ols_slope_daily_fail": (round(slope, 4) if slope is not None else None),
            "insufficient_data": insufficient,
            "floor_n": FLEET_IMPACT_FLOOR_N,
            "caveat": ("correlational, not causal; confounders: team/task/model mix"),
        }
        if insufficient:
            payload["fail_rate_delta"] = None
        if getattr(args, "format", "text") == "json":
            json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            if insufficient:
                print("fleet impact: insufficient data (n_before=%d, n_after=%d; "
                      "floor=%d per side)" % (nb, na, FLEET_IMPACT_FLOOR_N))
            else:
                print("fleet impact since %s (t0=%s):" % (t0, boundary_ts))
                print("  n_before=%d fail_rate=%.4f  n_after=%d fail_rate=%.4f  "
                      "delta=%+.4f" % (nb, rb, na, ra, ra - rb))
                print("  OLS daily-fail slope: %s" %
                      (("%.4f" % slope) if slope is not None else "n/a"))
            print("  caveat: %s" % payload["caveat"])
        return 0

    sys.stderr.write("agentware fleet: unknown subcommand (use issues|impact)\n")
    return 1


#: Verb declaration — the ONE place this verb is registered. Discovered by
#: `agentware_lib.verbs`, whose docstring explains the dunder name and the
#: sparse order key (the key IS the top-level --help position).
__verbs__ = (
    ("fleet", 2300, "register_fleet"),
)


def register_fleet(sub, fmt_parent):
    # fleet ------------------------------------------------------------------
    # Org fleet observability. Reads the committed per-user sink
    # across teams/*/fleet/*.jsonl (one co-resident tree; no git fan-out).
    p_fleet = sub.add_parser(
        "fleet", parents=[fmt_parent],
        help="org fleet observability over committed terminal events "
             "(issues|impact); deterministic, read-only")
    fleet_sub = p_fleet.add_subparsers(dest="fleet_cmd")
    p_fleet_issues = fleet_sub.add_parser(
        "issues", parents=[fmt_parent],
        help="top recurring failure outcomes + top learning tags across teams")
    p_fleet_issues.add_argument("--since", default=None,
                                help="only events on/after YYYY-MM-DD")
    p_fleet_issues.add_argument("--team", default=None,
                                help="restrict to one team")
    p_fleet_issues.set_defaults(func=cmd_fleet)
    p_fleet_impact = fleet_sub.add_parser(
        "impact", parents=[fmt_parent],
        help="causal before/after failure-rate delta at a steering-sha|date t0")
    p_fleet_impact.add_argument("--since", default=None,
                                help="t0 as a YYYY-MM-DD date OR a steering_sha")
    p_fleet_impact.add_argument("--team", default=None,
                                help="restrict to one team")
    p_fleet_impact.set_defaults(func=cmd_fleet)


_EXPORTS = [
    'FLEET_IMPACT_FLOOR_N',
    'cmd_fleet',
]
