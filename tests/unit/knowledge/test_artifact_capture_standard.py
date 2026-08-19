"""The ARTIFACT CAPTURE STANDARD (plan 260813 task 61, parity row
`docs:artifact-capture-standard`).

Runner:  python3 -m unittest tests.unit.knowledge.test_artifact_capture_standard -v

The standard, carried over from the donor tree: when a learning REFERENCES a
concrete artifact -- a config file, YAML snippet, exact command line, error
string or stack trace, file path, code snippet, URL/endpoint or identifier --
that artifact is embedded VERBATIM in a fenced code block rather than
paraphrased in prose. Detail lost at capture time is unrecoverable: a learning
that says "configured the collector with sigv4 auth" cannot be acted on six
months later, and the retriever can only surface content that is actually there.

WHY THIS IS TWO CLAIMS, NOT ONE
A written standard nobody can follow is decoration, and a capability nobody
documents is not a standard. So this file asserts BOTH halves, and each fails
independently:

  1. DOCS -- the standard is present in the skill an agent actually reads at
     promotion time, including the "do NOT pad" caveat. Without the caveat the
     standard reads as "make entries longer", which is the failure mode it was
     written to prevent, so the caveat is asserted as load-bearing rather than
     as flavour text.

  2. PRODUCT -- `learn` reproduces a cited artifact BYTE-FOR-BYTE in the stored
     entry, and `recall` can surface it. This is the half the standard rests on:
     if the write path reflowed, escaped or truncated a fenced block, following
     the standard would silently produce entries that no longer reproduce the
     artifact they cite -- exactly the parity row's mutation ("store the
     reference without the artifact: the captured body no longer reproduces the
     cited artifact"), reached through the code rather than through prose.

The artifact used below is deliberately hostile to a naive writer: YAML whose
indentation is semantic, a line with trailing whitespace, a `%s` that breaks
percent-formatting, a `{}` that breaks `str.format`, a backslash escape, and a
non-ASCII character. A write path that survives all six is one the standard can
be honestly asked of.
"""

import os
import unittest

try:
    from tests._fixtures import SyntheticKBTestCase, load_cli, REPO_ROOT
except ImportError:
    from _fixtures import SyntheticKBTestCase, load_cli, REPO_ROOT


SELF_IMPROVEMENT = os.path.join(
    REPO_ROOT, ".claude", "skills", "self-improvement", "SKILL.md")
KNOWLEDGE_BASE = os.path.join(
    REPO_ROOT, ".claude", "skills", "knowledge-base", "SKILL.md")

# Semantic indentation, trailing space, %s, {}, a backslash, and a non-ASCII
# character -- six independent ways a write path can corrupt an artifact.
ARTIFACT = (
    "extensions:\n"
    "  sigv4auth:\n"
    "    region: us-east-1\n"
    "    service: \"aps\"   \n"
    "exporters:\n"
    "  prometheusremotewrite:\n"
    "    endpoint: https://aps.example.com/workspaces/ws-1/api/v1/write\n"
    "    format: \"%s\"\n"
    "    template: \"{}\"\n"
    "    path_sep: \"C:\\\\tmp\"\n"
    "    note: \"café\"\n"
)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class StandardIsDocumentedTests(unittest.TestCase):
    """Half 1: the standard exists where an agent promoting a learning reads."""

    def test_self_improvement_skill_carries_the_standard(self):
        text = _read(SELF_IMPROVEMENT)
        self.assertIn("Artifact capture standard", text)
        self.assertIn("VERBATIM", text)
        self.assertIn("fenced code block", text)

    def test_the_standard_shows_a_good_and_a_thin_example(self):
        """A rule with no worked contrast is a rule people apply to the wrong
        things. The donor's section teaches by showing the SAME fact captured
        richly and captured thinly; dropping either example leaves an
        instruction with nothing to compare against."""
        text = _read(SELF_IMPROVEMENT)
        idx = text.index("Artifact capture standard")
        section = text[idx:idx + 4000]
        self.assertIn("Good (artifact embedded)", section)
        self.assertIn("Thin (artifact paraphrased", section)

    def test_the_do_not_pad_caveat_is_present(self):
        """Load-bearing, not flavour: without it the standard reads as "make
        entries longer", and the KB fills with padded one-fact learnings --
        the precise failure the caveat names."""
        text = _read(SELF_IMPROVEMENT)
        self.assertIn("do NOT pad", text)
        self.assertIn("MUST stay terse", text)

    def test_knowledge_base_skill_states_the_content_depth_rule(self):
        """The entry-conventions surface must carry the rule too, and must
        POINT at the full standard rather than restating it -- two copies of a
        standard drift, and the drill-down rule (R-DRILL-04) says the deep-dive
        lives in one place."""
        text = _read(KNOWLEDGE_BASE)
        self.assertIn("Content depth rule", text)
        self.assertIn("VERBATIM", text)
        self.assertIn("Artifact capture standard", text)
        self.assertIn("self-improvement/SKILL.md", text)


