#!/usr/bin/env python3
"""The scripted READ-ONLY session arm B of ``tests/_solo_parity.py`` runs.

Feature 260813-modular-core-adoption, task 81.

Split from ``_solo_parity`` because the two halves answer different questions
and change for different reasons: that module owns the COMPARISON and the
ledger, this one owns WHAT IS RUN -- the roster, the seeded scratch knowledge
base both sides read, the sandboxed environment, and the normalisation that
makes two runs from two different directories comparable at all.

The roster's one rule
---------------------
Every invocation must be READ-ONLY against the knowledge base, because the
whole arm rests on both sides reading byte-identical input. That is asserted,
not trusted: ``fingerprint_kb`` is taken before and after each side's run and a
change fails the gate (``_solo_parity.verdict``). ``features``, ``learn`` and
``migrate --apply`` are therefore deliberately absent -- ``migrate --list`` is
the read-only half of the same verb and is in.

Why the environment is stripped rather than merely set
------------------------------------------------------
``AGENTWARE_ORG_ROOT`` and ``AGENTWARE_TEAM`` are REMOVED, not set to "": the
org resolver's precedence rule (R-ORG-04) is that org keys UNSET must be
byte-identical to a flat single-team install, and an empty-string key is a
different state from an absent one. Removing them is what makes this arm a test
of the "team mode unset" clause rather than of a coincidence. ``$HOME`` is
redirected because both trees resolve their config pointer through it -- the
merged tree also honours ``AGENTWARE_CONFIG_HOME``, but ``aw-pre-merge`` reads
``os.path.expanduser("~")`` directly, so ``$HOME`` is the only isolation both
sides share.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys

#: One read-only invocation. ``{KB}``/``{TREE}``/``{HOME}`` are substituted.
#: Ids are stable: a ledger row names one.
SESSION = (
    ("config", ["config"]),
    ("config-json", ["config", "--format", "json"]),
    ("config-kdir", ["config", "--knowledge-dir-only"]),
    ("config-updater", ["config", "--updater-only"]),
    ("config-checkup", ["config", "--checkup-only"]),
    ("config-dream", ["config", "--dream-only"]),
    ("config-autocommit", ["config", "--kb-autocommit-only"]),
    ("config-handle", ["config", "--user-handle-only"]),
    ("config-skill-promote", ["config", "--skill-promote-only"]),
    ("index-validate", ["index", "validate"]),
    ("query-category", ["query", "--category", "learnings"]),
    ("query-id", ["query", "--id", "learn-parity-probe"]),
    ("query-tag", ["query", "--tag", "parity"]),
    ("query-json", ["query", "--tag", "parity", "--format", "json"]),
    ("query-missing", ["query", "--id", "learn-does-not-exist"]),
    ("recall", ["recall", "deterministic retrieval parity probe"]),
    ("recall-json", ["recall", "deterministic retrieval parity probe",
                     "--format", "json"]),
    ("recall-work", ["recall", "parity probe", "--scope", "work"]),
    ("recall-budget", ["recall", "parity probe", "--token-budget", "400"]),
    ("audit", ["audit"]),
    ("audit-json", ["audit", "--format", "json"]),
    ("migrate-list", ["migrate", "--list"]),
    ("metrics", ["metrics"]),
    ("whereami", ["whereami"]),
    ("profile", ["profile", "--path", "parityop"]),
    ("worklog-scan", ["worklog", "scan", "--path",
                      "{KB}/work/260101-parity/worklog.md"]),
    ("plan-lint", ["plan", "lint", "--path",
                   "{KB}/work/260101-parity/plan.md"]),
    ("skill-list", ["skill", "list"]),
    ("skill-search", ["skill", "search", "security"]),
    ("skill-propose-dry", ["skill", "propose", "--dry-run"]),
    ("steering-lint", ["steering", "lint"]),
)

#: Seeded so every retrieval row has something deterministic to rank.
SEED_LEARNING = (
    "--topic", "parity-probe",
    "--summary", "a seeded probe learning for the solo-parity session",
    "--tags", "parity,probe",
    "--content", ("Deterministic retrieval over a seeded corpus. This body "
                  "exists so recall, query and the index validator all have "
                  "identical input on both sides of the merge."),
)

SEED_PLAN = """# Plan — solo parity probe

> Feature: `260101-parity`
> Created: 2026-01-01
> Status: running

## Context

A minimal plan so `plan lint` has a real artifact to read on both sides.

## Tasks

- ⬜ **1** P1 Probe task.
  *Verify:* the linter reads this file
"""

SEED_WORKLOG = """# Worklog — solo parity probe

## 2026-01-01 · seed

Seeded so `worklog scan` has markers to find.

