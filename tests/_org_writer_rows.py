"""The ROSTER: every local-only CLI surface node, classified. Task 66.

Three buckets, and every local-only node from the generated parity manifest is
in exactly one of them (`tests/e2e/test_org_local_writers_e2e.py` asserts the
partition, so a verb added later cannot join an unenumerated set):

  WRITERS          probed live in org mode — writes the knowledge dir, so it has
                   a team shard to land in or leak out of.
  UNPROBED_WRITERS writes the knowledge dir but cannot be probed by SUBPROCESS
                   from the suite, with the reason stated per row. Not a
                   loophole: a row here has to say what makes it unprobeable,
                   and the count is asserted, so the bucket cannot quietly grow.
  NON_WRITERS      does not write the knowledge dir at all. Two kinds, and the
                   distinction matters — a pure READER has nothing to scope, and
                   a CONFIG-HOME writer is machine-local state that is not team
                   knowledge and is deliberately not sharded.

The `setup` callables build prerequisites through the REAL CLI wherever the
artifact has a schema (a graph manifest, an indexed entry), and by hand only
where the file is a flat append log. Hand-building a manifest would make the
probe agree with a fixture rather than with the product.
"""

from tests._org_writers import (  # noqa: F401  (re-export)
    COMMIT, LOCAL, Ctx, Writer, no_setup)

import json

#: A plan that satisfies `plan claim`'s readiness lint. A raw `plan new`
#: skeleton does NOT: claim refuses an unfilled placeholder, which is the right
#: behaviour and means the probe has to supply a real one.
READY_PLAN = """# Probe — feature plan

> Feature: `%s`
> Status: draft

## Context

A scratch plan used by the org-mode writer probe. It exists so `plan claim` and
`plan set-state` have a real state machine to move, not a skeleton to refuse.

## Target packages

- `scripts/agentware_lib`

## Tasks

- ⬜ **1** [e2e] Prove the writer probe runs against a real plan.
  *Verify:* `true`
- ⬜ **2** [kb] Knowledge-base update.
  *Verify:* `true`

## Acceptance criteria

- [ ] the probe plan is claimable
"""

#: A legacy plan with NO `> Status:` header — the only shape `plan
#: state-backfill` has anything to do. Without it the verb exits 0 having
#: stamped nothing, and the probe would prove that a no-op writes nowhere.
LEGACY_PLAN = "# Legacy — feature plan\n\n> Feature: `%s`\n\n## Tasks\n\n- ⬜ **1** x\n"

#: One prompts.log record, in the exact format the queue scanner parses.
PROMPT_LOG = ("[2026-08-19T00:00:00Z] [session S1] [cwd /probe] "
              "[origin=human] [turn=1]\n"
              "Always prefer explicit timeouts on outbound network calls.\n\n")

#: A minimal queue record. `approve`/`reject` need exactly `status` +
#: `workorder`, so anything more would be decoration asserted by accident.
QUEUE_LINE = json.dumps({"status": "proposed", "workorder": "auto/pref-probe",
                         "text": "always pin dependency versions"}) + "\n"

#: A durable preference the admission classifier ACCEPTS. Phrasing matters:
#: `prefer capture "use tabs"` exits 0 having captured nothing ("not a durable
#: preference"), so a probe built on it would assert an empty write set.
PREF_TEXT = "always pin dependency versions"
PREF_ID = "learn-pref-global-always-pin-dependency-versions"
PREF_REL = "learnings/pref-global-always-pin-dependency-versions.md"

#: An adversarial-review audit artifact with ONE confirmed LOW finding: enough
#: for `review-report` to render a report, append a call-out and emit a
#: follow-up workorder, and not enough to make it a blocking round.
AUDIT = {
    "model": "opus", "effort": "max", "fanout": 1,
    "dimensions": ["correctness"],
    "findings": [{"dimension": "test-quality", "severity": "low",
                  "file": "docs/loop.md", "line": 7,
                  "summary": "tidy a noisy log line",
                  "failure_scenario": "a verbose log line clutters the transcript",
                  "verdict": "confirmed"}],
    "critic": {"gaps": [], "additional_findings": []},
    "confirmed_blocking": 0, "malformed": 0,
}

