"""Owning unit file for ``agentware_lib.features.configcmd.cfgshow``.

Feature 260813-modular-core-adoption, task 81.

``cfgshow`` is the TERMINAL view of the `config` verb: the report every
non-setter invocation falls through to, in three renderings (human-readable,
``--format json``, and the bare ``--knowledge-dir-only`` path for shell
consumers) plus that view's exit-code contract.

Why this file exists at all
---------------------------
The module was ported with a de-duplication: the pre-split ``cmd_config``
printed its report tail inline in BOTH arms, and ``_view_lines`` computes it
once instead. The docstring called that "byte-identical output". It was not --
the shared tail came out one line short, and ``skill_promote`` vanished from
the human-readable report while the JSON arm kept the field and
``--skill-promote-only`` kept the value. Every structural gate passed it: the
surface dump reads no output, the body differ recorded the definition as
legitimately decomposed, and the help golden pins help text rather than
reports. ``tests/_solo_parity.py`` found it by running both CLIs side by side.

So the tests here are deliberately about OUTPUT, not about structure. A
decomposition claim ("both arms print the same lines") is a claim about
behaviour, and the only thing that can check it is a test that reads what the
verb prints.
"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

from tests._fixtures import load_cli

#: Every line of the shared tail, in print order. Pinned as a LIST rather than
#: a count so a line that is dropped names itself in the failure.
TAIL_LABELS = ["kb_autocommit", "kb_push", "retrieval_mode", "embedder",
               "embed_model", "kb_mode", "cli", "user_handle", "dream",
               "dream_sched", "dream_max_rt", "skill_promote"]


@contextlib.contextmanager
def isolated(kdir=None):
    """Redirect the config pointer to a temp file; never touch the operator's."""
    mod = load_cli()
    tmpd = tempfile.mkdtemp(prefix="agentware-cfgshow-")
    cfg = os.path.join(tmpd, "config.env")
    open(cfg, "w", encoding="utf-8").close()
    saved = (mod.HOME_CONFIG, mod.CONFIG_PATHS)
    saved_env = {k: os.environ.pop(k, None)
                 for k in ("AGENTWARE_KNOWLEDGE_DIR", "AGENTWARE_ORG_ROOT",
                           "AGENTWARE_TEAM", "AGENTWARE_SKILL_PROMOTE",
                           "AGENTWARE_SKILL_PROMOTE_CAP")}
    if kdir:
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = kdir
    mod.HOME_CONFIG = cfg
    mod.CONFIG_PATHS = (cfg,)
    try:
        yield mod
    finally:
        mod.HOME_CONFIG, mod.CONFIG_PATHS = saved
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(tmpd, ignore_errors=True)


def run(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = mod.main(argv)
    return code, out.getvalue(), err.getvalue()


def labels(text):
    """The ``label:`` column of a rendered report, in order."""
    return [line.split(":", 1)[0].strip() for line in text.splitlines()
            if ":" in line]


class TheHumanReadableReport(unittest.TestCase):
    """What an operator running a bare `config` actually reads."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="agentware-cfgshow-kb-")
        self.addCleanup(shutil.rmtree, self.kdir, ignore_errors=True)

    def test_it_reports_every_line_of_the_shared_tail(self):
        with isolated(self.kdir) as mod:
            code, out, _ = run(mod, ["config"])
        self.assertEqual(code, 0)
        self.assertEqual(labels(out)[:2], ["knowledge_dir", "initialized"])
        self.assertEqual(labels(out)[2:], TAIL_LABELS)

    def test_it_reports_the_resolved_skill_promotion_mode(self):
        """The regression `tests/_solo_parity.py` found. See the module head."""
        with isolated(self.kdir) as mod:
            _code, out, _ = run(mod, ["config"])
            _c2, only, _ = run(mod, ["config", "--skill-promote-only"])
        line = [ln for ln in out.splitlines()
                if ln.startswith("skill_promote:")]
        self.assertEqual(len(line), 1, "the report lost its skill_promote line")
        self.assertIn(only.strip(), line[0])
        self.assertIn("(cap ", line[0])

    def test_the_unconfigured_arm_prints_the_same_tail_and_exits_one(self):
        """The de-duplication's actual claim: one tail, both arms."""
        with isolated() as mod:
            code, out, _ = run(mod, ["config"])
        self.assertEqual(code, 1)
        self.assertEqual(labels(out)[0], "knowledge_dir")
        self.assertEqual(labels(out)[1:], TAIL_LABELS)

    def test_the_two_arms_differ_only_in_their_header(self):
        with isolated(self.kdir) as mod:
            _c, configured, _ = run(mod, ["config"])
        with isolated() as mod:
            _c, bare, _ = run(mod, ["config"])
        self.assertEqual(configured.splitlines()[2:], bare.splitlines()[1:])


class TheMachineReadableArms(unittest.TestCase):
    """The JSON dump and the bare path, which shell consumers parse."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="agentware-cfgshow-kb-")
        self.addCleanup(shutil.rmtree, self.kdir, ignore_errors=True)

    def test_the_json_arm_carries_every_reported_setting(self):
        with isolated(self.kdir) as mod:
            code, out, _ = run(mod, ["config", "--format", "json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        for key in ("knowledge_dir", "configured", "kb_autocommit", "kb_push",
                    "retrieval_mode", "kb_mode", "cli", "user_handle", "dream",
                    "skill_promote", "skill_promote_cap", "embedder_backend",
                    "phase_routing"):
            self.assertIn(key, data)

    def test_the_json_arm_and_the_text_arm_agree_on_skill_promote(self):
        with isolated(self.kdir) as mod:
            _c, text, _ = run(mod, ["config"])
            _c2, raw, _ = run(mod, ["config", "--format", "json"])
        data = json.loads(raw)
        self.assertIn("skill_promote: %s (cap %s)"
                      % (data["skill_promote"], data["skill_promote_cap"]),
                      text)

    def test_the_bare_path_arm_prints_the_path_and_nothing_else(self):
        with isolated(self.kdir) as mod:
            code, out, _ = run(mod, ["config", "--knowledge-dir-only"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), self.kdir)

    def test_the_bare_path_arm_is_silent_and_nonzero_when_unconfigured(self):
        with isolated() as mod:
            code, out, _ = run(mod, ["config", "--knowledge-dir-only"])
        self.assertEqual(code, 1)
        self.assertEqual(out.strip(), "")


if __name__ == "__main__":
    unittest.main()