class ArtifactSurvivesTheWritePathTests(SyntheticKBTestCase):
    """Half 2: the behaviour the standard rests on."""

    def _learn(self, topic, content):
        code, out, err = self.run_cli(
            ["learn", "--topic", topic,
             "--summary", "collector config that works in prod",
             "--tags", "otel,config",
             "--content", content])
        self.assertEqual(code, 0, "learn failed: %s %s" % (out, err))
        return os.path.join(self.kdir, "learnings", "%s.md" % topic)

    def test_a_cited_artifact_is_stored_byte_for_byte(self):
        """The parity row's mutation, reached through the code: if the stored
        body no longer reproduces the cited artifact, the standard cannot be
        followed no matter how well it is written."""
        body = ("The collector config that works in prod:\n\n"
                "```yaml\n" + ARTIFACT + "```\n")
        path = self._learn("artifact-verbatim", body)
        stored = _read(path)
        self.assertIn(ARTIFACT, stored,
                      "the artifact was not reproduced verbatim")

    def test_each_hostile_character_class_survives_individually(self):
        """Asserted per class so a failure NAMES which transform broke it,
        rather than reporting one opaque inequality over an 11-line blob."""
        path = self._learn("artifact-verbatim-classes",
                           "```yaml\n" + ARTIFACT + "```\n")
        stored = _read(path)
        for label, needle in (
                ("semantic indentation", "    region: us-east-1"),
                ("trailing whitespace", "service: \"aps\"   "),
                ("percent format", "format: \"%s\""),
                ("brace format", "template: \"{}\""),
                ("backslash escape", "path_sep: \"C:\\\\tmp\""),
                ("non-ascii", "note: \"café\""),
        ):
            with self.subTest(label=label):
                self.assertIn(needle, stored)

    def test_the_fence_survives_so_the_artifact_stays_a_code_block(self):
        """The fence is what makes the artifact copy-pastable. An entry whose
        fence was eaten still CONTAINS the text but no longer presents it as an
        artifact, which is the paraphrase failure in a different costume."""
        path = self._learn("artifact-verbatim-fence",
                           "```yaml\n" + ARTIFACT + "```\n")
        stored = _read(path)
        self.assertIn("```yaml", stored)
        self.assertEqual(stored.count("```"), 2)

    def test_the_captured_artifact_is_retrievable(self):
        """"The retriever can surface a learning only if the content is there
        to surface" is the standard's own justification. Assert it rather than
        assume it: an entry whose body never reaches the index is captured but
        not recallable, and the standard's rationale would be false."""
        self._learn("artifact-verbatim-recall",
                    "```yaml\n" + ARTIFACT + "```\n")
        code, out, err = self.run_cli(
            ["recall", "prometheusremotewrite sigv4auth collector",
             "--format", "json"])
        self.assertEqual(code, 0, err)
        self.assertIn("artifact-verbatim-recall", out,
                      "the captured artifact is not reachable by recall")


if __name__ == "__main__":
    unittest.main()
