"""The guidance-surface contract gate (feature 260813-modular-core-adoption, task 76).

Two halves, the shape every ratchet in this corpus carries.

The FIRST half proves the checker BITES. A gate over prose is the easiest kind to
write green and leave useless: it reads files the author just edited, so it agrees
with itself by construction. So the scratch tree is a copy of the REAL guidance
surfaces plus a stub for every path they resolve — the positive control is the
shipped content — and each mutation is one edit away from it and must be named by
its own arm.

Arm A gets an extra pair no other arm needs, because the arm is not "never say
`agentware_legacy.py`": `docs/architecture.md` must be able to say the monolith is
GONE. So one mutation adds a live mention (red) and its twin adds the same mention
inside a retirement sentence (green). A ban that cannot tell those apart would
force the documentation to stop explaining what happened.

The SECOND half runs the checker over this repository, which is the acceptance
criterion: `skill validate` passes for `modular-development`, no file under the
agent, docs or skills trees references the retired monolith path, and the contract
check exits 0 on the shipped tree.
"""

from tests._paths import TESTS_DIR

import os
import shutil
import sys
import tempfile
import unittest

HERE = TESTS_DIR
REPO_ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _guidance_sync as gs  # noqa: E402


def read(root, rel):
    with open(os.path.join(root, rel), encoding="utf-8") as handle:
        return handle.read()


def write(root, rel, body):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)


def append(root, rel, paragraph):
    """Add ONE paragraph to a surface. Anchor-free on purpose: an anchored edit
    lands the mutation in whatever paragraph the anchor happens to sit in, and
    arm A is paragraph-scoped, so the mutation would inherit that paragraph's
    words rather than standing on its own."""
    write(root, rel, read(root, rel) + "\n\n" + paragraph + "\n")


def edit(root, rel, old, new, count=-1):
    body = read(root, rel)
    assert old in body, "fixture drift: %r not in %s" % (old, rel)
    write(root, rel, body.replace(old, new, count))