GRAPH = "260819-probegraph"
NODE_A = "260819-alpha"
NODE_B = "260819-beta"


# --- setup callables ------------------------------------------------------

def s_prompts(ctx):
    ctx.write("logs/prompts.log", PROMPT_LOG)


def s_queue(ctx):
    ctx.write("logs/steering/prefer-queue.jsonl", QUEUE_LINE)


def s_captured_pref(ctx):
    ctx.must(["prefer", "capture", PREF_TEXT, "--global"])


def s_two_entries(ctx):
    for topic in ("alpha-thing", "beta-thing"):
        ctx.must(["learn", "--topic", topic, "--summary", "s",
                  "--tags", "a,b", "--content", "c"])


def s_session(ctx):
    """One error->recovery session pair: the miner arm that is live in prod."""
    err = {"tool": "Read", "status": "ERR",
           "input": json.dumps({"file_path": "/w/x.md"}),
           "response": "No such file or directory"}
    ok = dict(err, status="ok", response="ok")
    ctx.write("logs/sessions/S1/live.jsonl",
              json.dumps(err) + "\n" + json.dumps(ok) + "\n")
    ctx.write("logs/sessions/S1/main.jsonl", json.dumps({"type": "user"}) + "\n")


def s_ready_plan(ctx):
    ctx.write("work/%s/plan.md" % NODE_A, READY_PLAN % NODE_A)


def s_legacy_plan(ctx):
    ctx.write("work/%s/plan.md" % NODE_B, LEGACY_PLAN % NODE_B)


def s_graph_new(ctx):
    """An empty graph manifest plus the plan its first node will reference."""
    s_ready_plan(ctx)
    ctx.must(["graph", "new", GRAPH])


def s_graph(ctx):
    """A graph manifest with one node, built through the real verbs."""
    s_graph_new(ctx)
    ctx.must(["graph", "add-node", GRAPH, NODE_A])


def s_graph_two(ctx):
    s_graph(ctx)
    ctx.write("work/%s/plan.md" % NODE_B, READY_PLAN % NODE_B)
    ctx.must(["graph", "add-node", GRAPH, NODE_B])


def s_graph_edge(ctx):
    s_graph_two(ctx)
    ctx.must(["graph", "add-edge", GRAPH, NODE_A, NODE_B])


def s_second_operator(ctx):
    """Name a SECOND person in the caller's shard, so `checkup` has a REAL
    deterministic candidate to emit a workorder for.

    `checkup` is classified COMMIT because a fired candidate emits a
    `work/auto/<feature>/plan.md` workorder — the report itself lands under
    `logs/`, which git never offers, so the workorder IS the committable
    artifact. Until plan task 89 that candidate arrived by ACCIDENT: the
    multi-tenancy signal counted the placeholder `author` default 'operator' as a
    second identity, so every bare shard fired it and this row passed without
    anyone aiming it. Fixing that false positive removed the accident, and with
    it the row's only committable output — the probe was proving containment over
    a write it had not asked for. So ask for it: a genuinely two-handle knowledge
    base (the caller's own handle, plus a different person named in MAIN.md) is
    what the signal is FOR, and the row now fires it deliberately.
    """
    ctx.write("MAIN.md", "# Team KB\n\n- **Handle**: someone-else\n")


def s_worklog(ctx):
    ctx.write("work/%s/worklog.md" % NODE_A, "# Worklog\n")


def s_review_round(ctx):
    s_worklog(ctx)
    ctx.write("review-state/adversarial-review-round-1.json",
              json.dumps(AUDIT))


# --- the roster -----------------------------------------------------------

def _w(node, argv, setup, writes, commits=COMMIT):
    return Writer(node=node, argv=argv, setup=setup, writes=writes,
                  commits=commits)


