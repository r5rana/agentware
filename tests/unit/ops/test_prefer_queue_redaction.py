"""Transcript secret redaction on the COMMIT path (plan 260813 task 61).

Runner:  python3 -m unittest tests.unit.ops.test_prefer_queue_redaction -v

`minerextract`'s module docstring states the architecture as an invariant:

    "Everything the miner emits -- queue candidates, error signatures,
    workorders -- derives from these records, so redaction happens HERE, at the
    boundary where a record is first read, rather than at each of the places
    output is written. Redacting at the sinks means every new sink is a new
    chance to forget."

The code did not implement it. `_transcript_redact_secrets` was called from ONE
place (the error-signature selector in `minererrors`), was imported into
`minerscan` and never called, and the Stop-hook producer `_prefer_queue_scan`
stored the raw operator turn with the comment "the full `body` is stored
verbatim". Two of the three sinks the docstring names were therefore unredacted.

WHY THAT IS A COMMIT-PATH LEAK, NOT A LOG-LOCAL ONE
`logs/` is gitignored, so the queue file itself is local. Its CONSUMERS are not:
  * `_prefer_classify_drain` -> `_prefer_emit_workorder` writes the candidate
    into `work/auto/pref-*/plan.md` AND into that workorder's DIRECTORY NAME
    (the slug is `_slugify(text)[:40]`);
  * `prefer capture --from-workorder` promotes the same text into a durable
    `preferences/` entry via `learn`.
`work/` and `preferences/` are both staged by `kb_git_stage_allowlist` and
pushed. A preference-shaped prompt carrying a credential put that credential
into the knowledge base's git history permanently -- and a KB is the artifact
agentware is designed to SHARE.

These tests assert the behaviour end to end (the file a commit would carry), not
that a particular function is called, so a future refactor that moves redaction
somewhere else still passes and a future sink that forgets still fails.
"""

import json
import os
import shutil
import tempfile
import unittest

try:
    from tests._fixtures import load_cli, build_synthetic_kb, run_cli
except ImportError:
    from _fixtures import load_cli, build_synthetic_kb, run_cli


# A canary with a real issuer prefix and a real length, so it exercises the
# actual matcher rather than a placeholder the matcher was written around.
PAT = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"


def _rec(sid, turn, prompt, cwd="/tmp/x"):
    """One prompts.log record in the exact log-prompt.sh format."""
    return ("[2026-08-19T00:00:00Z] [session %s] [cwd %s] [origin=human] "
            "[turn=%s]\n%s\n\n" % (sid, cwd, turn, prompt))


def _tree_hits(root, needle, skip_dirs=("logs",)):
    """Every file under `root` whose text contains `needle`, excluding the
    gitignored dirs. This is the COMMITTABLE surface: what `kb-git commit`
    would carry."""
    hits = []
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fn in files:
            p = os.path.join(cur, fn)
            try:
                with open(p, encoding="utf-8", errors="replace") as f:
                    if needle in f.read():
                        hits.append(os.path.relpath(p, root))
            except OSError:
                pass
    return hits


