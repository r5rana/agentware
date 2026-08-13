"""agentware_lib.ops.orglint -- org-level KB determinism enforcement (O1..O13).

Owns: the read-only org-invariant linter lint_org -- the O1..O13
mechanical rules over a co-resident org tree (per-team entry origin/team resolution,
cross-team contamination O13, merged-id collision O2, retracted-supersedes O4, the
shared-root rules O6/O7/O8/O9, the fleet-sink terminal-schema check O12, and the folded
per-team template contract T1..T5), its `org lint` handler cmd_org_lint, and the `org`
parser registrar register_org. A true ok:true NO-OP when AGENTWARE_ORG_ROOT is unset (a
flat single-team install is byte-identical to today; INV-1).

Does NOT own: the fleet telemetry/reporter + the injection/conflict CONSTANTS and helpers
it consumes (INJECTION_MARKERS / ORG_CONFLICT_MARKER_RE / FLEET_TERMINAL_REQUIRED /
LOOP_OUTCOME_ENUM / _org_read_denylist / lint_templates -> ops.fleet); the
teams/registry.json access family (load_registry / registry_membership_errors ->
ops.team); the KB blob-size cap KB_BLOB_MAX_BYTES (-> ops.staging); and plan lint
(-> ops.planlint).

Layer deps: ops.orglint -> core (config/frontmatter/orgkeys) DOWNWARD + knowledge.index
(scan_entry_files/load_index/index_path) DOWNWARD + the SAME-LAYER siblings ops.fleet
(as _fleet), ops.team (as _team) and ops.staging (as staging) -- all acyclic: none of
fleet/team/staging imports orglint. Nothing above ops imports orglint.

Determinism: lint_org is a read-only walk of the org tree (INV-2 tolerant); no mutation,
deterministic error ordering, sort_keys/indent JSON output in cmd_org_lint.

_EXPORTS lists the two org-lint entry points; register_org is NOT exported (it is declared
in this module's `__verbs__`). This module is dispatch-reached only
(the `org lint` verb), so it is NOT re-exported onto the scripts/agentware facade.
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import json
import os
import sys

import agentware_lib.core.config as config
import agentware_lib.core.frontmatter as frontmatter
import agentware_lib.core.orgkeys as orgkeys
import agentware_lib.knowledge.index as index
import agentware_lib.ops.fleet as _fleet
import agentware_lib.ops.staging as staging
import agentware_lib.ops.team as _team


def lint_org(kdir, strict=False):
    """Read-only org invariant lint. Returns {ok, errors[], warnings[]}.

    NO-OP (ok:true) when AGENTWARE_ORG_ROOT is unset — a flat install never engages
    org rules (INV-1). Otherwise runs O1..O13 over the co-resident org tree.
    """
    org_root = config.resolve_org_root()
    if not org_root:
        # Not org mode → true no-op (byte-identical to today).
        return {"ok": True, "errors": [], "warnings": [], "engaged": False}
    errors, warnings = [], []

    def err(rule, file, line, message):
        errors.append({"rule": rule, "file": file, "line": line, "message": message})

    teams_dir = os.path.join(org_root, orgkeys.ORG_TEAMS_DIR)
    team_names = []
    if os.path.isdir(teams_dir):
        team_names = sorted(d for d in os.listdir(teams_dir)
                            if os.path.isdir(os.path.join(teams_dir, d)))

    # O5 — registry.json well-formed + unique (user, team).
    reg = _team.load_registry(org_root)
    for m in _team.registry_membership_errors(reg):
        err("O5", orgkeys.ORG_REGISTRY_REL, 0, m)

    # Per-team entry scan (O1/O2/O4/O13) + merged-id collision (O2).
    merged_ids = {}  # team-qualified id -> (team, path)
    for team in team_names:
        tdir = os.path.join(teams_dir, team)
        for rel in index.scan_entry_files(tdir):
            fpath = os.path.join(tdir, rel)
            fm = frontmatter.read_entry_frontmatter(fpath)
            eid = fm.get("id")
            rel_disp = "%s/%s/%s" % (orgkeys.ORG_TEAMS_DIR, team, rel)
            # O1 — every entry resolves origin + team (frontmatter OR path-inference).
            origin, res_team = config._entry_origin_team(fm, tdir)
            if not origin or not res_team:
                err("O1", rel_disp, 0,
                    "entry does not resolve origin/team (O1): origin=%r team=%r"
                    % (origin, res_team))
            # O13 (operator direction) — an entry physically under teams/<T> whose
            # DECLARED team frontmatter disagrees with <T> is cross-team contamination.
            declared = fm.get("team")
            if declared and declared != team:
                err("O13", rel_disp, 0,
                    "entry under teams/%s declares team:%s (cross-team "
                    "contamination — an agent wrote outside its team)"
                    % (team, declared))
            # O2 — no team-qualified id collision in the merged view.
            if eid:
                key = "%s::%s" % (res_team or team, eid)
                if key in merged_ids:
                    err("O2", rel_disp, 0,
                        "team-qualified id collision: %s already at %s"
                        % (key, merged_ids[key][1]))
                merged_ids[key] = (team, rel_disp)
            # O4 — a retracted entry must declare supersedes.
            if fm.get("status") == "retracted" and not fm.get("supersedes"):
                err("O4", rel_disp, 0,
                    "status:retracted without a supersedes: pointer (O4)")

    # Shared-root rules O6/O7/O8/O9.
    shared_dir = os.path.join(org_root, orgkeys.ORG_SHARED_DIR)
    denylist = _fleet._org_read_denylist(org_root)
    if os.path.isdir(shared_dir):
        for root, _dirs, files in os.walk(shared_dir):
            for fn in sorted(files):
                if not fn.endswith(".md"):
                    continue
                fpath = os.path.join(root, fn)
                rel_disp = os.path.relpath(fpath, org_root)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                except OSError:
                    continue
                # O6 — no git conflict markers in shared *.md.
                for i, line in enumerate(content.splitlines(), 1):
                    if _fleet.ORG_CONFLICT_MARKER_RE.match(line):
                        err("O6", rel_disp, i, "git conflict marker in shared file")
                        break
                # O7 — shared blob <= KB_BLOB_MAX_BYTES.
                if len(content.encode("utf-8")) > staging.KB_BLOB_MAX_BYTES:
                    err("O7", rel_disp, 0, "shared blob exceeds %d bytes (O7)"
                        % staging.KB_BLOB_MAX_BYTES)
                # O8 — no injection markers in shared content.
                low = content.lower()
                for marker in _fleet.INJECTION_MARKERS:
                    if marker in low:
                        err("O8", rel_disp, 0,
                            "injection marker %r in shared content (O8)" % marker)
                        break
        # O9 — a denylisted shared id must not be recallable (present + indexed).
        if denylist:
            sidx, _e = index.load_index(shared_dir) if os.path.exists(index.index_path(shared_dir)) else ({}, None)
            for e in (sidx.get("entries") or []) if isinstance(sidx, dict) else []:
                if e.get("id") in denylist and e.get("status") != "retracted":
                    err("O9", e.get("path") or "?", 0,
                        "denylisted shared id %s still recallable (O9)" % e.get("id"))

    # O12 — fleet sink: valid terminal schema + steering_sha present, append-only NDJSON.
    for team in team_names:
        fdir = os.path.join(teams_dir, team, orgkeys.ORG_FLEET_DIR)
        if not os.path.isdir(fdir):
            continue
        for fn in sorted(os.listdir(fdir)):
            if not fn.endswith(".jsonl"):
                continue
            fpath = os.path.join(fdir, fn)
            rel_disp = "%s/%s/%s/%s" % (orgkeys.ORG_TEAMS_DIR, team, orgkeys.ORG_FLEET_DIR, fn)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    for i, line in enumerate(f, 1):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except ValueError:
                            err("O12", rel_disp, i, "invalid JSON in fleet sink line")
                            continue
                        missing = [k for k in _fleet.FLEET_TERMINAL_REQUIRED if k not in ev]
                        if missing:
                            err("O12", rel_disp, i,
                                "fleet event missing required keys: %s" % missing)
                        if ev.get("outcome") not in _fleet.LOOP_OUTCOME_ENUM:
                            err("O12", rel_disp, i,
                                "fleet outcome %r not in the LOOP_OUTCOME enum"
                                % ev.get("outcome"))
            except OSError:
                continue

    # Template contract (T1..T5, feature 260721 follow-up) — fold in per-team
    # template lint so `org lint` (run in the pre/post hooks) also catches a team
    # template that would produce org-invalid entries. T1/T2/T5 are hard errors;
    # the org-field warning (T3) stays a warning unless --strict.
    for team in team_names:
        tres = _fleet.lint_templates(os.path.join(teams_dir, team), strict=strict)
        for e in tres["errors"]:
            err(e["rule"], "%s/%s/%s" % (orgkeys.ORG_TEAMS_DIR, team, e["file"]),
                e["line"], e["message"])
        for w in tres["warnings"]:
            warnings.append({"rule": w["rule"],
                             "file": "%s/%s/%s" % (orgkeys.ORG_TEAMS_DIR, team, w["file"]),
                             "line": w["line"], "message": w["message"]})

    ok = len(errors) == 0
    return {"ok": ok, "errors": errors, "warnings": warnings, "engaged": True}

def cmd_org_lint(args):
    """`agentware org lint` — run lint_org; exit 1 on any error. No-op when unset."""
    strict = bool(getattr(args, "strict", False))
    as_json = getattr(args, "format", "text") == "json"
    result = lint_org(config.require_knowledge_dir(), strict=strict)
    if as_json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        if not result.get("engaged"):
            print("org lint: no-op (AGENTWARE_ORG_ROOT unset — flat install)")
            return 0
        print("ORG LINT %s (%d error(s), %d warning(s)):"
              % ("PASSED" if result["ok"] else "FAILED",
                 len(result["errors"]), len(result["warnings"])))
        for e in result["errors"]:
            print("  [%s] %s:%s — %s" % (e["rule"], e["file"], e["line"], e["message"]))
    return 0 if result["ok"] else 1


#: Verb declaration — the ONE place this verb is registered. Discovered by
#: `agentware_lib.verbs`, whose docstring explains the dunder name and the
#: sparse order key (the key IS the top-level --help position).
__verbs__ = (
    ("org", 3000, "register_org"),
)


def register_org(sub, fmt_parent):
    # org --------------------------------------------------------------------
    # Org-level KB determinism enforcement. `org lint` runs the
    # O1..O13 mechanical rules; a true no-op (ok:true) when AGENTWARE_ORG_ROOT unset.
    p_org = sub.add_parser("org", help="org-level KB tooling (lint)")
    org_sub = p_org.add_subparsers(dest="org_command", metavar="<subcommand>")
    org_sub.required = True
    p_org_lint = org_sub.add_parser(
        "lint", parents=[fmt_parent],
        help="enforce org invariants O1..O13 (no-op when AGENTWARE_ORG_ROOT unset)")
    p_org_lint.add_argument("--strict", action="store_true",
                            help="treat warnings as hard failures")
    p_org_lint.set_defaults(func=cmd_org_lint)


_EXPORTS = [
    'lint_org',
    'cmd_org_lint',
]
