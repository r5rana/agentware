"""agentware_lib.features.graphdag.graphconst -- every CONSTANT the DAG scheduler agrees on (layer: features).

Owns: the manifest schema tag and size/concurrency bounds; the edge kinds; the six
node STATES and the two derived sets (`_GRAPH_DEP_SATISFIED` /
`_GRAPH_DISPATCHABLE`); the four checkpoint DISPOSITIONS and the table that maps
a verdict to one; the node detail codes; the timeouts, deadlines and the tick
ceiling; the kill-switch variable names and the lane env allowlist; the kernel
serialize token; and the checkpoint log heading.
Does NOT own: any behaviour whatsoever. Nothing here reads a file, spawns a
process, or decides anything -- it is the shared vocabulary and nothing else.

WHY THE VOCABULARY IS A MODULE
------------------------------
A DAG scheduler is a state machine with a lot of surface: nine other modules ask
"is this node dispatchable?", "is this outcome terminal?", "does this verdict
halt the subtree?". Every one of those questions is answered by comparing against
a set defined here. Letting two modules keep their own copy of "which states are
done" is how a scheduler ends up dispatching a node twice, or hanging forever on
one it already finished.

THE BOUNDS ARE REFUSALS, NOT SUGGESTIONS
----------------------------------------
`GRAPH_MAX_NODES`, `GRAPH_CONCURRENCY_MIN`/`MAX`, `GRAPH_DEADLINE_HOURS`,
`GRAPH_NODE_TIMEOUT_HOURS` and `GRAPH_TICK_CEILING` exist because this scheduler
spawns real agent processes against a real repository. Every one of them bounds
something that would otherwise be unbounded: how much work can be queued, how
much runs at once, how long a node may hang before the watchdog calls it failed,
and how many ticks the driver may spin before it stops rather than loop forever.
An orchestrator without a tick ceiling does not fail loudly; it burns quota in a
loop nobody is watching.

`GRAPH_KILL_SWITCH_VARS` names the env variables that must be ABSENT for the
orchestrator to spawn anything at all -- the operator's off switch, checked before
work starts rather than after.

Layer deps: none. Pure data, stdlib only, no imports beyond what the literals
need. This is the deepest leaf of the graphdag package.
Determinism: module-level literals evaluated once at import (INV-1).
"""


GRAPH_MANIFEST_SCHEMA = 1


GRAPH_MAX_NODES = 32


GRAPH_CONCURRENCY_MIN = 1


GRAPH_CONCURRENCY_MAX = 16


GRAPH_DEFAULT_CONCURRENCY = 3


# Edge kinds — logical (successor consumes predecessor OUTPUT: pred must be
# done+merged first) and resource (mutual-exclusion on a shared write-set:
# ordering only). Matches the Task-1 `_PLAN_DEPENDS_KINDS`.
_GRAPH_EDGE_KINDS = ("logical", "resource")


# Node state vocabulary the scheduler consumes (Task 4 resolves live state into
# these). Only a done+merged node satisfies a dependent — for a LOGICAL edge the
# artifact is present, for a RESOURCE edge the lane is merged so the successor
# spins off a clean `integration` base. superseded/cancelled/failed/blocked leave
# a dependent PENDING (scheduling-only), so a fixed-and-re-run upstream releases
# it automatically (no `cancelled` ever written to the dependent).
GRAPH_NODE_PENDING = "pending"


GRAPH_NODE_RUNNING = "running"


GRAPH_NODE_DONE = "done"


GRAPH_NODE_FAILED = "failed"


GRAPH_NODE_BLOCKED = "blocked"


GRAPH_NODE_HALT = "halt"


_GRAPH_DEP_SATISFIED = frozenset({GRAPH_NODE_DONE})


_GRAPH_DISPATCHABLE = frozenset({GRAPH_NODE_PENDING})


# Graph dispositions — what a node's terminal signal does to the whole graph.
GRAPH_HALT = "HALT"                    # systemic indictment — stop everything


GRAPH_BLOCK_SUBTREE = "BLOCK_SUBTREE"  # contained — block only the successor set