class ScratchTreeTest(unittest.TestCase):
    """The real surfaces, the real manifest, and a stub per resolved path."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="aw-guidance-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        surfaces = gs._surfaces(REPO_ROOT)
        self.assertGreaterEqual(
            len(surfaces), 25,
            "the guidance set collapsed; a vacuous scan passes every arm")
        copied = set(rel for rel, _kind in surfaces)
        copied.add(gs.CATALOG_REL)
        for rel in sorted(copied):
            dst = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(REPO_ROOT, rel), dst)
        for rel, _kind in surfaces:
            text = read(self.root, rel)
            targets = set(gs.path_tokens(text)) | set(gs.RUNNABLE.findall(text))
            for token in targets:
                self._stub(token, copied)

    def _stub(self, token, copied):
        """Mirror a path that EXISTS in the real tree, as an empty file or dir.

        Only real paths are mirrored: a token the live tree does not hold is
        either manifest-claimed or a declared exemption, and stubbing it would
        hide exactly the failure arm B exists to report.
        """
        if token in copied or not os.path.exists(os.path.join(REPO_ROOT, token)):
            return
        dst = os.path.join(self.root, token.rstrip("/"))
        if token.endswith("/") or os.path.isdir(os.path.join(REPO_ROOT, token)):
            os.makedirs(dst, exist_ok=True)
            return
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if not os.path.exists(dst):
            with open(dst, "w", encoding="utf-8") as handle:
                handle.write("")

    def errors(self):
        return gs.problems(self.root)

    def assertNoErrors(self):
        self.assertEqual(self.errors(), [])

    def assertNamed(self, arm, needle):
        hits = [e for e in self.errors()
                if e.startswith(arm + ":") and needle in e]
        self.assertTrue(hits, "arm %s named nothing containing %r; got %r"
                        % (arm, needle, self.errors()))


class PositiveControlTest(ScratchTreeTest):

    def test_the_unmutated_copy_of_the_real_tree_passes(self):
        """Without this, every mutation below could be failing for its own
        reason and the gate would still look like it works."""
        self.assertNoErrors()

    def test_the_scratch_tree_holds_every_class_of_surface(self):
        """WIDENED at task 77: the community-health documents joined the
        declared set. A human reads them rather than an agent, but a pointer
        that no longer resolves misleads either one, so they are a fifth CLASS
        rather than an exception."""
        kinds = set(kind for _rel, kind in gs._surfaces(self.root))
        self.assertEqual(kinds,
                         {"persona", "skill", "doc", "steering", "community"})

    def test_every_community_document_is_in_the_declared_set(self):
        """The class is not decorative: each named file must really be scanned,
        or arm B stops seeing the documents most likely to name a stale path."""
        scanned = set(rel for rel, _kind in gs._surfaces(self.root))
        for rel in gs.COMMUNITY:
            self.assertIn(rel, scanned)
        self.assertIn(".github/PULL_REQUEST_TEMPLATE.md", scanned)


class RetiredNameTest(ScratchTreeTest):
    """A. a retired path may be named only in a retirement sentence."""

    def test_a_live_mention_of_the_retired_monolith_is_named(self):
        append(self.root, "docs/loop.md",
               "The loop dispatches through `scripts/agentware_legacy.py`.")
        self.assertNamed("A", "docs/loop.md")

    def test_the_same_mention_inside_a_retirement_sentence_is_allowed(self):
        """The twin of the test above: the arm bans a live CLAIM, not a word.
        A ban that could not tell these apart would force the documentation to
        stop explaining what happened to the monolith."""
        append(self.root, "docs/loop.md",
               "The loop no longer dispatches through "
               "`scripts/agentware_legacy.py`; that file was deleted.")
        self.assertNoErrors()

    def test_the_retired_size_claim_is_named(self):
        append(self.root, "docs/fanout.md",
               "Lanes serialise on the single ~10k-line entrypoint.")
        self.assertNamed("A", "docs/fanout.md")

    def test_a_retired_path_that_comes_back_on_disk_fails_its_row(self):
        """Two-directional: a ledger claiming an absence must be TRUE."""
        write(self.root, "scripts/agentware_legacy.py", "# resurrected\n")
        self.assertNamed("A", "back on disk")

    def test_a_ghost_checker_reintroduced_into_a_persona_is_named(self):
        append(self.root, ".claude/agents/agentware-execution.md",
               "Run `tests/_global_resolve.py` before every edit.")
        self.assertNamed("A", "agentware-execution.md")


class ClaimedPathTest(ScratchTreeTest):
    """B. every repo-relative path a surface names is claimed."""

    def test_a_path_in_no_tree_and_no_manifest_is_named(self):
        """Mutated inside a backticked COMMAND, which is where a doc's paths
        mostly live: reading only word 0 of a span would see `python3` and check
        nothing, so this arm would have had a blind spot exactly where the
        document is most actionable."""
        edit(self.root, ".claude/skills/modular-development/SKILL.md",
             "`python3 tests/_manifest_gen.py --write`",
             "`python3 tests/_manifest_genn.py --write`", 1)
        self.assertNamed("B", "tests/_manifest_genn.py")

    def test_an_absent_path_the_manifest_CLAIMS_is_accepted(self):
        """The load-bearing half: `docs/architecture.md` names the owning test
        files `R-MOD-06` requires, 189 of which nobody has written yet."""
        absent = [module["test_file"] for module in self._manifest()["modules"]
                  if not os.path.exists(os.path.join(REPO_ROOT,
                                                     module["test_file"]))]
        self.assertTrue(absent, "fixture drift: every owning test now exists")
        self.assertIn(absent[0], read(self.root, "docs/architecture.md"))
        self.assertNoErrors()

    def test_dropping_the_claim_turns_that_same_path_into_a_finding(self):
        """Proof the arm accepts a CLAIM, not merely any tests/ path."""
        import json
        manifest = self._manifest()
        victim = None
        for module in manifest["modules"]:
            rel = module["test_file"]
            if (not os.path.exists(os.path.join(REPO_ROOT, rel))
                    and rel in read(self.root, "docs/architecture.md")):
                victim = rel
                module["test_file"] = ""
                break
        self.assertIsNotNone(victim, "fixture drift: no claimed-absent test")
        write(self.root, gs.CATALOG_REL, json.dumps(manifest, indent=2))
        self.assertNamed("B", victim)

    def _manifest(self):
        import json
        with open(os.path.join(self.root, gs.CATALOG_REL),
                  encoding="utf-8") as handle:
            return json.load(handle)


class PublishedModuleTest(ScratchTreeTest):
    """C. every agentware_lib.<dotted> name resolves to a published module."""

    def test_a_module_the_manifest_does_not_publish_is_named(self):
        edit(self.root, "docs/architecture.md", "`agentware_lib.ops.derive`",
             "`agentware_lib.ops.deriveee`", 1)
        self.assertNamed("C", "agentware_lib.ops.deriveee")

    def test_a_symbol_on_a_published_module_is_accepted(self):
        """`agentware_lib.cli.assemble_parser` is a function, not a module: the
        arm resolves the longest published PREFIX rather than the whole name."""
        self.assertIn("agentware_lib.cli.assemble_parser",
                      read(self.root, "docs/architecture.md"))
        self.assertNoErrors()


class RunnableCommandTest(ScratchTreeTest):
    """D. every `python3 tests/_x.py` a surface prints resolves."""

    def test_a_ghost_checker_command_in_a_skill_is_named(self):
        edit(self.root, ".claude/skills/modular-development/SKILL.md",
             "python3 tests/_ceiling_gate.py",
             "python3 tests/_ceiling_gate_v2.py", 1)
        self.assertNamed("D", "tests/_ceiling_gate_v2.py")


class ExemptionTest(ScratchTreeTest):
    """E. an exemption lives only as long as its witness does."""

    def test_removing_the_witness_names_the_row(self):
        token, rel, witness, _reason = gs.EXEMPT[0]
        edit(self.root, rel, witness, "scaffold new --kind verb")
        self.assertNamed("E", token)

    def test_an_exemption_nothing_references_any_more_is_named(self):
        """The row is checked in BOTH directions: this mutation keeps the
        witness and drops only the token, so the `stale` branch is what fires."""
        token, rel, witness, _reason = gs.EXEMPT[2]
        edit(self.root, rel, token, "the pinned release key")
        self.assertIn(witness, read(self.root, rel))
        self.assertNamed("E", "stale")

    def test_every_shipped_exemption_states_a_reason(self):
        for token, rel, witness, reason in gs.EXEMPT:
            self.assertTrue(witness.strip(), "%s has an empty witness" % token)
            self.assertGreater(len(reason.split()), 8,
                               "%s (%s) has a stub reason" % (token, rel))


class ClaimTest(ScratchTreeTest):
    """F. a surface that must name a thing still names it."""

    def test_the_fanout_doc_must_name_the_real_chokepoint(self):
        edit(self.root, "docs/fanout.md", "catalog/modules.json", "that file")
        self.assertNamed("F", "docs/fanout.md")

    def test_the_execution_persona_must_route_to_the_skill(self):
        edit(self.root, ".claude/agents/agentware-execution.md",
             "modular-development", "modular development")
        self.assertNamed("F", "agentware-execution.md")

    def test_a_claim_row_naming_a_missing_surface_is_named(self):
        os.remove(os.path.join(self.root, ".claude/skills/README.md"))
        self.assertNamed("F", ".claude/skills/README.md")


class LiveTreeTest(unittest.TestCase):
    """The acceptance criteria, asserted against the shipped repository."""

    def test_the_contract_check_passes_on_this_repository(self):
        self.assertEqual(gs.problems(REPO_ROOT), [])

    def test_no_agent_doc_or_skill_references_the_retired_monolith_path(self):
        """The plan's literal criterion, asserted without the marker escape:
        the retired ENTRYPOINT is named nowhere in the three trees at all."""
        offenders = []
        for rel, kind in gs._surfaces(REPO_ROOT):
            if kind == "steering":
                continue
            if "agentware_legacy.py" in read(REPO_ROOT, rel):
                offenders.append(rel)
        self.assertEqual(offenders, [])

    def test_the_modular_development_skill_is_installed_and_catalogued(self):
        import json
        skill = ".claude/skills/modular-development/SKILL.md"
        self.assertTrue(os.path.isfile(os.path.join(REPO_ROOT, skill)))
        with open(os.path.join(REPO_ROOT, "catalog", "skills.json"),
                  encoding="utf-8") as handle:
            catalog = json.load(handle)
        rows = [s for s in catalog["skills"]
                if s["name"] == "modular-development"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], ".claude/skills/modular-development")

    def test_the_skill_validates_against_the_agentskills_spec(self):
        sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
        from agentware_lib.features import skills as feature
        name, errs = feature._validate_skill_dir(
            os.path.join(REPO_ROOT, ".claude", "skills",
                         "modular-development"))
        self.assertEqual((name, errs), ("modular-development", []))

    def test_the_skill_body_is_not_always_on(self):
        """The whole point of the skill: the procedure costs nothing until it is
        invoked. A file that became always-on steering would defeat it."""
        import _alwayson_steering as always
        report = always.resolve(REPO_ROOT)
        resolved = set(entry["rel"] for entry in report["entries"])
        self.assertIn("CLAUDE.md", resolved)   # the resolver really ran
        self.assertNotIn(".claude/skills/modular-development/SKILL.md",
                         resolved)


if __name__ == "__main__":
    unittest.main()
