"""Self-healing adversarial-review gate — end-to-end loop test
(feature 260713-adversarial-review-gate, Task 5).

Drives the REAL agentware.sh against a SYNTHETIC, INITIALIZED throwaway knowledge
dir and a FAKE agent runtime, proving the Task-5 wiring in `run_selfheal_review`
(AUDIT -> CALL-OUT -> FIX -> RE-AUDIT, bounded, then PROCEED-or-ESCALATE):

  * a self-extension diff with a planted CONFIRMED defect is CAUGHT, auto-fixed,
    RE-AUDITED clean, and only THEN does the run PROCEED to the done-stamp + kb-sync
    ("fully complete", exit 0);
  * an UNFIXABLE planted defect (the fixer names no regression test, so the fix is
    REFUSED) survives every bounded round -> the run is BLOCKED (exit 1, "BLOCKED"),
    and NEVER reaches the done-stamp / kb-sync ("fully complete" absent);
  * a CLEAN self-extension diff proceeds with no remediation;
  * a NON-self-extension diff skips the gate entirely (byte-identical to today).

The gate spawns its Opus fan-out through `scripts/agentware review`/`remediate`,
which resolve the agent binary via AGENTWARE_CLI — so a single FAKE `claude` binary
drives every reviewer / verifier / critic / fixer with NO real Opus. The operator KB
is never touched (R-LOC-03): HOME + AGENTWARE_KNOWLEDGE_DIR are patched to temp dirs.

Runner:  python3 -m unittest tests.integration.test_loop_selfheal_gate -v
"""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest

try:
    from tests._fixtures import REPO_ROOT, build_synthetic_kb, run_cli
except ImportError:  # discover -s tests puts tests/ on sys.path
    from _fixtures import REPO_ROOT, build_synthetic_kb, run_cli

AGENTWARE_SH = os.path.join(REPO_ROOT, "agentware.sh")
FEATURE = "260713-selfheal-gate-test"

# Lint-passing synthetic plan: monotonic `- ⬜ **N**` markers, a *Verify:* per task,
# an R7 KB token, one [e2e] task, exactly one <promise>, no banned phrases.
PLAN_MD = """\
# Plan — self-heal gate test

> Synthetic plan for the self-healing adversarial-review gate loop test.

## Target packages

- /tmp/aw-selfheal-gate-target

## Tasks

- ⬜ **1** Implement the first synthetic thing deterministically.
  *Verify:* `scripts/agentware index validate` exits 0.

- ⬜ **2** Update the knowledge base after building (`R-KB-*`).
  Run `scripts/agentware learn` then `scripts/agentware index validate`.
  *Verify:* `scripts/agentware index validate` exits 0.

- ⬜ **3** [e2e] Run the end-to-end regression against the real loop.
  *Verify:* `./agentware.sh selfheal-gate` proceeds/blocks as expected.

## Acceptance criteria

- [ ] the self-heal gate fires on a self-extension diff

<promise>TASK_COMPLETE</promise>
"""