GRAPH_CONTINUE = "CONTINUE"            # success — merge / advance


GRAPH_PAUSE = "PAUSE"                  # quota throttle — checkpoint + resume


# LOOP_OUTCOME (agentware.sh terminal `outcome`) + merge-queue result statuses +
# synthetic scheduler signals -> a disposition. `premise_falsified` is handled
# specially (load-bearing => HALT). Anything NOT in this table falls closed to
# HALT (R-AUTO default-halt; audit fail-closed requirement).
_GRAPH_DISPOSITION_TABLE = {
    # loop terminal outcomes (agentware.sh LOOP_OUTCOME) --------------------
    "completed": GRAPH_CONTINUE,
    "hit_max_iterations": GRAPH_BLOCK_SUBTREE,
    "post_hook_failure": GRAPH_BLOCK_SUBTREE,
    "pre_hook_abort": GRAPH_BLOCK_SUBTREE,
    "adversarial_review_blocked": GRAPH_HALT,   # R-EXEC-06 hard blocker
    "blocked_on_ambiguity": GRAPH_HALT,         # R-EXEC-06 hard blocker
    "quota_throttled": GRAPH_PAUSE,             # not a failure — pause+resume
    # merge-queue verdicts (cmd_fanout_merge_queue result `status`) ---------
    "merged": GRAPH_CONTINUE,
    "skipped": GRAPH_CONTINUE,
    "halt-package": GRAPH_HALT,                 # package monolith conflict/error
    "halt-kb": GRAPH_HALT,                      # KB prose conflict/error
    "halt-gate": GRAPH_HALT,                    # integrated-tree gate regression
    # synthetic scheduler signals ------------------------------------------
    "self_extension_reached": GRAPH_HALT,       # mandatory manual checkpoint
    "manifest_mutated": GRAPH_HALT,             # approved manifest was mutated
}


# Per-node wall-clock watchdog — DISTINCT from STALLED_CLAIM_HOURS=72 (the crash-
# recovery re-claim window). A node whose claim is older than this with NO terminal
# metric is a hung lane -> FAILED_WATCHDOG, so the scheduler blocks its subtree
# instead of waiting forever. Overridable via `graph run --node-timeout`.
GRAPH_NODE_TIMEOUT_HOURS = 24


# Precise detail tags carried alongside the coarse GRAPH_NODE_* status (which is
# all _graph_ready_set consumes). They preserve the exact live signal so the
# scheduler + `graph status` can act (finalize / halt / merge / re-report) without
# re-deriving.
GRAPH_DETAIL_DONE_UNFINALIZED = "done_unfinalized"


GRAPH_DETAIL_FAILED_WATCHDOG = "failed_watchdog"


GRAPH_DETAIL_DEGENERATE = "degenerate"


GRAPH_DETAIL_AWAITING_MERGE = "awaiting_merge"


GRAPH_DETAIL_PLAN_MISSING = "plan_missing"


# LOOP terminal outcomes that mean the loop FAILED but the plan HONESTLY stays
# `running` (failure is NEVER written as a plan state — the 72h stale-claim
# recovery relies on it). Contained -> the scheduler blocks only the subtree.
_GRAPH_CONTAINED_FAIL_OUTCOMES = frozenset({
    "hit_max_iterations", "post_hook_failure", "pre_hook_abort",
})


# The four merge/review kill-switches. They defend TWO boundaries:
#   1. graph run FAILS CLOSED at start if ANY is present-and-truthy in the
#      ORCHESTRATOR's own os.environ — the merge gates run IN-PROCESS via
#      `_run_gate` (which does `env = dict(os.environ)`), and `gate release`
#      honours AGENTWARE_DISABLE_RELEASE_GATE from that env, so a switch left
#      exported in the operator's shell would silently disable the integrated-tree
#      gate for every merge (audit HIGH — refuse, don't strip-and-proceed).
#   2. they can NEVER appear in a LANE subprocess env (the allow-list below never
#      copies them), so an unattended lane can never disable its own gates.
GRAPH_KILL_SWITCH_VARS = (
    "AGENTWARE_DISABLE_PREMISE_GATE",
    "AGENTWARE_DISABLE_TARGET_GATE",
    "AGENTWARE_DISABLE_RELEASE_GATE",
    "AGENTWARE_SKIP_ADVERSARIAL_REVIEW",
)