> LEARNED: a seeded marker the scanner must report as unpromoted.

> DECISION: a seeded decision marker. Options: none. Chose: none. Reversible.
"""

#: Paths whose content is DERIVED, not authored: a side that rebuilds one has
#: not mutated the operator's knowledge, and both sides must be free to.
DERIVED_KB_PREFIXES = (".cache", "logs", ".loop", "FEATURES.md")

#: Volatile shapes in captured output. Each maps to a stable token so two runs
#: minutes apart from two directories compare as one string.
_SCRUB = (
    (re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?"), "<TS>"),
    (re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"), "<TS>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}"), "<DATE>"),
    (re.compile(r"\b\d+\.\d+s\b"), "<DUR>"),
    (re.compile(r"\b\d+(?:\.\d+)?\s?ms\b"), "<DUR>"),
)


def session_env(kb, home):
    """A hermetic environment with team mode UNSET (see the module docstring)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENTWARE_")}
    env["HOME"] = home
    env["AGENTWARE_KNOWLEDGE_DIR"] = kb
    env["COLUMNS"] = "80"
    env["LC_ALL"] = "C.UTF-8"
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for key in ("AGENTWARE_ORG_ROOT", "AGENTWARE_TEAM"):
        env.pop(key, None)
    return env


def cli_argv(root, argv, kb, home):
    """One roster row's argv, with its placeholders resolved."""
    subs = {"{KB}": kb, "{TREE}": root, "{HOME}": home}
    resolved = []
    for arg in argv:
        for token, value in subs.items():
            arg = arg.replace(token, value)
        resolved.append(arg)
    return [sys.executable, os.path.join(root, "scripts", "agentware")] + resolved


def invoke(root, argv, kb, home, timeout=180):
    """Run one invocation from inside ``root``; never raises on failure."""
    try:
        proc = subprocess.run(cli_argv(root, argv, kb, home), cwd=root,
                              capture_output=True, text=True,
                              env=session_env(kb, home), timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "TIMEOUT after %ss" % timeout


def normalize(text, root, kb, home):
    """Erase every value that is about WHERE this ran rather than WHAT ran."""
    for needle, token in sorted(((root, "<TREE>"), (kb, "<KB>"),
                                 (home, "<HOME>")),
                                key=lambda p: -len(p[0])):
        text = text.replace(needle, token)
        text = text.replace(os.path.realpath(needle), token)
    for pattern, token in _SCRUB:
        text = pattern.sub(token, text)
    return text


def run_session(root, kb, home, roster=SESSION):
    """``{id: {argv, rc, out, err}}`` for one tree, normalised."""
    rows = {}
    for rid, argv in roster:
        rc, out, err = invoke(root, argv, kb, home)
        rows[rid] = {"argv": list(argv), "rc": rc,
                     "out": normalize(out, root, kb, home),
                     "err": normalize(err, root, kb, home)}
    return rows


def seed_kb(tool_root, workdir, home):
    """Build the ONE scratch knowledge base both sides get a copy of.

    Seeded with ``aw-pre-merge``'s own CLI on purpose: the corpus must not be
    authored by the tree under test, or a difference in the writer would be
    read as a difference in the readers.
    """
    kb = os.path.join(workdir, "kb-template")
    env = session_env(kb, home)
    entry = os.path.join(tool_root, "scripts", "agentware")

    def run(*args):
        proc = subprocess.run([sys.executable, entry] + list(args),
                              cwd=tool_root, capture_output=True, text=True,
                              env=env)
        if proc.returncode != 0:
            raise RuntimeError("seed step %r failed: %s"
                               % (args[:2], (proc.stdout + proc.stderr)[-600:]))
        return proc.stdout

    run("init", "--knowledge-dir", kb)
    run("learn", *SEED_LEARNING)
    feature = os.path.join(kb, "work", "260101-parity")
    os.makedirs(feature, exist_ok=True)
    for name, body in (("plan.md", SEED_PLAN), ("worklog.md", SEED_WORKLOG)):
        with open(os.path.join(feature, name), "w", encoding="utf-8") as fh:
            fh.write(body)
    return kb


def fingerprint_kb(kb):
    """Content hash of every AUTHORED file under ``kb`` (derived paths out)."""
    digest = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(kb):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, kb)
            if rel.startswith(DERIVED_KB_PREFIXES):
                continue
            digest.update(rel.encode("utf-8"))
            with open(path, "rb") as fh:
                digest.update(hashlib.sha256(fh.read()).digest())
    return digest.hexdigest()


def dump(root, kb, home, out):
    """Debug helper: write one side's session rows as JSON."""
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(run_session(root, kb, home), fh, indent=1, sort_keys=True)