class RedactorShapeTests(unittest.TestCase):
    """`_transcript_redact_secrets` as a pure function: what it must catch, what
    it must leave alone, and that applying it twice changes nothing."""

    def setUp(self):
        self.mod = load_cli()
        self.R = self.mod._transcript_redact_secrets

    def test_authorization_bearer_redacts_the_token_not_the_scheme(self):
        """The regression this task was found by.

        `\\S+` stops at the first space, so `Authorization: Bearer <token>` --
        the commonest credential shape in a transcript -- used to have the word
        "Bearer" replaced and the TOKEN left standing. Redacting the scheme name
        and publishing the secret is worse than not redacting at all, because
        the output LOOKS redacted.
        """
        out = self.R("Authorization: Bearer %s" % PAT)
        self.assertNotIn(PAT, out)
        self.assertIn("<redacted>", out)
        # The scheme word survives: it is not the secret, and keeping it is what
        # lets an operator reading the review queue tell what was removed.
        self.assertIn("Bearer", out)

    def test_basic_scheme_credential_redacted(self):
        out = self.R("authorization = Basic dXNlcjpwYXNzd29yZA==")
        self.assertNotIn("dXNlcjpwYXNzd29yZA==", out)
        self.assertIn("<redacted>", out)

    def test_bare_issuer_prefixed_tokens_redacted_without_a_delimiter(self):
        """Every pre-existing rule needed a delimiter -- a URL '@' or a ':'/'='
        after a credential key name. A credential pasted on its own ("deploy
        with ghp_...", an AKIA in a stack trace) matched none of them."""
        for secret, prose in (
                (PAT, "deploy with %s today"),
                ("AKIAIOSFODNN7EXAMPLE", "export it as %s"),
                ("sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAA", "the key %s"),
                ("xoxb-1234567890-abcdefghij", "slack bot %s"),
                ("glpat-ABCDEFGHIJKLMNOPQRST", "gitlab %s"),
                ("github_pat_ABCDEFGHIJKLMNOPQRSTUV", "fine-grained %s"),
        ):
            with self.subTest(secret=secret):
                out = self.R(prose % secret)
                self.assertNotIn(secret, out)
                self.assertIn("<redacted>", out)

    def test_pem_private_key_block_redacted_across_lines(self):
        """A pasted key is multi-line, so no single-line rule can reach it."""
        pem = ("-----BEGIN RSA PRIVATE KEY-----\n"
               "MIIEowIBAAKCAQEAsecretmaterialhere\n"
               "moresecretmaterial\n"
               "-----END RSA PRIVATE KEY-----")
        out = self.R("here is the key:\n" + pem)
        self.assertNotIn("secretmaterialhere", out)
        self.assertIn("<redacted-private-key>", out)

    def test_url_and_assignment_shapes_still_redacted(self):
        """The rules that already existed must keep working -- this test is the
        regression lock for the ones the new alternatives sit beside."""
        self.assertNotIn("pw@", self.R("https://user:pw@github.com/x/y.git"))
        self.assertIn("<redacted>", self.R("token=abc123"))
        self.assertIn("<redacted>", self.R("password: hunter2"))

    def test_ordinary_preference_prose_is_untouched(self):
        """Over-redaction has a real cost: the candidate text is what the
        operator READS in `prefer review` to decide. A matcher that eats prose
        makes the queue unreviewable, so the negatives are asserted as strict
        byte equality, not merely "still has some words"."""
        for text in (
                "Always pin dependency versions, never use ranges.",
                "use scikit-learn and task-force naming",
                "Password for the account is stored in 1Password",
                "prefer tabs over spaces",
                "the ask-me-anything flow",
                "check-in at 9am",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.R(text), text)

    def test_redaction_is_idempotent(self):
        """Both producers redact, and the error-recovery arm composes its text
        from already-redacted parts, so the function is applied more than once
        to the same string by design. A non-idempotent matcher would corrupt
        the second pass."""
        for text in ("Authorization: Bearer %s" % PAT,
                     "https://user:pw@github.com/x/y.git",
                     "deploy with %s" % PAT,
                     "token=abc123"):
            with self.subTest(text=text):
                once = self.R(text)
                self.assertEqual(self.R(once), once)