# Allow-list of env var NAMES copied from the orchestrator into a lane
# subprocess. The lane env is built ADDITIVELY from this set — never by
# inheriting os.environ — so the four kill-switches (and any other stray
# AGENTWARE_* toggle) can NEVER leak into an unattended lane. AGENTWARE_KNOWLEDGE_
# DIR is set explicitly per-lane to the lane's isolated KB worktree.
GRAPH_LANE_ENV_ALLOW = (
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE",
    "TERM", "TMPDIR", "SHELL", "SSH_AUTH_SOCK",
)


# Write-set token that trips kernel serialization: two lanes that both touch the
# governance monolith are NEVER co-dispatched (git auto-merges disjoint same-file
# hunks silently, so co-dispatch would rely solely on the post-merge gate — the
# scheduler is conservative and serializes them instead).
GRAPH_KERNEL_SERIALIZE_TOKEN = "scripts/agentware"


GRAPH_DEADLINE_HOURS = 48        # whole-run wall-clock bound (real-clock seam)


GRAPH_TICK_CEILING = 1000        # deterministic backstop vs a non-terminating loop


# --- Task 6: explicit manual checkpoint barrier ------------------------------
# The gated merge-queue is the AUTOMATIC per-node review; the `graph checkpoint`
# verb is the OPTIONAL EXPLICIT barrier. A `manual` checkpoint (MANDATORY on every
# self-extension node) accepts ONLY an operator verdict — the loop can never clear
# it (tier-3: the operator runs a self-ext node themselves under R-PKG-03). The
# verdict is recorded in an append-only `## Checkpoint log` on the node's plan.md
# (recompute-friendly, like `## Status log`); `abort-subtree` cancels the node's
# transitive successor set through the normal set-state matrix.
_PLAN_CHECKPOINT_LOG_HEADING = "## Checkpoint log"


_GRAPH_CHECKPOINT_VERDICTS = ("proceed", "hold", "abort-subtree")


_EXPORTS = [
    "GRAPH_MANIFEST_SCHEMA",
    "GRAPH_MAX_NODES",
    "GRAPH_CONCURRENCY_MIN",
    "GRAPH_CONCURRENCY_MAX",
    "GRAPH_DEFAULT_CONCURRENCY",
    "_GRAPH_EDGE_KINDS",
    "GRAPH_NODE_PENDING",
    "GRAPH_NODE_RUNNING",
    "GRAPH_NODE_DONE",
    "GRAPH_NODE_FAILED",
    "GRAPH_NODE_BLOCKED",
    "GRAPH_NODE_HALT",
    "_GRAPH_DEP_SATISFIED",
    "_GRAPH_DISPATCHABLE",
    "GRAPH_HALT",
    "GRAPH_BLOCK_SUBTREE",
    "GRAPH_CONTINUE",
    "GRAPH_PAUSE",
    "_GRAPH_DISPOSITION_TABLE",
    "GRAPH_NODE_TIMEOUT_HOURS",
    "GRAPH_DETAIL_DONE_UNFINALIZED",
    "GRAPH_DETAIL_FAILED_WATCHDOG",
    "GRAPH_DETAIL_DEGENERATE",
    "GRAPH_DETAIL_AWAITING_MERGE",
    "GRAPH_DETAIL_PLAN_MISSING",
    "_GRAPH_CONTAINED_FAIL_OUTCOMES",
    "GRAPH_KILL_SWITCH_VARS",
    "GRAPH_LANE_ENV_ALLOW",
    "GRAPH_KERNEL_SERIALIZE_TOKEN",
    "GRAPH_DEADLINE_HOURS",
    "GRAPH_TICK_CEILING",
    "_PLAN_CHECKPOINT_LOG_HEADING",
    "_GRAPH_CHECKPOINT_VERDICTS",
]

__all__ = list(_EXPORTS)
