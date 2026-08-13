"""agentware_lib.features.fanout -- parallel-lane orchestration: lane primitives, provisioning, merge-policy, list, spin-up, gate runner (layer: features).

Owns: the fanout lane-registry primitives (load_lanes / save_lanes / _fanout_dir /
_fanout_lanes_path / _fanout_lane_paths / _resolve_repo_arg / _branch_exists), lane
PROVISIONING (_fanout_provision_lane), the KB merge-policy installer + its .gitattributes
managed block (_fanout_gitattributes_block / _strip_managed_block / _install_merge_policy /
cmd_fanout_merge_policy), lane OUTCOME reporting (_count_open_plan_markers / _lane_outcome /
cmd_fanout_list), the single post-merge GATE runner (_run_gate), the lane SPIN-UP command
(cmd_fanout_spin_up), and the fanout constants (FANOUT_FEATURE_RE / FANOUT_MERGE_UNION_PATHS /
FANOUT_GITATTR_BEGIN / FANOUT_GITATTR_END).

Does NOT own: the merge-queue + teardown orchestration (_is_ancestor / _ensure_integration_branch /
_merge_in_progress / _union_resolve_conflict / _lane_repo_args / _lanes_common_repo /
_mq_integrate_package / _mq_integrate_kb / _mq_emit / _teardown_blocker / _remove_worktree /
cmd_fanout_teardown / cmd_fanout_merge_queue) + the `fanout` parser registrar (register_fanout)
+ the mq-only constants (FANOUT_APPEND_ONLY_PATHS / FANOUT_DEFAULT_GATES) -> these build ON these
lane primitives and are extracted to agentware_lib.features.fanoutmq in a later seam (fanoutmq
imports THIS module DOWNWARD as a sibling; this module imports NOTHING from fanoutmq -- a clean
dependency root). The low-level git runner (_git) -> core.gitio; the atomic writer
(_atomic_write) -> ops.planauthor; the open-plan-marker regex (_PLAN_OPEN_MARKER_PY) ->
ops.planlint; the worklog scanner (scan_worklog) -> knowledge.worklog; git work-tree state
(git_is_work_tree) -> ops.kbgit; the repo root (REPO_ROOT) -> core.paths; config resolution
(HOME_CONFIG / resolve_knowledge_dir) -> core.config; index load/rebuild (load_index / rebuild_kb)
-> knowledge.index.

Layer deps: features.fanout -> core (config/gitio/paths) + knowledge (index/worklog) + ops
(kbgit/planauthor/planlint) -- ALL DOWNWARD for the features layer. No sibling features import,
no cli, no retrieval, no upward edge.

The scripts/agentware entrypoint re-exports the full _EXPORTS BY NAME so the resident
merge-queue/teardown handlers + register_fanout + cmd_update (_run_gate) + load_cli tests resolve
them as bare names. Cross-module FUNCTIONS are reached via MODULE aliases (kbgit. / planauthor. /
worklog.), never by-name imports (whose foreign __code__ the global-resolve gate would walk
against this namespace and FAIL); _git is the gitio by-name leaf-primitive exception (+ subprocess
for the gate), and _PLAN_OPEN_MARKER_PY / REPO_ROOT are by-name CONSTANTS (no __code__, gate-safe).

Determinism: pure orchestration -- drives git worktrees + spawns the EXISTING loop + runs the
EXISTING gates; NEVER edits KB entry content, NEVER touches ranking/retrieval (the moat is
untouched). Conflict resolution on derived/append-only KB files is DETERMINISTIC (union /
regenerate), never LLM. INV-1 deterministic.
Fan-in: 4 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import json
import os
import re
import subprocess  # noqa: F401 -- resolves by-name gitio._git's LOAD_GLOBAL 'subprocess' (global-resolve gate)
import sys  # noqa: F401

import agentware_lib.core.config as config
import agentware_lib.knowledge.index as index
import agentware_lib.knowledge.worklog as _worklog
import agentware_lib.ops.kbgit as kbgit
import agentware_lib.ops.planauthor as _planauthor
from agentware_lib.core.gitio import _git
from agentware_lib.core.paths import REPO_ROOT
from agentware_lib.ops.planlint import _PLAN_OPEN_MARKER_PY


FANOUT_FEATURE_RE = re.compile(r"^[A-Za-z0-9_-]+$")


#: Append-only artifacts every lane writes to, so a concurrent-lane merge unions
#: them instead of conflicting. Plan task 70 REMOVED the seventh entry,
#: `scripts/agentware_lib/_registration.py`: the single-file verb registry it
#: named no longer exists, because each module now declares its own verbs in
#: `__verbs__` (see agentware_lib.verbs). Two lanes adding two verbs now edit two
#: DIFFERENT files, so there is nothing left to union — the merge driver was
#: treating a symptom of the centralised registry, and the registry is gone.
FANOUT_MERGE_UNION_PATHS = (
    "benchmarks/history.jsonl",
    "benchmarks/SCORECARD.md",
    "index.json",
    "FEATURES.md",
    "**/index.md",
)


FANOUT_GITATTR_BEGIN = "# >>> agentware fanout merge-policy >>>"


FANOUT_GITATTR_END = "# <<< agentware fanout merge-policy <<<"


def _fanout_dir():
    """Base state dir for fanout (env-overridable for hermetic tests).

    Default: ~/.agentware/fanout (sibling of config.env). AGENTWARE_FANOUT_DIR
    overrides it so tests never touch the operator's real ~/.agentware state.
    """
    override = os.environ.get("AGENTWARE_FANOUT_DIR")
    if override and override.strip():
        return os.path.abspath(os.path.expanduser(override.strip()))
    return os.path.join(os.path.dirname(config.HOME_CONFIG), "fanout")


def _fanout_lanes_path():
    return os.path.join(_fanout_dir(), "lanes.json")


def load_lanes():
    """Load the fanout lane registry. Returns (data, error).

    A missing registry is the EMPTY registry (not an error). On success `data`
    is always `{"lanes": {<feature>: <record>}}`.
    """
    path = _fanout_lanes_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"lanes": {}}, None
    except (OSError, json.JSONDecodeError) as exc:
        return None, "invalid fanout registry %s: %s" % (path, exc)
    if not isinstance(data, dict) or not isinstance(data.get("lanes"), dict):
        return None, ("malformed fanout registry %s (expected {\"lanes\": {...}})"
                      % path)
    return data, None


def save_lanes(data):
    """Persist the lane registry canonically (atomic, 2-space, sorted, newline)."""
    _planauthor._atomic_write(
        _fanout_lanes_path(),
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def _resolve_repo_arg(value, default):
    """Absolute path from an optional --repo/--kb-repo value, else the default."""
    if value:
        return os.path.abspath(os.path.expanduser(value))
    return default


def _fanout_lane_paths(feature, repo, kb_repo):
    """Deterministic per-lane branch names + worktree/env paths."""
    base = _fanout_dir()
    wt_root = os.path.join(base, "worktrees", feature)
    return {
        "feature": feature,
        "repo": repo,
        "kb_repo": kb_repo,
        "feat_branch": "feat/%s" % feature,
        "kb_branch": "kb/%s" % feature,
        "worktree": os.path.join(wt_root, "pkg"),
        "kb_path": os.path.join(wt_root, "kb"),
        "env_file": os.path.join(base, "env", "%s.env" % feature),
    }


def _branch_exists(repo, branch):
    """True iff a local branch ref exists in `repo`."""
    return _git(repo, "rev-parse", "--verify", "--quiet",
                "refs/heads/%s" % branch).returncode == 0


def _fanout_provision_lane(data, lane, base, kb_base, dry):
    """Create one lane's worktrees + env file + registry entry.

    Returns (action, error). action ∈ {created, exists, would-create}; on any
    failure error is a message and action is None. Idempotent and clobber-safe:
    an intact registered lane is a no-op; a pre-existing branch/worktree this
    command does not own is REFUSED rather than overwritten.
    """
    feature = lane["feature"]
    existing = data["lanes"].get(feature)

    # Idempotent: a registered lane whose worktrees still exist is a no-op
    # success — we never clobber/recreate it.
    if existing:
        if (os.path.isdir(existing.get("worktree", "")) and
                os.path.isdir(existing.get("kb_path", ""))):
            return "exists", None
        return None, ("lane already registered but its worktrees are missing; "
                      "run `fanout teardown %s --force` then re-run" % feature)

    repo, kb_repo = lane["repo"], lane["kb_repo"]
    # Clobber guard: refuse a branch/worktree this command did not register.
    for r, b in ((repo, lane["feat_branch"]), (kb_repo, lane["kb_branch"])):
        if _branch_exists(r, b):
            return None, ("branch %s already exists in %s (refusing to clobber)"
                          % (b, r))
    for p in (lane["worktree"], lane["kb_path"]):
        if os.path.exists(p):
            return None, "worktree path already exists: %s (refusing to clobber)" % p

    if dry:
        return "would-create", None

    # Package worktree on feat/<f>.
    cp = _git(repo, "worktree", "add", "-b", lane["feat_branch"],
              lane["worktree"], base)
    if cp.returncode != 0:
        return None, "git worktree add (package) failed: %s" % cp.stderr.strip()

    # KB worktree on kb/<f>. On failure roll back the package worktree so a
    # half-provisioned lane never lingers.
    cp = _git(kb_repo, "worktree", "add", "-b", lane["kb_branch"],
              lane["kb_path"], kb_base)
    if cp.returncode != 0:
        _git(repo, "worktree", "remove", "--force", lane["worktree"])
        _git(repo, "branch", "-D", lane["feat_branch"])
        return None, "git worktree add (KB) failed: %s" % cp.stderr.strip()

    # Per-lane env file pinning AGENTWARE_KNOWLEDGE_DIR to the KB worktree.
    env_text = (
        "# agentware fanout lane env — feature %s\n"
        "# `source` (or `--main-prompt`-pass) this before `./agentware.sh %s`\n"
        "# from the lane's package worktree to pin the isolated KB.\n"
        "AGENTWARE_KNOWLEDGE_DIR=%s\n"
        % (feature, feature, lane["kb_path"]))
    _planauthor._atomic_write(lane["env_file"], env_text)

    data["lanes"][feature] = {
        "feature": feature,
        "feat_branch": lane["feat_branch"],
        "kb_branch": lane["kb_branch"],
        "worktree": lane["worktree"],
        "kb_path": lane["kb_path"],
        "env_file": lane["env_file"],
        "repo": repo,
        "kb_repo": kb_repo,
        "base": base,
        "merged": False,
    }
    save_lanes(data)
    return "created", None


def _fanout_gitattributes_block():
    """The managed merge=union block (delimited so it can be re-installed idempotently)."""
    lines = [FANOUT_GITATTR_BEGIN]
    lines += ["%s merge=union" % p for p in FANOUT_MERGE_UNION_PATHS]
    lines.append(FANOUT_GITATTR_END)
    return "\n".join(lines)


def _strip_managed_block(text):
    """Remove a prior managed merge-policy block, preserving all other lines."""
    if FANOUT_GITATTR_BEGIN not in text:
        return text
    out, skip = [], False
    for line in text.splitlines():
        if line.strip() == FANOUT_GITATTR_BEGIN:
            skip = True
            continue
        if line.strip() == FANOUT_GITATTR_END:
            skip = False
            continue
        if not skip:
            out.append(line)
    return "\n".join(out)


def _install_merge_policy(kb_repo):
    """Idempotently install the merge=union `.gitattributes` block. Returns (changed, error)."""
    if not kbgit.git_is_work_tree(kb_repo):
        return False, "%s is not a git work tree" % kb_repo
    path = os.path.join(kb_repo, ".gitattributes")
    existing = ""
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            existing = f.read()
    stripped = _strip_managed_block(existing)
    parts = []
    if stripped.strip():
        parts.append(stripped.rstrip("\n"))
    parts.append(_fanout_gitattributes_block())
    new_text = "\n".join(parts) + "\n"
    if new_text == existing:
        return False, None
    _planauthor._atomic_write(path, new_text)
    return True, None


def cmd_fanout_merge_policy(args):
    """Install the idempotent merge=union `.gitattributes` into the KB repo.

    Append-only files (`benchmarks/history.jsonl`, `benchmarks/SCORECARD.md`) and
    derived files (`index.json`, `FEATURES.md`, `<sub>/index.md`) get
    `merge=union`, so concurrent-lane conflicts in them resolve DETERMINISTICALLY
    — never by LLM. Derived files are regenerated post-merge by `index rebuild`
    (the merge-queue), so a union'd-but-stale derived file is always overwritten
    with the canonical union from frontmatter. Idempotent; scoped to the KB repo.
    """
    fmt_json = getattr(args, "format", "text") == "json"
    kb_repo = _resolve_repo_arg(getattr(args, "kb", None),
                                config.resolve_knowledge_dir())
    if not kb_repo:
        sys.stderr.write("fanout merge-policy: no KB repo (pass --kb or set "
                         "AGENTWARE_KNOWLEDGE_DIR).\n")
        return 1
    changed, err = _install_merge_policy(kb_repo)
    if err:
        sys.stderr.write("fanout merge-policy: %s\n" % err)
        return 1
    path = os.path.join(kb_repo, ".gitattributes")
    if fmt_json:
        json.dump({"kb": kb_repo, "gitattributes": path, "changed": changed,
                   "patterns": list(FANOUT_MERGE_UNION_PATHS)},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("fanout merge-policy: %s %s"
              % ("installed" if changed else "already current", path))
    return 0


def _count_open_plan_markers(plan_path):
    """Count open (⬜/🟡) task markers in a plan.md via the canonical matcher."""
    try:
        with open(plan_path, encoding="utf-8") as f:
            return sum(1 for line in f if _PLAN_OPEN_MARKER_PY.match(line))
    except OSError:
        return None


def _lane_outcome(rec):
    """Read-only inference of a lane's loop outcome.

    The plan referenced a `derive_outcome` that does NOT exist in this codebase
    (R-FAIL-08), so this infers the outcome deterministically from the lane's
    isolated KB worktree — never mutating anything:

      merged      — already integrated by the merge-queue (registry flag).
      missing     — a worktree is gone.
      provisioned — lane exists but its plan.md has not been seeded yet.
      in_progress — open plan markers remain AND no `.loop/.done` sentinel.
      blocked     — finished but the worklog still has unpromoted markers
                    (zero-knowledge-loss not yet satisfied).
      completed   — finished and clean.
    """
    if rec.get("merged"):
        return "merged"
    kb_path = rec.get("kb_path", "")
    worktree = rec.get("worktree", "")
    if not (os.path.isdir(kb_path) and os.path.isdir(worktree)):
        return "missing"
    feature = rec.get("feature", "")
    work_dir = os.path.join(kb_path, "work", feature)
    open_n = _count_open_plan_markers(os.path.join(work_dir, "plan.md"))
    done = os.path.isfile(os.path.join(work_dir, ".loop", ".done"))
    if open_n is None and not done:
        return "provisioned"
    if not (done or open_n == 0):
        return "in_progress"
    # Finished — enforce the zero-knowledge-loss view if a worklog exists.
    worklog = os.path.join(work_dir, "worklog.md")
    if os.path.isfile(worklog):
        # READ-ONLY contract (INV-2): use the NON-rebuilding load so a missing/corrupt
        # lane index is never auto-repaired here (no write side-effect on inference).
        data, err = index.load_index(kb_path, _allow_rebuild=False)
        if err is None:
            try:
                unpromoted, _total = _worklog.scan_worklog(worklog, kb_path, data)
                if unpromoted:
                    return "blocked"
            except OSError:
                pass
    return "completed"


def cmd_fanout_list(args):
    """List active lanes with their branch, KB path, and inferred loop outcome."""
    fmt_json = getattr(args, "format", "text") == "json"
    data, err = load_lanes()
    if err:
        sys.stderr.write("fanout list: %s\n" % err)
        return 1
    lanes = []
    for feature in sorted(data["lanes"]):
        rec = data["lanes"][feature]
        lanes.append({
            "feature": feature,
            "feat_branch": rec.get("feat_branch"),
            "kb_branch": rec.get("kb_branch"),
            "kb_path": rec.get("kb_path"),
            "worktree": rec.get("worktree"),
            "merged": bool(rec.get("merged")),
            "outcome": _lane_outcome(rec),
        })
    if fmt_json:
        json.dump({"lanes": lanes}, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        if not lanes:
            print("fanout list: no active lanes.")
        else:
            for l in lanes:
                print("%-28s %-14s %-12s %s"
                      % (l["feature"], l["feat_branch"], l["outcome"],
                         l["kb_path"]))
    return 0


def _run_gate(cmd, repo, kb_repo):
    """Run one post-merge gate (shell) in `repo` with the integration KB pinned.

    Each gate is an operator-default / operator-supplied (trusted) command, so
    shell=True is acceptable here. AGENTWARE_KNOWLEDGE_DIR is pinned to the
    INTEGRATION KB so `eval --record` writes ledger rows THERE, never into a lane
    KB. Returns (rc, combined_output).
    """
    env = dict(os.environ)
    env["AGENTWARE_KNOWLEDGE_DIR"] = kb_repo
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        cp = subprocess.run(cmd, shell=True, cwd=repo, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)
        return cp.returncode, cp.stdout or ""
    except Exception as exc:  # pragma: no cover - defensive
        return 1, str(exc)


def cmd_fanout_spin_up(args):
    """Provision one or more isolated lanes (package + KB worktrees + env file).

    Per feature: a package git worktree on feat/<f> off --base, an isolated KB
    git worktree on kb/<f> off --kb-base, and a per-lane env file pinning
    AGENTWARE_KNOWLEDGE_DIR to the KB worktree. Idempotent (an intact existing
    lane is a no-op), refuses to clobber a branch/worktree it does not own, and
    --dry-run prints the plan and writes nothing.
    """
    fmt_json = getattr(args, "format", "text") == "json"
    dry = getattr(args, "dry_run", False)

    repo = _resolve_repo_arg(getattr(args, "repo", None), REPO_ROOT)
    kb_repo = _resolve_repo_arg(getattr(args, "kb_repo", None),
                                config.resolve_knowledge_dir())
    if not kb_repo:
        sys.stderr.write("fanout spin-up: no KB repo (pass --kb-repo or set "
                         "AGENTWARE_KNOWLEDGE_DIR).\n")
        return 1
    base = getattr(args, "base", None) or "HEAD"
    kb_base = getattr(args, "kb_base", None) or base

    for label, path in (("package", repo), ("KB", kb_repo)):
        if not kbgit.git_is_work_tree(path):
            sys.stderr.write("fanout spin-up: %s repo is not a git work tree: "
                             "%s\n" % (label, path))
            return 1

    data, err = load_lanes()
    if err:
        sys.stderr.write("fanout spin-up: %s\n" % err)
        return 1

    results, rc = [], 0
    for feature in args.features:
        if not FANOUT_FEATURE_RE.match(feature):
            sys.stderr.write("fanout spin-up: invalid feature name %r "
                             "(allowed: [A-Za-z0-9_-]).\n" % feature)
            rc = 1
            continue
        lane = _fanout_lane_paths(feature, repo, kb_repo)
        action, lerr = _fanout_provision_lane(data, lane, base, kb_base, dry)
        if lerr:
            sys.stderr.write("fanout spin-up: %s: %s\n" % (feature, lerr))
            rc = 1
            results.append({"feature": feature, "action": "error", "error": lerr})
            continue
        results.append({
            "feature": feature, "action": action,
            "feat_branch": lane["feat_branch"], "kb_branch": lane["kb_branch"],
            "worktree": lane["worktree"], "kb_path": lane["kb_path"],
            "env_file": lane["env_file"],
        })

    if fmt_json:
        json.dump({"dry_run": dry, "lanes": results}, sys.stdout,
                  ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        for r in results:
            if r["action"] == "error":
                continue
            tag = "[dry-run] would create" if dry else r["action"]
            print("fanout spin-up: %s %s" % (r["feature"], tag))
            print("  feat worktree: %s (%s)" % (r["worktree"], r["feat_branch"]))
            print("  kb   worktree: %s (%s)" % (r["kb_path"], r["kb_branch"]))
            print("  env file:      %s" % r["env_file"])
    return rc


_EXPORTS = [
    "FANOUT_FEATURE_RE",
    "FANOUT_MERGE_UNION_PATHS",
    "FANOUT_GITATTR_BEGIN",
    "FANOUT_GITATTR_END",
    "_fanout_dir",
    "_fanout_lanes_path",
    "load_lanes",
    "save_lanes",
    "_resolve_repo_arg",
    "_fanout_lane_paths",
    "_branch_exists",
    "_fanout_provision_lane",
    "_fanout_gitattributes_block",
    "_strip_managed_block",
    "_install_merge_policy",
    "cmd_fanout_merge_policy",
    "_count_open_plan_markers",
    "_lane_outcome",
    "cmd_fanout_list",
    "_run_gate",
    "cmd_fanout_spin_up",
]
