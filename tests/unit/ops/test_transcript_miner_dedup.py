"""TestErrorRecoveryDedup -- split from `test_transcript_miner` (task 58).

See `tests.unit.ops._transcript_miner_support` for the
shared header.
"""

from tests.unit.ops._transcript_miner_support import (  # noqa: F401
    _MinerBase, json, mock, os, setUpModule, tearDownModule)


class TestErrorRecoveryDedup(_MinerBase):
    """F-E (260722 miner go-live gate): error-recovery emission dedups on a
    session-INDEPENDENT key (tool + target-CLASS + normalized signature) backed
    by a PERSISTENT cross-run ledger (state err_emitted_keys) — because
    _prefer_cand_fp embeds sid, the same failure mode recovered in a NEW
    session on a LATER run derives a FRESH fp and the fp belt cannot catch it.
    Exercised through the REAL _transcript_mine path (real state write/reload),
    never a hand-written ledger."""

    def _err_fix(self, target, sig="No such file or directory", tool="Read"):
        return [self._call(tool, "ERR", target, sig),
                self._call(tool, "ok", target, "contents")]

    def test_same_mode_across_two_separate_runs_emits_once(self):
        # THE two-run collapse (go-live safety): run 1 emits + seeds the
        # persistent ledger; the SAME failure mode from a NEW session on a
        # LATER run is suppressed even though its _prefer_cand_fp is fresh.
        self._seed_zero()
        self._write_session("A", self._err_fix("/tmp/one.md"))
        self.assertEqual(self._mine(), 1)
        self._write_session("B", self._err_fix("/tmp/two.md"))
        self.assertEqual(self._mine(), 0,
                         "cross-RUN duplicate must be suppressed by the "
                         "persistent session-independent ledger")
        self.assertEqual(len(self._queue()), 1)
        self.assertEqual(self._queue()[0]["source_sessions"], ["A"])

    def test_same_mode_across_sessions_in_one_batch_collapses(self):
        self._seed_zero()
        self._write_session("A", self._err_fix("/tmp/one.md"))
        self._write_session("B", self._err_fix("/tmp/two.md"))
        self.assertEqual(self._mine(), 1, "N sessions, one mode -> ONE candidate")

    def test_distinct_signature_still_emits(self):
        self._seed_zero()
        self._write_session("A", self._err_fix("/tmp/x.md"))
        self.assertEqual(self._mine(), 1)
        self._write_session("B", self._err_fix("/tmp/y.md",
                                               sig="Permission denied"))
        self.assertEqual(self._mine(), 1,
                         "a genuinely-distinct failure signature still emits")

    def test_distinct_tool_still_emits(self):
        self._seed_zero()
        self._write_session("A", self._err_fix("/tmp/x.md", tool="Read"))
        self.assertEqual(self._mine(), 1)
        self._write_session("B", self._err_fix("/tmp/y.md", tool="Edit"))
        self.assertEqual(self._mine(), 1)

    def test_volatile_signature_parts_are_masked(self):
        # Paths / digits inside the signature differ per occurrence of the SAME
        # mode; the class-mode normalization masks them so the mode collapses.
        self._seed_zero()
        self._write_session("A", self._err_fix(
            "/tmp/a.md", sig="ls: /tmp/a1: No such file or directory"))
        self._write_session("B", self._err_fix(
            "/tmp/b.md", sig="ls: /tmp/b2: No such file or directory"))
        self.assertEqual(self._mine(), 1)

    def test_ledger_is_persisted_session_independent_and_deterministic(self):
        self._seed_zero()
        self._write_session("A", self._err_fix("/tmp/one.md"))
        self.assertEqual(self._mine(), 1)
        keys = self._state()["err_emitted_keys"]
        self.assertEqual(keys, [self.mod._transcript_errrec_key(
            "Read", "/tmp/one.md", "No such file or directory", "class")])
        self.assertNotIn("|A|", keys[0], "key must not embed the sid")
        # Determinism: a DIFFERENT session + concrete path, same mode, same key.
        self.assertEqual(keys[0], self.mod._transcript_errrec_key(
            "Read", "/tmp/two.md", "No such file or directory", "class"))

    def test_dedup_off_restores_legacy_per_session_emission(self):
        with mock.patch.dict(os.environ,
                             {self.mod.MINER_ERRREC_DEDUP_KEY: "off"}):
            self._seed_zero()
            self._write_session("A", self._err_fix("/tmp/one.md"))
            self._write_session("B", self._err_fix("/tmp/two.md"))
            self.assertEqual(self._mine(), 2)
            self.assertEqual(self._state()["err_emitted_keys"], [],
                             "'off' neither consults nor grows the ledger")

    def test_exact_mode_distinguishes_raw_targets(self):
        with mock.patch.dict(os.environ,
                             {self.mod.MINER_ERRREC_DEDUP_KEY: "exact"}):
            self._seed_zero()
            self._write_session("A", self._err_fix("/tmp/one.md"))
            self._write_session("B", self._err_fix("/tmp/two.md"))
            self.assertEqual(self._mine(), 2, "exact mode keys on raw target")
            self._write_session("C", self._err_fix("/tmp/one.md"))
            self.assertEqual(self._mine(), 0,
                             "same raw target+sig still collapses cross-run")

    def test_invalid_mode_config_degrades_to_the_default(self):
        with mock.patch.dict(os.environ,
                             {self.mod.MINER_ERRREC_DEDUP_KEY: "banana"}):
            self._seed_zero()
            self._write_session("A", self._err_fix("/tmp/one.md"))
            self._write_session("B", self._err_fix("/tmp/two.md"))
            self.assertEqual(self._mine(), 1, "typo degrades to 'class'")

    def test_prefer_cand_fp_is_untouched(self):
        # The legitimate cross-producer dedup contract: fp still embeds sid so
        # a stop-hook record and a miner record for the SAME (sid, turn, text)
        # collapse, while different sids stay distinct. F-E must not change it.
        fp_a = self.mod._prefer_cand_fp("A", "t1", "x")
        self.assertEqual(fp_a, self.mod._prefer_cand_fp("A", "t1", "x"))
        self.assertNotEqual(fp_a, self.mod._prefer_cand_fp("B", "t1", "x"))

    def test_legacy_state_without_err_ledger_loads_clean(self):
        self._write_state({"offsets": {"prompts": 7, "metrics": 0},
                           "processed_sessions": [], "backfill_pending": [],
                           "tally": {}, "emitted_keys": []})
        st, fresh = self.mod._transcript_load_state(self.kdir)
        self.assertFalse(fresh, "legacy state must NOT be re-seeded")
        self.assertEqual(st["err_emitted_keys"], [])
        self.assertEqual(st["offsets"]["prompts"], 7, "offsets preserved")

    def test_err_ledger_is_bounded_via_real_mining(self):
        # Wiring lock (adversarial-review): the ERR ledger must actually be
        # pruned INSIDE _transcript_mine, not merely by the shared helper in
        # isolation. Drive real mining past a LOW cap over distinct modes and
        # assert err_emitted_keys is capped with the oldest dropped.
        with mock.patch.object(self.mod, "MINER_EMITTED_CAP", 5):
            self._seed_zero()
            n = 8
            for i in range(n):
                self._write_session("s%d" % i, self._err_fix(
                    "/tmp/f%d.md" % i,
                    sig="distinct error alpha%s bravo" % chr(97 + i)))
            self.assertEqual(self._mine(), n, "each distinct mode emits once")
            keys = self._state()["err_emitted_keys"]
            self.assertEqual(len(keys), 5, "ledger capped at MINER_EMITTED_CAP")

    # --- adversarial-review F-E hardening regression locks ----------------
    def test_err_target_class_pins_url_path_and_command_shapes(self):
        # Direct pinning of _transcript_err_target_class (adversarial-review
        # HIGH: the class component otherwise has ZERO regression lock — dropping
        # it from the key kept the whole suite green).
        C = self.mod._transcript_err_target_class
        # url -> host, with credentials AND port stripped so neither enters the
        # persistent ledger.
        self.assertEqual(C("https://api.example.com/v1/x"), "url:api.example.com")
        self.assertEqual(C("http://user:pw@host.io:8080/p"), "url:host.io")
        # path -> extension.
        self.assertEqual(C("/a/b/c.md"), "path:.md")
        self.assertEqual(C("/repo/src/main.py"), "path:.py")
        # command (internal space) -> first token, NEVER a path segment.
        self.assertEqual(C("npm run build -- ./src"), "cmd:npm")
        self.assertEqual(C("make 2>/tmp/log.123"), "cmd:make")
        self.assertEqual(C("pytest"), "cmd:pytest")
        # extensionless per-session temp names collapse to ONE masked class.
        self.assertEqual(C("/var/folders/T/tmp_ab12cd"),
                         C("/var/folders/T/tmp_ef34gh"))
        self.assertTrue(C("/var/folders/T/tmp_ab12cd").startswith("path:"))

    def test_target_class_distinguishes_same_tool_and_signature(self):
        # Two candidates sharing tool AND normalized signature but differing ONLY
        # in target class must BOTH emit — dropping the class from the key would
        # collapse them (silent suppression). Mine-level lock for the HIGH.
        self._seed_zero()
        self._write_session("A", self._err_fix("/tmp/x.md"))
        self.assertEqual(self._mine(), 1)
        self._write_session("B", self._err_fix("/tmp/y.py"))
        self.assertEqual(self._mine(), 1,
                         "same tool+signature, DIFFERENT class -> distinct key")

    def _cmd_fix(self, command, sig="build failed", tool="Bash"):
        inp = json.dumps({"command": command})
        return [self._call(tool, "ERR", input_override=inp, response=sig),
                self._call(tool, "ok", input_override=inp, response="done")]

    def test_command_with_path_arg_keys_on_binary_not_path(self):
        # A command carrying a path argument must key on its binary (cmd:npm),
        # never be misrouted into the path branch by the embedded '/'
        # (adversarial-review MEDIUM).
        self._seed_zero()
        self._write_session("A", self._cmd_fix("npm run build"))
        self.assertEqual(self._mine(), 1)
        self._write_session("B", self._cmd_fix("npm run build -- ./src/app.ts"))
        self.assertEqual(self._mine(), 0,
                         "same binary+signature collapses despite a path arg")

    def test_distinct_command_binaries_still_emit(self):
        self._seed_zero()
        self._write_session("A", self._cmd_fix("npm run build"))
        self.assertEqual(self._mine(), 1)
        self._write_session("B", self._cmd_fix("make build"))
        self.assertEqual(self._mine(), 1, "different binary -> distinct class")

    def test_extensionless_temp_targets_collapse_across_sessions(self):
        # Volatility in the TARGET (per-session temp name), not the message:
        # unmasked, N sessions flood; masked, they collapse (adversarial-review
        # MEDIUM).
        self._seed_zero()
        self._write_session("A", self._err_fix("/var/folders/T/tmp_ab12cd"))
        self.assertEqual(self._mine(), 1)
        self._write_session("B", self._err_fix("/var/folders/T/tmp_ef34gh"))
        self.assertEqual(self._mine(), 0,
                         "same mode on per-session temp names must collapse")

    def test_alphanumeric_volatile_tokens_in_signature_are_masked(self):
        # sha256 digests / random ids in the signature differ per occurrence of
        # the SAME mode; they carry no 0x prefix and survive plain digit-masking
        # as fragments, so they must be masked as whole tokens (adversarial-
        # review MEDIUM).
        self._seed_zero()
        self._write_session("A", self._err_fix(
            "/tmp/a.md", sig="error pulling image sha256:9f2a3b1c4d5e6f70"))
        self._write_session("B", self._err_fix(
            "/tmp/b.md", sig="error pulling image sha256:7d4e0a55c1b2e3f4"))
        self.assertEqual(self._mine(), 1,
                         "same mode with a volatile digest must collapse")

    def test_uuid_bearing_signatures_collapse(self):
        self._seed_zero()
        self._write_session("A", self._err_fix(
            "/tmp/a.md",
            sig="job 550e8400-e29b-41d4-a716-446655440000 failed"))
        self._write_session("B", self._err_fix(
            "/tmp/b.md",
            sig="job 7c9e6679-7425-40de-944b-e07fc1f90ae7 failed"))
        self.assertEqual(self._mine(), 1, "uuid-bearing same mode collapses")

    def test_kernel_bait_is_bounded_but_never_blinds_a_legit_recovery(self):
        # A tier-3 kernel-path recovery is EMITTED (the drain rejects it) AND
        # ledgered — under its OWN namespace. Both halves matter: exempting it
        # from the ledger reopens an ungated once-per-session flood on the
        # kernel path, while sharing the namespace lets a rejected bait blind a
        # legitimate same tool/class/signature learning (adversarial-review
        # rounds 1-2).
        self._seed_zero()
        self._write_session("A", self._err_fix(
            "/repo/AGENTS.md", sig="Permission denied", tool="Edit"))
        self.assertEqual(self._mine(), 1,
                         "kernel bait still emits (the drain rejects it)")
        keys = self._state()["err_emitted_keys"]
        self.assertEqual(len(keys), 1)
        self.assertTrue(keys[0].startswith("errrec3|"),
                        "tier-3 keys live in their OWN namespace: %s" % keys[0])
        # BOUNDED: the SAME tier-3 mode in a NEW session must not re-emit.
        self._write_session("B", self._err_fix(
            "/repo/AGENTS.md", sig="Permission denied", tool="Edit"))
        self.assertEqual(self._mine(), 0,
                         "a tier-3 mode must be bounded, not an ungated path")
        # ...yet a LEGITIMATE recovery sharing tool+class+signature still emits.
        self._write_session("C", self._err_fix(
            "/w/design.md", sig="Permission denied", tool="Edit"))
        self.assertEqual(self._mine(), 1,
                         "a legit same-class recovery must not be blinded")

    def test_short_error_codes_stay_distinct_discriminators(self):
        # 'a genuinely-distinct failure still emits': when the ONLY difference
        # is a short structured code, masking it would silently suppress the
        # second mode forever (adversarial-review round 2 HIGH).
        self._seed_zero()
        self._write_session("A", self._err_fix("/tmp/a.md", sig="build failed E0001"))
        self.assertEqual(self._mine(), 1)
        self._write_session("B", self._err_fix("/tmp/b.md", sig="build failed E0002"))
        self.assertEqual(self._mine(), 1,
                         "a distinct error CODE is a discriminator, not noise")

    def test_command_binary_with_digits_is_not_collapsed(self):
        # Masking the command binary mapped unrelated tools onto cmd:<id>
        # (adversarial-review round 2 HIGH) — binaries are discriminators.
        C = self.mod._transcript_err_target_class
        self.assertEqual(C("s3cmd sync ./a"), "cmd:s3cmd")
        self.assertEqual(C("py3 script"), "cmd:py3")
        self.assertNotEqual(C("s3cmd sync ./a"), C("py3 script"))

    def test_space_bearing_path_is_not_read_as_a_command(self):
        # A path sigil beats the space heuristic (adversarial-review round 2).
        C = self.mod._transcript_err_target_class
        self.assertEqual(C("/tmp/my report.md"), "path:.md")
        self.assertEqual(C("~/notes/my file.txt"), "path:.txt")

    def test_command_wrapper_prefixes_are_skipped(self):
        C = self.mod._transcript_err_target_class
        self.assertEqual(C("sudo npm run build"), "cmd:npm")
        self.assertEqual(C("env FOO=bar pytest -q"), "cmd:pytest")
        self.assertEqual(C("/usr/local/bin/npm run build"), "cmd:npm")

    def test_volatile_url_host_is_masked_but_stable_hosts_stay_distinct(self):
        C = self.mod._transcript_err_target_class
        self.assertEqual(C("https://run-3f9a12bc7d.workers.dev/x"),
                         C("https://run-8b2e04af1c.workers.dev/y"))
        self.assertNotEqual(C("https://api.example.com/x"),
                            C("https://api.other.com/x"))
        # The registrable domain + TLD are the STABLE identity and must survive
        # masking, or two distinct hosts collapse (adversarial-review round 3).
        self.assertNotEqual(C("https://svc1234abcd.alpha.com/x"),
                            C("https://svc1234abcd.beta.com/x"))

    def test_tool_kind_decides_cmd_vs_path_not_the_string_shape(self):
        # Every purely-lexical cmd-vs-path heuristic has a dual failure mode;
        # the producers name the tool, so the TOOL KIND is authoritative
        # (adversarial-review round 3).
        C = self.mod._transcript_err_target_class
        # A command whose LAST argument ends in an extension is still a COMMAND.
        self.assertEqual(C("./run.sh --out report.md", "Bash"), "cmd:run.sh")
        self.assertEqual(C("pytest tests/test_x.py", "Bash"), "cmd:pytest")
        self.assertNotEqual(C("./a.sh --out report.md", "Bash"),
                            C("./b.sh --out report.md", "Bash"))
        # A path-tool target is always a PATH, even with a space.
        self.assertEqual(C("/tmp/my report.md", "Read"), "path:.md")
        self.assertEqual(C("relative name.txt", "Edit"), "path:.txt")

    def test_traceback_preamble_does_not_collapse_distinct_failures(self):
        # A Python traceback's FIRST line is constant across every genuinely-
        # distinct failure; keying on it would derive ONE key and the persistent
        # ledger would suppress them all forever (adversarial-review round 3).
        S = self.mod._transcript_err_signature
        tb1 = ("Traceback (most recent call last):\n"
               "  File \"a.py\", line 2, in <module>\n"
               "ValueError: bad input")
        tb2 = ("Traceback (most recent call last):\n"
               "  File \"b.py\", line 9, in <module>\n"
               "KeyError: missing key")
        self.assertEqual(S({"response": tb1}), "ValueError: bad input")
        self.assertEqual(S({"response": tb2}), "KeyError: missing key")
        self.assertNotEqual(S({"response": tb1}), S({"response": tb2}))
        # A normal single-line error is untouched.
        self.assertEqual(S({"response": "No such file or directory"}),
                         "No such file or directory")

    def test_distinct_tracebacks_both_emit_through_the_real_miner(self):
        self._seed_zero()
        self._write_session("A", self._err_fix(
            "/tmp/a.py", sig="Traceback (most recent call last):\nValueError: x"))
        self.assertEqual(self._mine(), 1)
        self._write_session("B", self._err_fix(
            "/tmp/b.py", sig="Traceback (most recent call last):\nKeyError: y"))
        self.assertEqual(self._mine(), 1,
                         "distinct tracebacks are distinct failure modes")

    def test_suppressed_pair_does_not_drop_later_distinct_pairs(self):
        # The dedup `continue` must skip ONLY the suppressed pair — never abort
        # the rest of the session's timeline. A `break` (or any early exit)
        # would silently drop every later learning in that session while the
        # whole suite stayed green (adversarial-review round 3).
        self._seed_zero()
        self._write_session("A", self._err_fix("/tmp/x.md"))
        self.assertEqual(self._mine(), 1)
        self._write_session("B", self._err_fix("/tmp/y.md")
                            + self._err_fix("/tmp/z.py",
                                            sig="Permission denied",
                                            tool="Edit"))
        self.assertEqual(self._mine(), 1,
                         "the DISTINCT pair after a suppressed one must emit")

    def test_dedup_mode_read_from_config_env_when_env_var_absent(self):
        # The middle resolver rung (env -> config.env -> default): a hand-edited
        # config.env value is honored when the env var is unset (critic gap).
        cfg = os.path.join(self.kdir, "config.env")
        with open(cfg, "w", encoding="utf-8") as f:
            f.write("%s=off\n" % self.mod.MINER_ERRREC_DEDUP_KEY)
        with mock.patch.object(self.mod, "CONFIG_PATHS", [cfg]), \
                mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(self.mod.MINER_ERRREC_DEDUP_KEY, None)
            self._seed_zero()
            self._write_session("A", self._err_fix("/tmp/one.md"))
            self._write_session("B", self._err_fix("/tmp/two.md"))
            self.assertEqual(self._mine(), 2,
                             "config.env 'off' honored -> legacy emission")

    # --- adversarial-review round-4 remediation regression locks ----------
    def test_signature_strips_exit_code_preamble(self):
        # `Exit code N` is the DOMINANT real Bash preamble and is CONSTANT
        # across every genuinely-distinct failure; keying on it collapses them
        # all onto one permanent F-E key. The signature must be the detail line.
        S = self.mod._transcript_err_signature
        self.assertEqual(
            S({"response": "Exit code 1\nModuleNotFoundError: no module "
                           "named requests"}),
            "ModuleNotFoundError: no module named requests")
        self.assertEqual(
            S({"response": "Exit code 1\nWORKLOG SCAN FAILED: 7 of 7 "
                           "marker(s) unpromoted"}),
            "WORKLOG SCAN FAILED: 7 of 7 marker(s) unpromoted")
        self.assertNotEqual(
            S({"response": "Exit code 1\nModuleNotFoundError: x"}),
            S({"response": "Exit code 1\nSyntaxError: invalid syntax"}))
        # a bare exit code (no detail) is the only line -> kept as-is
        self.assertEqual(S({"response": "Exit code 143"}), "Exit code 143")
        # codex FUSES the detail on the same line -> kept whole, not stripped
        self.assertEqual(S({"response": "Exit code 2: missing separator"}),
                         "Exit code 2: missing separator")

    def test_signature_keeps_java_exception_line_not_the_stack_frame(self):
        # The `exception in thread .*` preamble alternative used to swallow the
        # Java error (which IS on line 1) and key on the shared trailing stack
        # frame, collapsing NullPointerException and IllegalStateException.
        S = self.mod._transcript_err_signature
        a = ('Exception in thread "main" java.lang.NullPointerException: '
             's is null\n\tat java.base/java.lang.Thread.run(Thread.java:840)')
        b = ('Exception in thread "main" java.lang.IllegalStateException: '
             'pool closed\n\tat java.base/java.lang.Thread.run(Thread.java:840)')
        self.assertEqual(
            S({"response": a}),
            'Exception in thread "main" java.lang.NullPointerException: '
            's is null')
        self.assertNotEqual(S({"response": a}), S({"response": b}))

    def test_signature_skips_constant_trailing_footer(self):
        # A constant footer (`Run with --verbose ...`) is not the error; taking
        # the LAST line blindly would collapse distinct failures that share it.
        S = self.mod._transcript_err_signature
        a = ("Error:\ncannot open /etc/app.conf\n"
             "Run with --verbose for more information")
        b = ("Error:\ninvalid credentials for the registry\n"
             "Run with --verbose for more information")
        self.assertEqual(S({"response": a}), "cannot open /etc/app.conf")
        self.assertEqual(S({"response": b}),
                         "invalid credentials for the registry")
        self.assertNotEqual(S({"response": a}), S({"response": b}))

    def test_distinct_exit_code_failures_both_emit_through_the_miner(self):
        self._seed_zero()
        self._write_session("A", self._cmd_fix(
            "npm run build",
            sig="Exit code 1\nerror TS2304: Cannot find name 'Foo'."))
        self.assertEqual(self._mine(), 1)
        self._write_session("B", self._cmd_fix(
            "npm run build",
            sig="Exit code 1\nModuleNotFoundError: No module named 'yaml'"))
        self.assertEqual(self._mine(), 1,
                         "distinct detail behind the SAME Exit code preamble "
                         "must still emit")

    def test_cd_chained_commands_key_on_the_real_binary_not_cd(self):
        # `cd <dir> && <real cmd>` is the dominant real Bash shape; it must key
        # on the real binary, not bucket every unrelated tool onto cmd:cd.
        C = self.mod._transcript_err_target_class
        self.assertEqual(C("cd /repo && npm run build", "Bash"), "cmd:npm")
        self.assertEqual(C("cd /repo && pytest -q", "Bash"), "cmd:pytest")
        self.assertEqual(C("cd /tmp && make install", "Bash"), "cmd:make")
        # newline-joined (whitespace-collapsed) cd prefix, no explicit '&&'
        self.assertEqual(C("cd /repo scripts/agentware gate release", "Bash"),
                         "cmd:agentware")
        self.assertNotEqual(C("cd /a && npm run build", "Bash"),
                            C("cd /a && pytest -q", "Bash"))
        # a bare `cd` failure legitimately classes as cd
        self.assertEqual(C("cd /nonexistent", "Bash"), "cmd:cd")

    def test_cd_chained_distinct_binaries_do_not_share_a_ledger_key(self):
        self._seed_zero()
        self._write_session("A", self._cmd_fix("cd /repo && npm run build"))
        self.assertEqual(self._mine(), 1)
        self._write_session("B", self._cmd_fix("cd /other && make install"))
        self.assertEqual(self._mine(), 1,
                         "a DIFFERENT real binary behind cd -> distinct class")
        self._write_session("C", self._cmd_fix("cd /third && npm run test"))
        self.assertEqual(self._mine(), 0,
                         "SAME real binary+signature collapses despite cd")

    def test_rotated_log_extensions_collapse_to_a_bounded_class(self):
        C = self.mod._transcript_err_target_class
        self.assertEqual(C("/var/log/app.log.20260720"), "path:.log")
        self.assertEqual(C("/var/log/app.log.20260721"), "path:.log")
        self.assertEqual(C("/var/log/app.log.1"), "path:.log")       # logrotate
        self.assertEqual(C("/data/db.sql.1690000001"), "path:.sql")  # epoch bkp
        self.assertEqual(C("/tmp/build.log.38271"), "path:.log")     # pid suffix
        # real type extensions are still discriminators
        self.assertEqual(C("/a/b/c.py"), "path:.py")
        self.assertNotEqual(C("/a/b/c.py"), C("/a/b/c.md"))

    def test_rotated_logs_collapse_across_sessions(self):
        self._seed_zero()
        self._write_session("A", self._err_fix("/var/log/app.log.20260720"))
        self.assertEqual(self._mine(), 1)
        self._write_session("B", self._err_fix("/var/log/app.log.20260721"))
        self.assertEqual(self._mine(), 0,
                         "rotated logs are ONE bounded class, not N per stamp")

    def test_url_password_with_at_sign_does_not_leak_a_credential(self):
        # `(?:[^@/\\s]*@)?` stripped only ONE '@'; a password containing '@'
        # leaked its tail into the persistent ledger. Strip ALL userinfo.
        C = self.mod._transcript_err_target_class
        self.assertEqual(C("http://user:p@w@h.io/x"), "url:h.io")
        self.assertNotIn("p@w", C("http://user:p@w@h.io/x"))
        self.assertNotIn("@", C("http://user:p@w@h.io/x"))
        # existing behavior preserved: single userinfo + port stripped
        self.assertEqual(C("http://user:pw@host.io:8080/p"), "url:host.io")
        self.assertEqual(C("https://api.example.com/v1/x"), "url:api.example.com")

    def _live_ok(self, cmd, ts, resp="passed"):
        return {"ts": ts, "tool": "Bash", "status": "ok",
                "input": json.dumps({"command": cmd}), "response": resp}

    def test_deep_truncated_tracebacks_stay_distinct_through_reconciler(self):
        # A >MAXLEN traceback differing ONLY in its final exception line must
        # NOT collapse: the tail-preserving response truncation keeps the
        # discriminating exception, so distinct deep failures still both emit.
        self._seed_zero()
        frames = "".join(
            '  File "/venv/lib/python3.11/site-packages/pkg/mod%d.py", '
            'line %d, in fn%d\n    do_call_%d()\n' % (i, i, i, i)
            for i in range(70))

        def tb(exc):
            return "Traceback (most recent call last):\n" + frames + exc
        a = tb("ValueError: unsupported currency 'XBT'")
        b = tb("KeyError: 'customer_id'")
        self.assertGreater(len(a), self.mod.MINER_TRANSCRIPT_MAXLEN)
        cmd = "pytest -q"
        self._write_session("A", [self._live_ok(cmd, "2026-07-22T10:05:00Z")])
        self._write_transcript("A", [
            ("Bash", {"command": cmd}, True, a, "2026-07-22T10:00:00.1Z")])
        self.assertEqual(self._mine(), 1)
        self._write_session("B", [self._live_ok(cmd, "2026-07-22T11:05:00Z")])
        self._write_transcript("B", [
            ("Bash", {"command": cmd}, True, b, "2026-07-22T11:00:00.1Z")])
        self.assertEqual(self._mine(), 1,
                         "distinct deep-traceback modes must survive truncation")

    def test_setup_neutralizes_ambient_errrec_dedup_knob(self):
        # Hermeticity lock: an operator who EXPORTS AGENTWARE_MINER_ERRREC_DEDUP
        # must not silently flip the suite. Simulate the ambient export, re-run
        # setUp, and assert the gate resolves to the shipped default.
        os.environ[self.mod.MINER_ERRREC_DEDUP_KEY] = "off"
        self.addCleanup(os.environ.pop, self.mod.MINER_ERRREC_DEDUP_KEY, None)
        self.setUp()
        self.assertEqual(self.mod._transcript_errrec_gate()["mode"], "class",
                         "setUp must neutralize an ambient dedup knob")
