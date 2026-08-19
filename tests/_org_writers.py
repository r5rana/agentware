"""The ORG-MODE x LOCAL-WRITER cross product — scratch org, probe runner, roster.

Feature `260813-modular-core-adoption`, task 66. The roster itself is the
sibling `tests/_org_writer_rows.py` (this file was at the 600-line corpus cap
with both halves in it, and the halves have different reasons to change: this
one tracks the org layout, that one tracks the CLI surface).

WHY THIS EXISTS
---------------
The two merged trees each covered one half of a product and neither covered the
product.

  * The DONOR brought org mode — `teams/<team>/`, `shared/`, the curator gate,
    the fleet sink — and exercised it against the writers the donor had: `learn`,
    `decide`, `kb-git`.
  * This tree brought a second generation of writers the donor never had — the
    transcript miner, `checkup`, the whole `prefer` capture family, the review
    and remediation gates, the plan-graph scheduler, `tick`, `relate`,
    `migrate` — and exercised them on a FLAT knowledge dir, because a flat
    knowledge dir was the only kind that existed here.

So "does `prefer capture` land in the caller's team shard?" is a question that no
test on either side has ever asked. It is not hypothetical. R-ORG-02's blast
radius is one team's agent writing knowledge into another team's shard, where it
is recalled as that team's own and acted on — and a path prefix is not something
anyone notices in a commit diff.

WHAT A ROW PROVES
-----------------
Each `Writer` row is probed twice, and both halves have to hold:

  1. LANDS — run in org mode with the team resolved from the ENVIRONMENT
     (`AGENTWARE_ORG_ROOT` + `AGENTWARE_TEAM`, never an explicit
     `AGENTWARE_KNOWLEDGE_DIR`), the verb's writes land under `teams/<team>/`
     and NOWHERE ELSE. "Nowhere else" is a whole-org-tree sha256 manifest diff
     (`tests/_kb_rehearsal.capture`), not a list of paths someone thought to
     check: an assertion that only looks where it expects cannot see the write
     it did not expect, which is the exact shape of a cross-team leak.
  2. REFUSED — the same verb run as the OTHER team produces paths that the first
     team's staging allowlist rejects BY NAME with a `cross-team` reason. That
     is the layer at which a cross-team write is actually stopped from being
     committed, which is why the roster and the allowlist assertion share a file.

Aiming is by RESOLUTION, deliberately. A writer is not HANDED a team, it is
POINTED at one, and the probe asserts that pointing it at team A can produce
nothing outside team A. A probe that passed `--team` would be testing a
parameter rather than the property.

`setup` runs BEFORE the snapshot is taken, so a row's prerequisites (a plan file
for `plan set-state`, a queued candidate for `prefer approve`) are never counted
as the verb's own writes.

Hermetic: a scratch org tree in a tempdir with `AGENTWARE_CONFIG_HOME`
redirected, so a verb that persists config cannot reach the operator's real
`~/.agentware/config.env`. Stdlib + subprocess only.
"""

from tests._kb_rehearsal import capture, diff_manifests
from tests._paths import REPO_ROOT

import collections
import json
import os
import subprocess

CLI = os.path.join(REPO_ROOT, "scripts", "agentware")

#: The generated fixture the roster's COMPLETENESS is checked against. Deriving
#: the local-only surface from the parity manifest rather than restating it here
#: is what makes a NEW local-only verb redden the gate instead of silently
#: joining an unenumerated set.
PARITY_MANIFEST = os.path.join(REPO_ROOT, "tests", "fixtures",
                               "parity-manifest.json")

#: Pruned from the tree snapshot: the org repo's object store churns on every
#: `git add` the harness does and says nothing about where a verb wrote.
SNAPSHOT_PRUNE = (".git",)

#: Kill switches the probes need. `AGENTWARE_CHECKUP_LLM=0` stops `checkup`
#: spawning a real nested `claude`; `AGENTWARE_SKIP_ADVERSARIAL_REVIEW=1` stops
#: `review` fanning out real model subprocesses. Neither changes WHERE a verb
#: writes, which is the only thing under test here — but without them the suite
#: would make live model calls, which is not a test.
HERMETIC_ENV = {
    "AGENTWARE_CHECKUP_LLM": "0",
    "AGENTWARE_SKIP_ADVERSARIAL_REVIEW": "1",
    "AGENTWARE_DREAM": "0",
    "AGENTWARE_NESTED_UNITTEST": "1",
}


#: node    — the parity surface node id, minus the `surface:` prefix.
#: argv    — the exact CLI argv, run as a subprocess.
#: setup   — callable(ctx) seeding prerequisites, run BEFORE the snapshot.
#: writes  — kdir-relative paths that MUST appear in the write set. A row with an
#:           empty `writes` is a probe that proved nothing, and the roster's own
#:           schema test refuses one.
#: commits — COMMIT or LOCAL: which of the two isolation mechanisms holds this
#:           writer's output inside its shard. Declared per row rather than
#:           measured at runtime, because the interesting failure is a writer
#:           MOVING between the classes: knowledge that quietly stops being
#:           committed, or machine-local log state that quietly starts being.
Writer = collections.namedtuple("Writer", "node argv setup writes commits")

#: The output is a knowledge/work artifact git tracks: isolation is the staging
#: ALLOWLIST, which refuses another team's path by name.
COMMIT = "commit"
#: The output is machine-local state under `logs/` (or the derived `index.json`):
#: gitignored, so it never reaches the commit path from EITHER team. Isolation is
#: by exclusion rather than by refusal, and saying so is the point — asserting a
#: cross-team refusal over a path git never offers would be asserting nothing.
LOCAL = "local"