# One FAKE `claude` for every spawn. Dispatch is on the prompt HEAD (the text BEFORE
# the first `<<<` data fence) so the fenced diff/finding DATA — which, for THIS very
# feature, literally contains the reviewer/critic/fixer prompt strings — can never
# leak a dispatch keyword. Review-gate spawns return ONLY JSON; phase spawns (PRE /
# MAIN / POST) flip one plan marker (MAIN only) + write a worklog line and echo the
# phase promises, mirroring tests/test_loop_worklog_gate.
FAKE_CLI = r"""#!/usr/bin/env bash
prompt="${!#}"
head="${prompt%%<<<*}"

# --- Review-gate persona spawns (JSON only; no phase promises) ----------------
if printf '%s' "$head" | grep -q "REMEDIATION fixer"; then
  if [[ "${FAKE_FIXABLE:-1}" == "1" ]]; then
    # A verifiable fix: names a real, fast, passing regression test + signals the
    # RE-AUDIT that the defect is closed by dropping the fix marker.
    touch "$FAKE_FIXMARK"
    if [[ "${FAKE_FIXER_BREAKS_PLAN:-0}" == "1" ]]; then
      # Simulate a concurrent run deleting/replacing plan.md BETWEEN rounds:
      # the NEXT round's --acceptance-file open must fail. A directory (not
      # chmod 000) so the OSError fires even when the suite runs as root.
      rm -f "$FAKE_PLAN" && mkdir -p "$FAKE_PLAN"
    fi
    echo '{"fixes":[{"index":0,"status":"fixed","regression_test":"tests/unit/core/test_frontmatter.py","notes":"planted defect fixed"}]}'
  else
    # An UNVERIFIED fix: claims fixed but names NO regression test -> REFUSED
    # (downgraded to unfixable). No marker -> the defect survives every re-audit.
    echo '{"fixes":[{"index":0,"status":"fixed","notes":"claims fixed, names no regression test"}]}'
  fi
  exit 0
fi
if printf '%s' "$head" | grep -q "COMPLETENESS CRITIC"; then
  echo '{"gaps":[],"additional_findings":[]}'
  exit 0
fi
if printf '%s' "$head" | grep -q "REFUTE"; then
  echo '{"verdict":"confirmed","verdict_rationale":"reproduced against the diff"}'
  exit 0
fi
if printf '%s' "$head" | grep -q "dimension reviewer"; then
  if [[ "${FAKE_REVIEW_DIES:-0}" == "1" ]]; then
    exit 1   # simulate the agent runtime unreachable: no output, nonzero exit
  fi
  if [[ "${FAKE_REVIEW_SOFTFAIL_QUOTES:-0}" == "1" ]]; then
    # Exit-0 prose that QUOTES the gate's own fail-closed error strings — the
    # errlog-contamination shape: the soft-fail diagnostic relays this snippet
    # (fenced, words intact) into the round errlog, where an UN-anchored
    # classifier would misread a runtime-class exit-3 as a DIFF SOURCE failure.
    echo "sorry, I could not audit this diff; earlier harness output said review: cannot read --diff-file: x and also review: cannot resolve --diff-range 'HEAD' (git failed)"
    exit 0
  fi
  if [[ "${FAKE_REVIEWER_COMMITS_TREE:-0}" == "1" && -n "${FAKE_GIT_WT:-}" ]]; then
    # Simulate the operator committing the working tree WHILE the round runs:
    # the synthetic target repo becomes clean before the bash layer re-resolves
    # the diff. EXPLICIT --git-dir/--work-tree so this can NEVER touch the real
    # checkout, even if the env were dropped (git just errors, swallowed).
    git --git-dir "$FAKE_GIT_WT/.git" --work-tree "$FAKE_GIT_WT" add -A >/dev/null 2>&1 || true
    git --git-dir "$FAKE_GIT_WT/.git" --work-tree "$FAKE_GIT_WT" -c user.email=t@example.com -c user.name=t commit -m mid-run-commit --no-gpg-sign >/dev/null 2>&1 || true
  fi
  if [[ "${FAKE_CLEAN:-0}" == "1" ]] || [[ -f "${FAKE_FIXMARK:-/nonexistent}" ]]; then
    echo '{"findings":[]}'
  elif printf '%s' "$head" | grep -q "'correctness' dimension reviewer"; then
    echo '{"findings":[{"dimension":"correctness","severity":"high","file":"agentware.sh","line":1,"summary":"planted correctness defect","failure_scenario":"hostile input crashes the gate"}]}'
  else
    echo '{"findings":[]}'
  fi
  exit 0
fi

# --- Phase spawns (PRE / MAIN / POST) -----------------------------------------
if printf '%s' "$prompt" | grep -q "find the next task marked"; then
  python3 - "$FAKE_PLAN" <<'PY'
import re, sys
p = sys.argv[1]
with open(p, encoding="utf-8") as f:
    s = f.read()
s = re.sub(r"⬜|🟡", "✅", s, count=1)
with open(p, "w", encoding="utf-8") as f:
    f.write(s)
PY
  printf -- '- iteration: did synthetic task, verified.\n' >> "$FAKE_WORKLOG"
fi
echo "<promise>PRE_TASK_COMPLETE</promise>"
echo "<promise>TASK_COMPLETE</promise>"
echo "<promise>POST_COMPLETE</promise>"
"""