WRITERS = (
    _w("checkup", ["checkup"], s_second_operator,
       ["logs/checkup/checkup-latest.md"]),
    _w("migrate", ["migrate", "--apply"], no_setup,
       [".schema", "logs/migrations.jsonl"]),
    _w("relate", ["relate", "learn-alpha-thing", "--supersedes",
                  "learn-beta-thing"], s_two_entries,
       ["learnings/alpha-thing.md", "index.json"]),
    _w("tick", ["tick"], no_setup, ["logs/metrics.jsonl"], LOCAL),
    _w("prefer.capture", ["prefer", "capture", PREF_TEXT, "--global"],
       no_setup, [PREF_REL, "index.json"]),
    _w("prefer.mine", ["prefer", "mine", "--format", "json"], s_session,
       ["logs/steering/.transcript-miner.state.json"], LOCAL),
    _w("prefer.scan-queue", ["prefer", "scan-queue", "--sid", "S1",
                             "--cwd", "/probe"], s_prompts,
       ["logs/steering/prefer-queue.jsonl",
        "logs/steering/.prefer-queue.cursor.json"], LOCAL),
    _w("prefer.seed-cursor", ["prefer", "seed-cursor", "--sid", "S-seed"],
       no_setup, ["logs/steering/.prefer-queue.cursor.json"], LOCAL),
    _w("prefer.approve", ["prefer", "approve", "auto/pref-probe"], s_queue,
       ["logs/steering/prefer-queue.jsonl"], LOCAL),
    _w("prefer.reject", ["prefer", "reject", "auto/pref-probe",
                         "--reason", "probe"], s_queue,
       ["logs/steering/prefer-queue.jsonl"], LOCAL),
    _w("prefer.forget", ["prefer", "forget", PREF_ID], s_captured_pref,
       ["logs/steering/forgotten/" + PREF_REL.split("/")[-1], "index.json"], LOCAL),
    _w("plan.claim", ["plan", "claim", NODE_A], s_ready_plan,
       ["work/%s/plan.md" % NODE_A]),
    _w("plan.set-state", ["plan", "set-state", NODE_A, "cancelled",
                          "--reason", "probe"], s_ready_plan,
       ["work/%s/plan.md" % NODE_A]),
    _w("plan.state-backfill", ["plan", "state-backfill"], s_legacy_plan,
       ["work/%s/plan.md" % NODE_B]),
    _w("graph.new", ["graph", "new", GRAPH], no_setup,
       ["work/graphs/%s/graph.json" % GRAPH]),
    _w("graph.add-node", ["graph", "add-node", GRAPH, NODE_A], s_graph_new,
       ["work/graphs/%s/graph.json" % GRAPH]),
    _w("graph.add-edge", ["graph", "add-edge", GRAPH, NODE_A, NODE_B],
       s_graph_two, ["work/graphs/%s/graph.json" % GRAPH]),
    _w("graph.rm-edge", ["graph", "rm-edge", GRAPH, NODE_A, NODE_B],
       s_graph_edge, ["work/graphs/%s/graph.json" % GRAPH]),
    _w("graph.rm-node", ["graph", "rm-node", GRAPH, NODE_B], s_graph_two,
       ["work/graphs/%s/graph.json" % GRAPH]),
    _w("graph.checkpoint", ["graph", "checkpoint", GRAPH, NODE_A,
                            "--verdict", "proceed"], s_graph,
       ["work/%s/plan.md" % NODE_A]),
    _w("graph.cancel", ["graph", "cancel", GRAPH], s_graph,
       ["work/%s/plan.md" % NODE_A]),
    _w("review", ["review", "--adversarial", "--worklog",
                  "@work/%s/worklog.md" % NODE_A], s_worklog,
       ["work/%s/worklog.md" % NODE_A]),
    _w("review-report", ["review-report", "--state-dir", "@review-state",
                         "--feature-dir", "@work/%s" % NODE_A,
                         "--worklog", "@work/%s/worklog.md" % NODE_A,
                         "--feature", NODE_A, "--outcome", "clean"],
       s_review_round, ["work/%s/adversarial-review.md" % NODE_A]),
)

