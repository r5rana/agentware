"""agentware_lib.ops.fleet.fleetshard -- the committed fleet TELEMETRY SINK (layer: ops).

Owns: the steering-version join key (resolve_steering_sha -- git HEAD, else a
deterministic content hash of AGENTS.md + steering/**/*.md, never raises), the
committed per-USER sink paths (_fleet_dir_for/fleet_shard_path), the tolerant
fleet-wide reader (fleet_read_all), the terminal-event SCHEMA constants the sink
writes and org lint O12 validates (FLEET_TERMINAL_REQUIRED/LOOP_OUTCOME_ENUM),
the org-root resolver every aggregation and the shared-write guard share
(_fleet_org_root), and the EMIT path cmd_fleet_emit + its `fleet-emit` registrar
(append+stage ONE terminal event, best-effort, never blocks the loop).
Does NOT own: the shared-write governance guard + the org-lint injection/conflict
constants and read-denylist (agentware_lib.ops.fleet.fleetguard); the fleet
REPORTER `fleet issues|impact` (agentware_lib.ops.fleet.fleetreport); the
per-team template contract T1..T5 (agentware_lib.ops.fleet.fleettemplates); the
org-invariant lint that CONSUMES these constants (agentware_lib.ops.orglint); the
teams/registry.json family (agentware_lib.ops.team); the low-level git runners
(agentware_lib.core.gitio -- _git and _git_noninteractive_env are imported here BY
NAME so cmd_fleet_emit's staging call resolves and resolve_steering_sha's
`"_git_noninteractive_env" in globals()` probe stays TRUE).
Layer deps: ops -> core only (config/dates/orgkeys/routing/gitio/paths). No
sibling import inside this package: fleetshard is the package LEAF, which is what
keeps fleetguard -> fleetshard and fleetreport -> fleetshard acyclic.
Fan-in: 2 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

Determinism: fleet_read_all is read-only aggregation (INV-2); resolve_steering_sha
never raises (the sink must never block the loop) and runs its one git subprocess
with GIT_TERMINAL_PROMPT=0 via _git_noninteractive_env, OUTSIDE the recall path.
"""

import json
import os
import subprocess
import sys

import agentware_lib.core.config as config
import agentware_lib.core.dates as dates
import agentware_lib.core.orgkeys as orgkeys
import agentware_lib.core.routing as routing
from agentware_lib.core.gitio import _git, _git_noninteractive_env
from agentware_lib.core.paths import REPO_ROOT


def resolve_steering_sha():
    """The agentware package's steering version key (causal join key for fleet).

    Prefer the package repo's HEAD commit SHA (git -C REPO_ROOT rev-parse HEAD).
    Fall back to a content hash of AGENTS.md + steering/**/*.md when git is
    unavailable (e.g. a tarball checkout) — DETERMINISTIC across two independent
    checkouts of the same content. Returns a short hex string, or "unknown" on any
    failure (never raises; the sink must never block the loop). Pure/stdlib +
    one git subprocess OUTSIDE the recall path (moat preserved).
    """
    try:
        cp = subprocess.run(["git", "-C", REPO_ROOT, "rev-parse", "HEAD"],
                            capture_output=True, text=True,
                            env=_git_noninteractive_env() if "_git_noninteractive_env" in globals() else None)
        if cp.returncode == 0 and cp.stdout.strip():
            return cp.stdout.strip()[:12]
    except Exception:
        pass
    # Content-hash fallback (deterministic across checkouts).
    try:
        import hashlib  # stdlib, lazy
        h = hashlib.sha256()
        paths = [os.path.join(REPO_ROOT, "AGENTS.md")]
        steer = os.path.join(REPO_ROOT, "steering")
        if os.path.isdir(steer):
            for root, _dirs, files in os.walk(steer):
                for fn in sorted(files):
                    if fn.endswith(".md"):
                        paths.append(os.path.join(root, fn))
        for p in sorted(paths):
            try:
                with open(p, "rb") as f:
                    h.update(f.read())
            except OSError:
                continue
        return "sha256:" + h.hexdigest()[:12]
    except Exception:
        return "unknown"