def no_setup(ctx):
    """No prerequisites: the verb writes into a freshly scaffolded team dir."""


class OrgTree(object):
    """A scratch two-team org repo, and the environment a real org run has."""

    def __init__(self, root, teams):
        self.root = root
        self.org = os.path.join(root, "org")
        self.cfg = os.path.join(root, "cfg")
        for path in (self.org, self.cfg):
            os.makedirs(path)
        self._git("init", "-q", ".")
        self._git("config", "user.email", "org-writers@local")
        self._git("config", "user.name", "org-writers")
        self.teams = list(teams)
        for team, handle in self.teams:
            rc, _out, err = self.cli(
                ["team", "create", "--name", team, "--handle", handle,
                 "--no-set-default", "--format", "json"], handle=handle)
            if rc != 0:
                raise AssertionError("team create %s: %s" % (team, err))

    def _git(self, *args):
        return subprocess.run(["git", "-C", self.org] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True)

    def stage_everything(self):
        """`git add -A` so `status --porcelain` names each path individually.

        Without it git COLLAPSES an untracked directory to `teams/`, and the
        allowlist would see one row instead of the per-team paths whose scoping
        is the whole question. The whole org root is staged, not just `teams/`,
        so an org-ROOT write (migration 002 seeds `shared/`) is enumerated too
        rather than hiding inside a collapsed directory row.
        """
        return self._git("add", "-A")

    def candidates(self):
        """Repo-relative paths the STAGE GATE will be offered — git's own view.

        A path git ignores is never offered, so it can neither be allowed nor
        refused; the two isolation mechanisms are told apart by this set.
        """
        from agentware_lib.ops.kbgit import git_changed_paths
        return set(git_changed_paths(self.org))

    def env(self, team=None, handle=None):
        """The environment an org-mode run has, and nothing else.

        `AGENTWARE_KNOWLEDGE_DIR` is REMOVED rather than set: R-ORG-04 puts the
        direct pointer ABOVE the org keys, so leaving one set would prove that an
        explicitly-aimed write lands where it was aimed — a tautology. The
        property under test is that the ORG KEYS aim it.
        """
        env = dict(os.environ)
        env.update(HERMETIC_ENV)
        env["AGENTWARE_CONFIG_HOME"] = self.cfg
        env["AGENTWARE_ORG_ROOT"] = self.org
        env.pop("AGENTWARE_KNOWLEDGE_DIR", None)
        for key, val in (("AGENTWARE_TEAM", team),
                         ("AGENTWARE_USER_HANDLE", handle)):
            if val:
                env[key] = val
            else:
                env.pop(key, None)
        return env

    def cli(self, argv, team=None, handle=None):
        """Run the real CLI as a subprocess in org mode. Returns (rc, out, err)."""
        proc = subprocess.run(["python3", CLI] + list(argv), cwd=REPO_ROOT,
                              env=self.env(team, handle),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True)
        return proc.returncode, proc.stdout, proc.stderr

    def team_dir(self, team):
        return os.path.join(self.org, "teams", team)


class Ctx(object):
    """One (org tree, team, handle) aim — what a `setup` callable is handed."""

    def __init__(self, tree, team, handle):
        self.tree = tree
        self.team = team
        self.handle = handle

    @property
    def kdir(self):
        return self.tree.team_dir(self.team)

    def path(self, rel):
        return os.path.join(self.kdir, rel)

    def expand(self, argv):
        """`@<rel>` -> the ABSOLUTE kdir path, everywhere in an argv.

        Several verbs take a filesystem path rather than a feature slug
        (`review --worklog`, `review-report --state-dir`). Those paths have to be
        absolute and team-resolved, and writing them out per row would put the
        team name in the roster — which is exactly the thing the probe must not
        let a row control, since it is aiming the write by hand at that point.
        """
        return [self.path(tok[1:]) if tok.startswith("@") else tok
                for tok in argv]

    def cli(self, argv):
        return self.tree.cli(self.expand(argv), team=self.team,
                             handle=self.handle)

    def must(self, argv):
        rc, out, err = self.cli(argv)
        if rc != 0:
            raise AssertionError("setup `%s` rc=%d: %s"
                                 % (" ".join(argv), rc, err or out))
        return out

    def write(self, rel, text):
        """Write `text` at a kdir-relative path, creating parents."""
        target = self.path(rel)
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(text)
        return target


def probe(writer, ctx):
    """Run one `Writer` in org mode and report WHAT IT TOUCHED, tree-wide.

    Returns (rc, stdout, stderr, touched) where `touched` is every org-relative
    path added or changed anywhere under the org root — the whole tree, so a
    write outside the caller's shard shows up as an ordinary row rather than as
    nothing at all.
    """
    writer.setup(ctx)
    before = capture(ctx.tree.org, prune=SNAPSHOT_PRUNE)
    rc, out, err = ctx.cli(writer.argv)
    after = capture(ctx.tree.org, prune=SNAPSHOT_PRUNE)
    delta = diff_manifests(before, after)
    touched = sorted(set(delta["added"]) | set(delta["changed"]))
    return rc, out, err, touched


def local_only_surface_nodes(path=PARITY_MANIFEST):
    """Every LOCAL-ONLY CLI surface node id, from the generated parity manifest.

    The roster must cover exactly this set, so a local-only verb added later
    fails here until someone classifies it as a writer or says in writing why it
    is not one.
    """
    with open(path, encoding="utf-8") as handle:
        rows = json.load(handle)["rows"]
    return {row["id"][len("surface:"):] for row in rows
            if row["dimension"] == "cli_surface"
            and row["sides"] == ["local"]
            and row["id"].startswith("surface:")}