def _have(binary):
    return shutil.which(binary) is not None


@unittest.skipUnless(_have("jq"), "jq (an agentware.sh preflight dep) is required")
@unittest.skipUnless(_have("bash"), "bash is required to run agentware.sh")
@unittest.skipUnless(_have("git"), "git is required for the self-extension predicate")
class SelfHealGateLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentware-shgate-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.tmp_home = os.path.join(self.tmp, "home")
        os.makedirs(self.tmp_home)
        self.bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin_dir)
        self.kdir = os.path.join(self.tmp, "kb")
        os.makedirs(self.kdir)
        # Build + initialize a valid KB so the pre/post toolkit gates run for real.
        build_synthetic_kb(self.kdir)
        run_cli(["index", "migrate-frontmatter"], self.kdir)
        run_cli(["index", "rebuild"], self.kdir)
        with open(os.path.join(self.kdir, "MAIN.md"), "w", encoding="utf-8") as f:
            f.write("# KB\n\n- **Handle**: tester\n")
        with open(os.path.join(self.kdir, ".initialized"), "w", encoding="utf-8") as f:
            f.write("2026-07-13T00:00:00Z tester\n")
        self.docs = os.path.join(self.kdir, "work", FEATURE)
        os.makedirs(self.docs)
        self.plan = os.path.join(self.docs, "plan.md")
        with open(self.plan, "w", encoding="utf-8") as f:
            f.write(PLAN_MD)
        self.worklog = os.path.join(self.docs, "worklog.md")
        self.fake_cli = os.path.join(self.bin_dir, "claude")
        with open(self.fake_cli, "w", encoding="utf-8") as f:
            f.write(FAKE_CLI)
        os.chmod(self.fake_cli, os.stat(self.fake_cli).st_mode
                 | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        self.fixmark = os.path.join(self.tmp, "fixmark")

    def _env(self, **extra):
        env = dict(os.environ)
        env.update({
            "HOME": self.tmp_home,
            "AGENTWARE_KNOWLEDGE_DIR": self.kdir,
            "AGENTWARE_CLI": "claude",
            "AGENTWARE_KB_AUTOCOMMIT": "0",
            "AGENTWARE_KB_PUSH": "0",
            "AGENTWARE_NO_STREAM": "1",
            "AGENTWARE_METRICS_EMIT": "0",
            "AGENTWARE_DISABLE_RELEASE_GATE": "1",   # our own edits show dirty
            "AGENTWARE_DISABLE_TARGET_GATE": "1",    # target path need not exist
            # Keep the audited diff small + deterministic: the fake ignores the diff
            # body (it dispatches on the prompt head), and the self-extension
            # PREDICATE is independent of the range (it reads git status/name-only).
            "AGENTWARE_REVIEW_DIFF_RANGE": "HEAD..HEAD",
            "FAKE_PLAN": self.plan,
            "FAKE_WORKLOG": self.worklog,
            "FAKE_FIXMARK": self.fixmark,
            "PATH": self.bin_dir + os.pathsep + os.environ.get("PATH", ""),
        })
        env.update({k: str(v) for k, v in extra.items()})
        return env

    def _run(self, env, cwd=None):
        return subprocess.run(
            ["bash", AGENTWARE_SH, FEATURE], cwd=cwd or REPO_ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            timeout=300)

    def _make_git_workdir(self):
        """A synthetic git checkout MIRRORING the real repo via top-level
        symlinks (so agentware.sh's relative references — scripts/, .claude/ —
        still resolve) whose GIT state the test fully controls. The loop runs
        with THIS as cwd, so every bare `git` the gate's bash layer executes
        reads the synthetic repo — NEVER the operator's real checkout, whose
        dirty/clean state is non-deterministic across dev machines and CI.
        Returns (workdir, git) where git(*args) runs `git -C workdir ...`."""
        workdir = os.path.join(self.tmp, "workdir")
        os.makedirs(workdir)
        for entry in os.listdir(REPO_ROOT):
            if entry == ".git":
                continue
            os.symlink(os.path.join(REPO_ROOT, entry),
                       os.path.join(workdir, entry))

        def git(*args):
            return subprocess.run(
                ["git", "-C", workdir] + list(args),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        self.assertEqual(git("init").returncode, 0, "git init failed")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
        with open(os.path.join(workdir, "pkgfile.txt"), "w",
                  encoding="utf-8") as f:
            f.write("seed\n")
        self.assertEqual(git("add", "-A").returncode, 0)
        self.assertEqual(
            git("commit", "-m", "seed", "--no-gpg-sign").returncode, 0)
        # Dirty the tree: `git diff HEAD` now resolves NON-empty — the shape
        # the gate fires on in production (default diff_range=HEAD).
        with open(os.path.join(workdir, "pkgfile.txt"), "w",
                  encoding="utf-8") as f:
            f.write("changed self-extension content\n")
        return workdir, git

    def test_planted_defect_is_fixed_reaudited_clean_then_proceeds(self):
        # Round 1 finds a CONFIRMED high; remediation applies a verified fix +
        # drops the marker; round 2 re-audits CLEAN -> the run PROCEEDS.
        proc = self._run(self._env(FAKE_FIXABLE="1", AGENTWARE_REVIEW_MAX_ROUNDS="3",
                                   AGENTWARE_REVIEW_DIFF_FILES_OVERRIDE="scripts/agentware"))
        out = proc.stdout
        self.assertEqual(proc.returncode, 0,
                         "a self-healed run must complete:\n%s" % out)
        self.assertIn("running the adversarial-review gate", out, out)
        self.assertIn("round 1", out, out)
        self.assertIn("auto-remediating", out, out)
        self.assertIn("CLEAN", out, out)          # a later round audits clean
        self.assertIn("fully complete", out, out)  # reached done-stamp + kb-sync
        self.assertTrue(os.path.isfile(self.fixmark), "the fixer must have run")

    def test_single_pass_default_ships_after_one_audit_and_fix(self):
        # NEW single-pass default (max_rounds=1, operator-directed 2026-07-24):
        # ONE audit finds a confirmed high, remediation applies a SELF-VERIFIED
        # fix (regression test + gate chain green -> exit 0), and the run SHIPS
        # WITHOUT a confirming re-audit. No AGENTWARE_REVIEW_MAX_ROUNDS is set, so
        # the default (1) is exercised — this locks the audit->fix->re-audit LOOP
        # from silently coming back (the run-1 marathon / max-rounds cliff).
        proc = self._run(self._env(FAKE_FIXABLE="1",
                                   AGENTWARE_REVIEW_DIFF_FILES_OVERRIDE="scripts/agentware"))
        out = proc.stdout
        self.assertEqual(proc.returncode, 0,
                         "a single-pass self-healed run must complete:\n%s" % out)
        self.assertIn("max_rounds=1", out,
                      "the default must be exactly ONE audit:\n%s" % out)
        self.assertIn("auto-remediating", out, out)
        self.assertIn("no confirming re-audit", out,
                      "single-pass must SHIP the self-verified fix, not re-audit:\n%s" % out)
        self.assertNotIn("round 2", out,
                         "single-pass must NOT run a second audit round:\n%s" % out)
        self.assertIn("fully complete", out,
                      "the single-pass run must reach done-stamp + kb-sync:\n%s" % out)
        self.assertTrue(os.path.isfile(self.fixmark), "the fixer must have run")

    def test_single_pass_failed_fix_blocks_immediately(self):
        # Single-pass (default max_rounds=1) with an UNFIXABLE defect: remediation
        # exits non-zero (no regression test -> fix refused), so the gate BLOCKS on
        # the failed fix in round 1 — never shipping a broken fix, never re-auditing.
        proc = self._run(self._env(FAKE_FIXABLE="0",
                                   AGENTWARE_REVIEW_DIFF_FILES_OVERRIDE="scripts/agentware"))
        out = proc.stdout
        self.assertNotEqual(proc.returncode, 0,
                            "a failed single-pass fix must block the run:\n%s" % out)
        self.assertIn("BLOCKED", out, out)
        self.assertNotIn("fully complete", out,
                         "a blocked run must NOT reach done-stamp + kb-sync:\n%s" % out)
        self.assertNotIn("round 2", out,
                         "single-pass must not re-audit before blocking:\n%s" % out)

    def test_unfixable_defect_blocks_done_and_escalates(self):
        # The fixer never provides a regression test -> every fix is REFUSED -> the
        # confirmed high survives every bounded round -> BLOCKED, no done, no commit.
        proc = self._run(self._env(FAKE_FIXABLE="0", AGENTWARE_REVIEW_MAX_ROUNDS="2",
                                   AGENTWARE_REVIEW_DIFF_FILES_OVERRIDE="scripts/agentware"))
        out = proc.stdout
        self.assertNotEqual(proc.returncode, 0,
                            "an unfixable defect must block the run:\n%s" % out)
        self.assertIn("BLOCKED", out, out)
        self.assertNotIn("fully complete", out,
                         "a blocked run must NOT reach done-stamp + kb-sync:\n%s" % out)
        self.assertFalse(os.path.isfile(self.fixmark),
                         "the unfixable fixer must not signal a closed defect")

    def test_clean_self_extension_diff_proceeds_without_remediation(self):
        proc = self._run(self._env(FAKE_CLEAN="1",
                                   AGENTWARE_REVIEW_DIFF_FILES_OVERRIDE="scripts/agentware"))
        out = proc.stdout
        self.assertEqual(proc.returncode, 0,
                         "a clean self-extension audit must proceed:\n%s" % out)
        self.assertIn("running the adversarial-review gate", out, out)
        self.assertIn("CLEAN", out, out)
        self.assertNotIn("auto-remediating", out,
                         "a clean round must not remediate:\n%s" % out)
        self.assertIn("fully complete", out, out)

    def test_non_self_extension_diff_skips_the_gate(self):
        # Force the changed-file list to a NON-package path: the predicate no-matches
        # and the gate is skipped entirely (no reviewer ever spawns).
        proc = self._run(self._env(
            AGENTWARE_REVIEW_DIFF_FILES_OVERRIDE="work/x/plan.md docs/readme.md"))
        out = proc.stdout
        self.assertEqual(proc.returncode, 0,
                         "a non-self-extension run must complete:\n%s" % out)
        self.assertIn("non-self-extension diff", out, out)
        self.assertNotIn("running the adversarial-review gate", out, out)
        self.assertIn("fully complete", out, out)

    # === Post-audit fail-CLOSED hardening (2026-07-13) =====================
    def test_agent_runtime_unreachable_fails_closed_blocks(self):
        # THE fail-open the audit found: when every reviewer subprocess fails (agent
        # runtime unreachable / nonzero exit), the gate must BLOCK (fail-closed),
        # NEVER silently stamp done + commit an un-audited self-extension diff.
        proc = self._run(self._env(
            FAKE_REVIEW_DIES="1", AGENTWARE_REVIEW_MAX_ROUNDS="2",
            AGENTWARE_REVIEW_DIFF_FILES_OVERRIDE="scripts/agentware"))
        out = proc.stdout
        self.assertNotEqual(proc.returncode, 0,
                            "an un-runnable audit must BLOCK, not proceed:\n%s" % out)
        self.assertIn("COULD NOT RUN", out, out)
        # Remediation round 9 (test-quality): 'COULD NOT RUN' is shared by BOTH
        # exit>=3 diagnosis branches, so it alone cannot lock branch selection —
        # a classifier mutation that matches unconditionally (misreporting a
        # genuine runtime outage as a DIFF SOURCE failure, and suppressing the
        # sanctioned recorded-skip recovery) would ship 100% green. Lock the
        # runtime-unreachable branch's CONTENT: it must name the real class,
        # must still offer the sanctioned skip hatch, and must NOT print the
        # diff-source diagnosis.
        self.assertIn("agent runtime unreachable", out,
                      "the runtime-class failure must be NAMED as such:\n%s"
                      % out)
        self.assertIn("AGENTWARE_SKIP_ADVERSARIAL_REVIEW=1", out,
                      "the runtime-unreachable class legitimately offers the "
                      "recorded-DECISION skip hatch — it must stay named:\n%s"
                      % out)
        self.assertNotIn("DIFF SOURCE", out,
                         "a runtime-class failure must never be misdiagnosed "
                         "as a diff-source failure:\n%s" % out)
        self.assertNotIn("fully complete", out,
                         "a fail-closed block must NOT reach done-stamp/kb-sync:\n%s"
                         % out)

    def test_unresolvable_diff_range_names_diff_source_not_bypass(self):
        # Remediation round 8 (reuse-in-new-context): cmd_review's exit 3
        # gained a NEW meaning — an unresolvable/unreadable DIFF SOURCE (gc'd
        # base ref, shallow clone, mistyped --diff-file). The bash consumer
        # used to reuse its stale predicate and diagnose every exit>=3 as
        # 'agent runtime unreachable', printing 'set
        # AGENTWARE_SKIP_ADVERSARIAL_REVIEW=1 to bypass' — actively steering
        # the operator toward the bypass for exactly the un-audited-diff shape
        # the fail-closed hardening exists to block. The console must now name
        # the DIFF SOURCE failure and must NOT suggest the bypass.
        proc = self._run(self._env(
            AGENTWARE_REVIEW_DIFF_RANGE="deadbeef123456..HEAD",
            AGENTWARE_REVIEW_MAX_ROUNDS="2",
            AGENTWARE_REVIEW_DIFF_FILES_OVERRIDE="scripts/agentware"))
        out = proc.stdout
        self.assertNotEqual(proc.returncode, 0,
                            "an unresolvable diff range must BLOCK:\n%s" % out)
        self.assertIn("COULD NOT RUN", out, out)
        self.assertIn("DIFF SOURCE", out,
                      "the console must name the REAL failure class (diff "
                      "source), not the stale unreachable guess:\n%s" % out)
        self.assertNotIn("agent runtime unreachable", out,
                         "the stale unreachable predicate must not fire for a "
                         "diff-source failure:\n%s" % out)
        self.assertNotIn("AGENTWARE_SKIP_ADVERSARIAL_REVIEW=1", out,
                         "the console must NOT steer the operator toward the "
                         "bypass over an UN-audited diff:\n%s" % out)
        self.assertNotIn("fully complete", out,
                         "a diff-source failure must NOT reach done-stamp:\n%s"
                         % out)

    def test_softfail_quoting_gate_errors_is_not_misread_as_diff_source(self):
        # Remediation round 9 (correctness + reuse-in-new-context): the round
        # errlog carries BOTH cmd_review's own operational lines AND relayed,
        # attacker/model-influenceable agent stdout/stderr snippets (fenced,
        # words intact). A reviewer that soft-fails with exit-0 prose QUOTING
        # the gate's own error strings — highly likely whenever the diff under
        # audit touches cmd_review's fail-closed messages, and steerable by a
        # hostile diff — used to make the UN-anchored exit>=3 classifier
        # misdiagnose the runtime-class failure as a DIFF SOURCE failure:
        # the operator was told to fix a healthy diff source, told 'do NOT
        # bypass', and the sanctioned recorded-skip recovery for the
        # runtime class was suppressed. The classifier must consume ONLY
        # line-anchored operational lines, never the fenced payload (R-SEC-02).
        proc = self._run(self._env(
            FAKE_REVIEW_SOFTFAIL_QUOTES="1", AGENTWARE_REVIEW_MAX_ROUNDS="2",
            AGENTWARE_REVIEW_DIFF_FILES_OVERRIDE="scripts/agentware"))
        out = proc.stdout
        self.assertNotEqual(proc.returncode, 0,
                            "a soft-failed fan-out must BLOCK:\n%s" % out)
        self.assertIn("COULD NOT RUN", out, out)
        # Vacuity guard: the classifier strings really DID contaminate the
        # round errlog (inside the fenced WARN relay) — the misclassification
        # input is present, and classification still resists it.
        errlog = os.path.join(self.docs, ".loop",
                              "adversarial-review-round-1.err")
        with open(errlog, encoding="utf-8") as f:
            errtxt = f.read()
        self.assertIn("cannot read --diff-file", errtxt,
                      "the relayed snippet must really reach the errlog — "
                      "else this test exercises nothing:\n%s" % errtxt)
        self.assertIn("untrusted agent stdout", errtxt,
                      "the relay must be fenced as untrusted DATA:\n%s"
                      % errtxt)
        # The REAL class wins: runtime/soft-fail diagnosis, with its
        # sanctioned skip hatch — never the forged diff-source diagnosis.
        self.assertIn("agent runtime unreachable", out, out)
        self.assertIn("AGENTWARE_SKIP_ADVERSARIAL_REVIEW=1", out, out)
        self.assertNotIn("DIFF SOURCE", out,
                         "a relayed snippet quoting the gate's own error "
                         "strings must NOT flip the diagnosis to DIFF "
                         "SOURCE:\n%s" % out)
        self.assertNotIn("fully complete", out, out)

    def test_unreadable_acceptance_file_names_criteria_source_not_bypass(self):
        # Remediation round 9 (reuse-in-new-context + novel-input): the gate
        # always passes --acceptance-file "$plan"; a plan deleted/replaced by
        # a concurrent run BETWEEN rounds used to degrade SILENTLY to empty
        # criteria — round 2 audited the diff with NO acceptance criteria and
        # stamped it GREEN. cmd_review now fails CLOSED (exit 3) and the bash
        # console must name the ACCEPTANCE-CRITERIA SOURCE class — not the
        # diff source, not the stale unreachable guess, and never the bypass.
        proc = self._run(self._env(
            FAKE_FIXABLE="1", FAKE_FIXER_BREAKS_PLAN="1",
            AGENTWARE_REVIEW_MAX_ROUNDS="3",
            AGENTWARE_REVIEW_DIFF_FILES_OVERRIDE="scripts/agentware"))
        out = proc.stdout
        self.assertNotEqual(proc.returncode, 0,
                            "an unreadable acceptance file must BLOCK — the "
                            "spec half of the audit cannot run:\n%s" % out)
        self.assertIn("auto-remediating", out,
                      "round 1 must really have run + remediated (the plan "
                      "breaks BETWEEN rounds):\n%s" % out)
        self.assertIn("COULD NOT RUN", out, out)
        self.assertIn("ACCEPTANCE-CRITERIA SOURCE", out,
                      "the console must name the criteria-source class:\n%s"
                      % out)
        self.assertNotIn("DIFF SOURCE", out, out)
        self.assertNotIn("agent runtime unreachable", out, out)
        self.assertNotIn("AGENTWARE_SKIP_ADVERSARIAL_REVIEW=1", out,
                         "the console must NOT steer the operator toward the "
                         "bypass over an un-audited diff:\n%s" % out)
        self.assertNotIn("fully complete", out, out)

    def test_mid_run_commit_vanishing_diff_blocks_clean_stamp(self):
        # Remediation round 9 (novel-input-edge-cases): the gate re-resolves
        # `git diff $diff_range` FRESH each round with no non-empty
        # expectation — a working tree committed/stashed MID-RUN made a later
        # round resolve an EMPTY diff, audit NOTHING, and stamp the
        # self-extension CLEAN (shipping any unremediated survivors GREEN
        # with zero reviewers having seen the final diff). The bash layer
        # already proved the entry diff non-empty; it must cross-check that
        # expectation before letting a clean round PROCEED.
        workdir, git = self._make_git_workdir()
        proc = self._run(self._env(
            FAKE_CLEAN="1", FAKE_REVIEWER_COMMITS_TREE="1",
            FAKE_GIT_WT=workdir,
            AGENTWARE_REVIEW_DIFF_RANGE="HEAD",
            AGENTWARE_REVIEW_DIFF_FILES_OVERRIDE="scripts/agentware"),
            cwd=workdir)
        out = proc.stdout
        # Vacuity guard: the mid-run commit really happened — the synthetic
        # tree is clean, so the round genuinely resolved an EMPTY diff.
        status = git("status", "--porcelain").stdout.strip()
        self.assertEqual(status, "",
                         "the fake reviewer must have committed the tree "
                         "mid-run (else this test exercises nothing)")
        self.assertNotEqual(proc.returncode, 0,
                            "a clean round over a VANISHED diff must BLOCK, "
                            "never stamp the un-audited diff green:\n%s" % out)
        self.assertIn("running the adversarial-review gate", out, out)
        self.assertIn("VANISHED", out,
                      "the console must name the vanished-diff class:\n%s"
                      % out)
        self.assertNotIn("fully complete", out,
                         "a vanished-diff round must NOT reach done-stamp:\n%s"
                         % out)

    def test_skip_false_does_NOT_bypass_gate(self):
        # Fix B: AGENTWARE_SKIP_ADVERSARIAL_REVIEW=false means "keep the gate ON".
        # A bare `-n` guard would BYPASS on any non-empty value (inverting intent);
        # the truthy-matched guard must RUN the gate.
        proc = self._run(self._env(
            FAKE_CLEAN="1", AGENTWARE_SKIP_ADVERSARIAL_REVIEW="false",
            AGENTWARE_REVIEW_DIFF_FILES_OVERRIDE="scripts/agentware"))
        out = proc.stdout
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("running the adversarial-review gate", out,
                      "=false must NOT bypass — the gate must actually run:\n%s" % out)
        self.assertNotIn("BYPASSING", out, out)
        self.assertIn("fully complete", out, out)

    def test_skip_internal_whitespace_does_NOT_bypass(self):
        # Re-audit residual: bash must trim ONLY leading/trailing whitespace (like
        # Python str.strip()), NOT collapse internal whitespace. "o n" must stay
        # non-truthy in BOTH bash and Python so it can never bypass without a DECISION.
        proc = self._run(self._env(
            FAKE_CLEAN="1", AGENTWARE_SKIP_ADVERSARIAL_REVIEW="o n",
            AGENTWARE_REVIEW_DIFF_FILES_OVERRIDE="scripts/agentware"))
        out = proc.stdout
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("running the adversarial-review gate", out,
                      "internal-space 'o n' must NOT bypass — the gate must run:\n%s"
                      % out)
        self.assertNotIn("BYPASSING", out, out)

    def test_skip_truthy_bypasses_with_recorded_decision(self):
        # A TRUTHY value bypasses — but ONLY with a recorded `> DECISION:` (never
        # silent), and the Python + bash truthy checks must AGREE so the marker lands.
        proc = self._run(self._env(
            FAKE_CLEAN="1", AGENTWARE_SKIP_ADVERSARIAL_REVIEW="1",
            AGENTWARE_REVIEW_DIFF_FILES_OVERRIDE="scripts/agentware"))
        out = proc.stdout
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("BYPASSING", out, out)
        self.assertNotIn("running the adversarial-review gate", out,
                         "a truthy skip must short-circuit before the round loop:\n%s"
                         % out)
        with open(self.worklog, encoding="utf-8") as f:
            wl = f.read()
        self.assertIn("> DECISION:", wl,
                      "a bypass must RECORD a > DECISION: marker (never silent)")


if __name__ == "__main__":
    unittest.main()