class QueueProducerRedactionTests(unittest.TestCase):
    """The Stop-hook producer: the record it persists, and everything derived
    from it, must be redacted."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_prefer_redact_")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.mod = load_cli()
        self.sid = "sess-REDACT"
        os.makedirs(os.path.join(self.kdir, "logs"), exist_ok=True)
        self.logp = os.path.join(self.kdir, "logs", "prompts.log")
        self.qpath = os.path.join(self.kdir, self.mod.PREFER_QUEUE_REL)

    def _scan(self, prompt, turn="t1"):
        with open(self.logp, "w", encoding="utf-8") as f:
            f.write(_rec(self.sid, turn, prompt))
        return self.mod._prefer_queue_scan(self.kdir, self.sid, "/tmp/x",
                                           prompts_path=self.logp)

    def _queue(self):
        if not os.path.isfile(self.qpath):
            return []
        with open(self.qpath, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]

    def test_queued_record_text_is_redacted(self):
        n = self._scan("From now on always deploy with `curl -H "
                       "'Authorization: Bearer %s' https://api.example.com/d` "
                       "and never use the web console." % PAT)
        self.assertEqual(n, 1)
        rec = self._queue()[0]
        self.assertNotIn(PAT, rec["text"])
        self.assertIn("<redacted>", rec["text"])

    def test_the_candidate_is_still_captured_and_still_readable(self):
        """Redaction must not turn into a capture regression: the cue still
        matches, the record still carries its schema, and the OPERATOR-MEANINGFUL
        part of the sentence survives for review."""
        self.assertEqual(
            self._scan("From now on always deploy with token=%s "
                       "and never use the web console." % PAT), 1)
        rec = self._queue()[0]
        self.assertEqual(rec["status"], "queued")
        self.assertEqual(rec["turn_id"], "t1")
        self.assertEqual(rec["producer"], self.mod.PREFER_QUEUE_PRODUCER)
        self.assertIn("never use the web console", rec["text"])
        self.assertIn("From now on always deploy", rec["text"])

    def test_matched_pattern_never_carries_secret_material(self):
        """`matched_pattern` is stored and rendered too; it is derived from the
        same redacted string as `text`, so the two can never disagree about
        what was removed."""
        self._scan("From now on always use %s" % PAT)
        self.assertEqual(self._queue()[0]["matched_pattern"], "from now on")

    def test_miner_record_builder_redacts_too(self):
        """The SECOND producer. `_transcript_build_record` had
        `_transcript_redact_secrets` imported into its module and never called;
        its recurrence arm passes a raw prompt body straight through, so the
        leak would return the moment MINER_RECURRENCE_RETIRED is lifted."""
        rec = self.mod._transcript_build_record(
            self.kdir, sid="s", turn_id="t",
            text="always deploy with Authorization: Bearer %s" % PAT,
            signal="recurrence")
        self.assertNotIn(PAT, rec["text"])
        self.assertIn("<redacted>", rec["text"])


class CommitPathSinkTests(unittest.TestCase):
    """The claim that actually matters: nothing a `kb-git commit` would stage
    carries the secret. Asserted over the TREE, so a sink nobody thought of is
    still covered."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="aw_prefer_sink_")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        build_synthetic_kb(self.kdir)
        self.mod = load_cli()
        self._prev = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
        os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self.kdir
        self.addCleanup(self._restore_env)
        os.makedirs(os.path.join(self.kdir, "logs"), exist_ok=True)
        self.logp = os.path.join(self.kdir, "logs", "prompts.log")

    def _restore_env(self):
        if self._prev is None:
            os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None)
        else:
            os.environ["AGENTWARE_KNOWLEDGE_DIR"] = self._prev

    def _mine_and_drain(self, prompt):
        with open(self.logp, "w", encoding="utf-8") as f:
            f.write(_rec("sess-SINK", "t1", prompt))
        self.assertEqual(
            self.mod._prefer_queue_scan(self.kdir, "sess-SINK", "/tmp/x",
                                        prompts_path=self.logp), 1)
        return self.mod._prefer_classify_drain(self.kdir)

    def test_emitted_workorder_carries_no_secret(self):
        res = self._mine_and_drain(
            "From now on always deploy with `curl -H 'Authorization: Bearer "
            "%s' https://api.example.com/d` and never use the web console."
            % PAT)
        self.assertEqual(res.get("proposed"), 1,
                         "the drain must still emit the workorder: this is a "
                         "redaction test, not a capture-suppression test")
        self.assertEqual(_tree_hits(self.kdir, PAT), [],
                         "a committed artifact carries the credential")
        # The plan really was written -- otherwise the assertion above is
        # vacuously true because there is nothing on disk to search.
        plans = []
        for cur, _d, files in os.walk(os.path.join(self.kdir, "work", "auto")):
            plans += [os.path.join(cur, f) for f in files if f == "plan.md"]
        self.assertEqual(len(plans), 1)
        with open(plans[0], encoding="utf-8") as f:
            self.assertIn("<redacted>", f.read())

    def test_workorder_directory_name_carries_no_secret(self):
        """The slug is `_slugify(text)[:40]`, so a SHORT prompt puts the
        credential in a committed PATH, where a content scan would not find
        it."""
        res = self._mine_and_drain("Always use %s" % PAT)
        self.assertEqual(res.get("proposed"), 1)
        names = os.listdir(os.path.join(self.kdir, "work", "auto"))
        self.assertEqual(len(names), 1)
        # Compare against a PREFIX of the SLUGIFIED credential. Two transforms
        # sit between the prompt and this name and each one would make a naive
        # assertion vacuous: `_slugify` lowercases and turns `_` into `-`, and
        # the slug is then cut to 40 chars, so neither the raw token nor its
        # full slug form ever appears literally. Unredacted, this directory is
        # `pref-always-use-ghp-abcdefghijklmnopqrstuvwxy-<fp>` -- 26 of the 35
        # credential characters, in a COMMITTED path where no content scan
        # would find them. The prefix is what survives both transforms.
        leaked = self.mod._slugify(PAT)[:20]
        self.assertNotIn(leaked, names[0])

    def test_promoted_preference_entry_carries_no_secret(self):
        """The durable sink: `prefer capture --from-workorder` writes the queue
        text into a `preferences/` entry that is committed AND injected into
        every future session."""
        res = self._mine_and_drain(
            "From now on always deploy with Authorization: Bearer %s and "
            "never use the web console." % PAT)
        self.assertEqual(res.get("proposed"), 1)
        qpath = os.path.join(self.kdir, self.mod.PREFER_QUEUE_REL)
        with open(qpath, encoding="utf-8") as f:
            recs = [json.loads(l) for l in f if l.strip()]
        wo = next(r["workorder"] for r in recs if r.get("workorder"))
        # Approval is a code check: flip the reviewed record to `approved` the
        # way `prefer approve` does, then run the activation the workorder names.
        for r in recs:
            if r.get("workorder") == wo:
                r["status"] = "approved"
        with open(qpath, "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        code, out, err = run_cli(
            ["prefer", "capture", "--from-workorder", wo], self.kdir)
        self.assertEqual(code, 0, "prefer capture failed: %s %s" % (out, err))
        self.assertEqual(_tree_hits(self.kdir, PAT), [])
        # `_prefer_capture` writes through `learn`, so the entry lands in
        # learnings/ as `pref-<scope>-<slug>.md` -- and the slug is derived from
        # the text, so an unredacted candidate would put the credential in the
        # FILENAME and the entry id as well as the body.
        learn_dir = os.path.join(self.kdir, "learnings")
        written = [f for f in os.listdir(learn_dir)
                   if f.startswith("pref-") and f.endswith(".md")]
        self.assertTrue(written, "no preference entry was written -- the "
                                 "no-secret assertion would be vacuous")
        for f in written:
            self.assertNotIn(PAT.lower(), f.lower())


if __name__ == "__main__":
    unittest.main()