#: Migration 002's out-of-shard write set — REMOVED at task 98, which scoped the
#: migration to the caller's own shard. What stood here was `LEAKY = "migrate"`
#: plus a `KNOWN_OUTSIDE_SHARED` tuple: a shrink-AND-grow ratchet over the exact
#: set `migrate --apply` wrote outside `teams/<caller>/`, held deliberately as a
#: two-directional pin so that fixing the leak would redden it and bring task 98's
#: implementer here rather than leaving a stale exemption behind. It did exactly
#: that. `migrate` is now an ordinary row in WRITERS above, carried by the
#: whole-roster containment rule like every other writer, and the characterization
#: test that pinned the leak became
#: `MigrateStaysInsideTheCallersShardTest` — the same probe, asserting containment
#: outright.
#:
#: This comment stays because the roster is where someone looks to ask "was
#: `migrate` ever exempt from the containment rule?", and the answer being "yes,
#: until task 98, and here is what it wrote" is worth more than the absence.

#: Writes the knowledge dir, but not probeable as a subprocess from the suite.
UNPROBED_WRITERS = {
    "graph.run": (
        "the executor bootstraps an `integration` branch in the PACKAGE repo "
        "before wave 1 (`_graph_bootstrap_integration`), so a subprocess probe "
        "would mutate the checkout under test. Covered in-process instead, with "
        "that seam monkeypatched, by tests/unit/features/test_plan_graph_e2e.py."),
    "graph.resume": (
        "same executor and the same package-repo branch bootstrap as `graph "
        "run`, plus it requires a prior approved run in the ledger."),
}

#: Does NOT write the knowledge dir. `reader:` has nothing to scope; `config:`
#: writes machine-local state under AGENTWARE_CONFIG_HOME, which is deliberately
#: NOT team knowledge — a pinned publisher key and an approved-skill lock belong
#: to the MACHINE, and sharding them per team would make one engineer's trust
#: decisions arrive with someone else's clone.
NON_WRITERS = {
    "graph": "parent: subcommands only, no action of its own",
    "prefer": "parent: subcommands only, no action of its own",
    "release": "parent: `preflight` is its only action",
    "trust": "parent: pin/rotate/list/verify are its actions",
    "graph.show": "reader: renders the manifest",
    "graph.status": "reader: renders node state for the dashboard",
    "graph.validate": "reader: lints the manifest, writes nothing",
    "graph.teardown": "reader-on-this-path: with no provisioned lanes it "
                      "short-circuits; lane removal is git worktree state, "
                      "not knowledge",
    "plan.verify-premises": "reader: re-checks a plan's cited premises",
    "prefer.digest": "reader: renders the active preference set",
    "prefer.list": "reader: renders captured preferences",
    "prefer.review": "reader: renders the queue awaiting the operator",
    "release.preflight": "reader: a hermetic self-test over signing readiness",
    "remediate": "reader-on-this-path: with zero confirmed blocking findings it "
                 "short-circuits; its fixer mutates the PACKAGE repo, never the "
                 "knowledge dir",
    "scaffold": "parent: subcommands only, no action of its own",
    "scaffold.check": "reader: audits the tree's own wiring, writes nothing",
    "scaffold.new": "writer-on-another-target: emits a unit and its wiring into "
                    "the PACKAGE checkout (module, test, README row, curated "
                    "parity row), never the knowledge dir — the same shape as "
                    "`remediate`",
    "skill.sync": "config: writes skills.lock.json under AGENTWARE_CONFIG_HOME",
    "trust.list": "reader: prints the pinned publisher keys",
    "trust.pin": "config: appends to allowed_signers under AGENTWARE_CONFIG_HOME",
    "trust.rotate": "config: appends to allowed_signers under AGENTWARE_CONFIG_HOME",
    "trust.verify": "reader: checks allowed_signers integrity",
}