def _fleet_dir_for(kdir):
    """The committed per-user telemetry sink dir for the KB at `kdir` (teams/<t>/fleet)."""
    return os.path.join(kdir, orgkeys.ORG_FLEET_DIR)


def fleet_shard_path(kdir, handle):
    """Absolute path to a caller's per-USER committed fleet shard <kdir>/fleet/<handle>.jsonl.

    Per-USER filenames never textually collide on rebase (R3/R4 fix). `handle` is
    sanitized to the safe [a-z0-9._-] alias charset; falls back to 'unknown'.
    """
    h = routing._normalize_user_handle(handle) if handle else None
    h = h or "unknown"
    return os.path.join(_fleet_dir_for(kdir), "%s.jsonl" % h)


def fleet_read_all(org_root):
    """All committed terminal events across teams/*/fleet/*.jsonl under org_root.

    READ-ONLY + tolerant (mirrors _read_metrics_jsonl): missing dir → [], malformed
    lines skipped, non-dict ignored. Each event is tagged with its team (from the
    folder path) if not already present. Used by the `fleet` reporter to aggregate
    fleet-wide without any git fan-out (one co-resident tree). Pure/stdlib.
    """
    out = []
    teams_dir = os.path.join(org_root, orgkeys.ORG_TEAMS_DIR)
    if not os.path.isdir(teams_dir):
        return out
    for team in sorted(os.listdir(teams_dir)):
        fdir = os.path.join(teams_dir, team, orgkeys.ORG_FLEET_DIR)
        if not os.path.isdir(fdir):
            continue
        for fn in sorted(os.listdir(fdir)):
            if not fn.endswith(".jsonl"):
                continue
            handle = fn[:-len(".jsonl")]
            try:
                with open(os.path.join(fdir, fn), "r", encoding="utf-8",
                          errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except ValueError:
                            continue
                        if not isinstance(obj, dict):
                            continue
                        obj.setdefault("team", team)
                        obj.setdefault("handle", handle)
                        out.append(obj)
            except OSError:
                continue
    return out


FLEET_TERMINAL_REQUIRED = ("event", "ts", "outcome", "steering_sha", "team")


# The UNION of both trees' loop-terminal vocabularies (plan task 34). O12 REJECTS
# any outcome outside this tuple, and the pre-hook org-lint gate exits 1 on an O12
# error — so a value the loop CAN emit but the enum does not name hard-blocks every
# subsequent run in team mode, permanently (the offending line stays in the
# append-only sink). The set is therefore a strict superset of everything either
# side can produce:
#   * donor:  completed / hit_max_iterations / post_hook_failure / pre_hook_abort /
#             blocked_on_ambiguity / sync_failed / unknown
#   * LOCAL-only, added here: `premise_falsified` (the R-GND-01 premise gate halt,
#     scripts/loop/gates-plan.sh) and `adversarial_review_blocked` (the R-EXEC-06
#     self-heal review block, agentware.sh) — both live set-points on this tree,
#     both absent donor-side.
#   * `quota_throttled` — a loop terminal the graph disposition table already
#     classifies (PAUSE, not a failure) and which no gate may reject if a lane
#     pause path ever emits it.
# tests.test_loop_emission RE-DERIVES the emitted set from the shell sources on
# every run and fails if this tuple stops covering it, so a new set-point cannot
# land without widening the enum first.
LOOP_OUTCOME_ENUM = ("completed", "hit_max_iterations", "post_hook_failure",
                     "pre_hook_abort", "blocked_on_ambiguity", "sync_failed",
                     "premise_falsified", "adversarial_review_blocked",
                     "quota_throttled", "unknown")


def _fleet_org_root(kdir):
    """Resolve the org root that CONTAINS this kdir's teams/ tree (for aggregation).

    From a teams/<team> kdir, the org root is two levels up. Falls back to
    resolve_org_root(), else None.
    """
    rp = None
    norm = os.path.normpath(kdir or "").replace(os.sep, "/")
    parts = norm.split("/")
    for i in range(len(parts) - 2, -1, -1):
        if parts[i] == orgkeys.ORG_TEAMS_DIR:
            rp = "/".join(parts[:i]) or "/"
            break
    return rp or config.resolve_org_root()


def cmd_fleet_emit(args):
    """Append+stage ONE committed terminal event to teams/<team>/fleet/<handle>.jsonl.

    Called from run_kb_sync BEFORE the KB commit (R3 fix — the committed sink must
    be staged inside the same commit, NOT written in the EXIT trap which fires
    after the push and would leave the tree permanently dirty). Best-effort: any
    failure returns 0 (never blocks/fails the loop). Reuses the terminal schema +
    stamps steering_sha (the causal join key) + team (from the kdir path) + the
    per-USER handle (shard filename never textually collides on rebase).
    """
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 0  # best-effort
    team = config._infer_team_from_kdir(kdir)
    if not team:
        # Not an org team dir (flat KB) → no committed sink (byte-identical to today).
        return 0
    handle = routing.resolve_user_handle() or os.environ.get("USER") or "unknown"
    event = {
        "event": "terminal",
        "ts": getattr(args, "ts", None) or dates._today(),
        "feature": getattr(args, "feature", None) or "",
        "outcome": getattr(args, "outcome", None) or "unknown",
        "steering_sha": getattr(args, "steering_sha", None) or resolve_steering_sha(),
        "team": team,
        "handle": handle,
        "iterations_used": int(getattr(args, "iterations_used", 0) or 0),
        "max": int(getattr(args, "max", 0) or 0),
        "tasks_total": int(getattr(args, "tasks_total", 0) or 0),
        "tasks_done": int(getattr(args, "tasks_done", 0) or 0),
        "promise_status": getattr(args, "promise_status", None) or "unknown",
    }
    path = fleet_shard_path(kdir, handle)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        return 0  # best-effort — never block the loop
    # Stage it so it rides the SAME KB commit. Best-effort. Use -f because the
    # fleet dir may sit under a broad *.jsonl ignore that is negated for the sink
    # (teams/*/fleet/*.jsonl) — force-add guarantees the committed sink is staged
    # even if a future gitignore ordering would otherwise skip it. Add by ABSOLUTE
    # path so it is unambiguous regardless of git's cwd.
    try:
        _git(kdir, "add", "-f", "--", path)
    except Exception:
        pass
    if getattr(args, "format", "text") == "json":
        json.dump({"emitted": True, "path": path, "event": event},
                  sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        print("fleet-emit: %s -> %s" % (event["outcome"], path))
    return 0


def register_fleet_emit(sub, fmt_parent):
    # fleet-emit (internal; called by run_kb_sync to write+stage the committed sink)
    p_fleet_emit = sub.add_parser(
        "fleet-emit", parents=[fmt_parent],
        help="append+stage ONE committed terminal event to the team fleet sink "
             "(called by the loop; best-effort)")
    p_fleet_emit.add_argument("--outcome", default="unknown")
    p_fleet_emit.add_argument("--feature", default="")
    p_fleet_emit.add_argument("--ts", default=None)
    p_fleet_emit.add_argument("--steering-sha", dest="steering_sha", default=None)
    p_fleet_emit.add_argument("--iterations-used", dest="iterations_used", default=0)
    p_fleet_emit.add_argument("--max", default=0)
    p_fleet_emit.add_argument("--tasks-total", dest="tasks_total", default=0)
    p_fleet_emit.add_argument("--tasks-done", dest="tasks_done", default=0)
    p_fleet_emit.add_argument("--promise-status", dest="promise_status", default="unknown")
    p_fleet_emit.set_defaults(func=cmd_fleet_emit)


_EXPORTS = [
    'resolve_steering_sha',
    '_fleet_dir_for',
    'fleet_shard_path',
    'fleet_read_all',
    'FLEET_TERMINAL_REQUIRED',
    'LOOP_OUTCOME_ENUM',
    '_fleet_org_root',
    'cmd_fleet_emit',
]
