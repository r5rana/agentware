#!/usr/bin/env python3
"""Capture a pre-existing test + gate failure baseline for one tree.

Feature 260813-modular-core-adoption, task 5.

"Green" for the whole migration means **no worse than the recorded baseline**,
never "all pass". Both the local tree and the donor tree carry pre-existing
failures; this tool records them per module so a later gate can diff against
them instead of demanding an all-green suite that never existed.

What it records, per tree:

  * every test module under ``<root>/tests`` (recursively), run as its own
    ``python3 -m unittest <dotted>`` subprocess, with pass / fail / error /
    skip counts parsed from the runner summary. Module runs carry the
    package's own ``AGENTWARE_NESTED_UNITTEST=1`` recursion guard, exactly as
    the ``audit`` verb sets it before shelling the suite: several modules
    exercise verbs that themselves shell ``unittest discover -s tests``, so
    without the guard each of those re-runs the entire suite inside its own
    row -- quadratic wall time, and a false ``timeout`` red for any module
    whose nested suite outlives the per-module budget;
  * the three package gates -- ``steering lint``, ``audit`` and
    ``gate release`` -- each with its exit code and, where the verb speaks
    JSON, its per-check pass/fail breakdown;
  * a knowledge-base fingerprint taken before and after the run, so a suite
    that mutates the operator's real knowledge base is caught rather than
    assumed away.

The CLI is always invoked as ``<python> <root>/scripts/agentware`` rather than
executed directly: the donor tree lives on a mount where the exec bit is not
honoured, and going through the interpreter keeps both trees on one code path.

Usage:
    python3 tests/_baseline_capture.py --root <tree> --label local --out <path>
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import time

SCHEMA_VERSION = 1

# Modules are run one subprocess each; a hung module must not hang the capture.
DEFAULT_MODULE_TIMEOUT = 900
DEFAULT_GATE_TIMEOUT = 900

RAN_RE = re.compile(r"^Ran (\d+) tests? in ", re.M)
STATUS_RE = re.compile(r"^(OK|FAILED)(?:\s+\((?P<detail>.*)\))?\s*$", re.M)
DETAIL_RE = re.compile(r"(failures|errors|skipped|expected failures|unexpected successes)=(\d+)")

# KB subtrees whose content must be unchanged by a read-only baseline capture.
KB_FINGERPRINT_DIRS = ("learnings", "decisions", "skills", "projects", "references")

# Directories under tests/ holding fixture DATA rather than suite modules. Their
# contents are never collected by `unittest discover -s tests` and several live
# under non-identifier directory names, so a dotted-module run of one can only
# ever report "module not found".
NON_SUITE_DIRS = ("fixtures",)

# The package's own recursion guard, set by the `audit` verb before it shells
# the suite. Applied to module runs so a module exercising a suite-shelling verb
# records its OWN result rather than a nested full-suite run. Gates are run
# WITHOUT it, so each gate row reflects what an operator invoking it would see.
NESTED_GUARD_ENV = {"AGENTWARE_NESTED_UNITTEST": "1"}


# --- generic helpers ---------------------------------------------------------

def run(cmd, cwd, timeout, env=None):
    """Run a command, never raising on failure. Returns (rc, stdout, stderr)."""
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, timeout=timeout, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except subprocess.TimeoutExpired:
        return None, "", "TIMEOUT after %ss" % timeout
    except OSError as exc:
        return None, "", "OSERROR: %s" % exc
    return proc.returncode, proc.stdout, proc.stderr


def _identity_needles():
    """The LIVE operator-identity needles, resolved once per process.

    Resolved lazily and tolerantly: a capture must still run on a machine with no
    knowledge dir configured (a fresh clone, CI), where there is simply nothing to
    redact. Any failure degrades to "no needles" rather than aborting the capture.
    """
    global _NEEDLES
    if _NEEDLES is None:
        try:
            sys.path.insert(0, os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
            import agentware_lib.core.config as _cfg
            import agentware_lib.knowledge.privacy as _priv
            _NEEDLES = tuple(_priv.operator_identity_strings(
                _cfg.resolve_knowledge_dir()))
        except Exception:
            _NEEDLES = ()
    return _NEEDLES


_NEEDLES = None


def redact(text):
    """Strip operator identity from any text this capture is about to COMMIT.

    Recorded subprocess output is not neutral data: a failing test's traceback
    carries absolute paths, and an assertion message carries whatever value the
    test compared — which is how an operator handle ends up committed inside a
    fixture nobody thinks of as personal data. The scan in
    `agentware_lib.knowledge.privacy` grades the tree AFTER the fact; this is the
    write-side counterpart, sharing that module's needle set so the two cannot
    disagree about what identity means.
    """
    try:
        sys.path.insert(0, os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
        import agentware_lib.knowledge.privacy as _priv
        return _priv.redact_operator_identity(text, _identity_needles())
    except Exception:
        return text


def neutralise(text):
    """Strip VENDOR identity from any text this capture is about to COMMIT.

    The sibling of `redact`, and it exists because the two answer different
    questions with the same urgency: `redact` removes the OPERATOR from a
    committed traceback, this removes the ENVIRONMENT the donor tree ran in. A
    failing donor test quotes its own assertion message, and three donor tests
    assert on a corporate build system and auth cookie by name -- which is how a
    vendor token ends up committed inside a fixture nobody thinks of as vendor
    data. `tests/_neutrality_redact.py` shares the five-surface gate's needle
    set, so the write side and the scan that grades it cannot disagree.
    """
    try:
        import _neutrality_redact as _neutral
    except ImportError:  # pragma: no cover - direct-script invocation
        from tests import _neutrality_redact as _neutral
    try:
        compiled, _meta = _neutral.load_compiled()
    except (ValueError, OSError):
        return text
    return _neutral.redact_text(text, compiled)


def tail(text, limit=2000):
    text = neutralise(redact((text or "").strip()))
    if len(text) <= limit:
        return text
    return "..." + text[-limit:]


# --- test-module discovery and execution -------------------------------------

def discover_modules(root):
    """Return sorted dotted module names for every test file under <root>/tests.

    Mirrors what ``unittest discover -s tests`` can actually collect: fixture
    data directories are pruned, and so is any path whose components are not
    importable as a dotted module (the SWE task fixtures sit under hyphenated
    directory names, which no dotted run could ever resolve).
    """
    tests_dir = os.path.join(root, "tests")
    found = []
    for dirpath, dirnames, filenames in os.walk(tests_dir):
        dirnames[:] = [d for d in dirnames
                       if d != "__pycache__" and d not in NON_SUITE_DIRS]
        for name in filenames:
            if not (name.startswith("test_") and name.endswith(".py")):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), root)
            parts = rel[: -len(".py")].split(os.sep)
            if not all(part.isidentifier() for part in parts):
                continue
            found.append(".".join(parts))
    return sorted(found)


def parse_unittest_summary(rc, out, err):
    """Parse a unittest run into a status + counts row.

    unittest writes its summary to stderr; both streams are scanned so the row
    survives a module that redirects one of them.
    """
    blob = (err or "") + "\n" + (out or "")
    row = {
        "ran": 0, "passed": 0, "failures": 0, "errors": 0,
        "skipped": 0, "expected_failures": 0, "unexpected_successes": 0,
    }

    ran_match = RAN_RE.search(blob)
    status_match = STATUS_RE.search(blob)

    if ran_match is None:
        if rc is None:
            return "timeout", row
        # No summary line at all: the interpreter died before the runner
        # reported (crash, syntax error at import of the runner itself).
        return "crash", row

    row["ran"] = int(ran_match.group(1))

    detail = ""
    outcome = "unknown"
    if status_match:
        outcome = status_match.group(1)
        detail = status_match.group("detail") or ""
    for key, value in DETAIL_RE.findall(detail):
        row[key.replace(" ", "_")] = int(value)

    row["passed"] = (row["ran"] - row["failures"] - row["errors"]
                     - row["skipped"] - row["unexpected_successes"])
    if row["passed"] < 0:
        row["passed"] = 0

    if outcome == "OK":
        status = "ok"
    elif outcome == "FAILED":
        status = "failed"
    else:
        status = "unknown"
    return status, row


def run_module(python, root, dotted, timeout, env):
    rc, out, err = run([python, "-m", "unittest", dotted], root, timeout, env)
    status, row = parse_unittest_summary(rc, out, err)
    record = {"module": dotted, "status": status, "rc": rc}
    record.update(row)
    if status != "ok":
        record["output_tail"] = tail(err or out)
    return record


# --- package gates -----------------------------------------------------------

def parse_json_or_none(text):
    text = (text or "").strip()
    if not text.startswith("{"):
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def run_gate(python, root, name, argv, timeout, env):
    cli = os.path.join(root, "scripts", "agentware")
    rc, out, err = run([python, cli] + argv, root, timeout, env)
    record = {"gate": name, "argv": argv, "rc": rc}
    data = parse_json_or_none(out)
    if data is None:
        record["ok"] = (rc == 0)
        record["output_tail"] = tail(out or err)
        return record

    if "checks" in data:  # audit
        record["ok"] = bool(data.get("ok"))
        record["checks"] = [
            {"name": c.get("name"), "ok": bool(c.get("ok")),
             "details": c.get("details") or []}
            for c in data.get("checks") or []
        ]
        record["failing"] = sorted(c["name"] for c in record["checks"] if not c["ok"])
    elif "gates" in data:  # gate release
        record["ok"] = bool(data.get("passed"))
        record["skipped"] = bool(data.get("skipped"))
        record["checks"] = [
            {"name": g.get("gate"), "ok": bool(g.get("passed")),
             "details": g.get("details") or []}
            for g in data.get("gates") or []
        ]
        record["failing"] = sorted(c["name"] for c in record["checks"] if not c["ok"])
    else:
        record["ok"] = (rc == 0)
        record["output_tail"] = tail(out or err)
    return record


GATES = (
    ("steering-lint", ["steering", "lint"]),
    ("audit", ["audit", "--format", "json"]),
    ("gate-release", ["gate", "release", "--format", "json"]),
)


# --- knowledge-base fingerprint (read-only assertion) ------------------------

def kb_fingerprint(kdir):
    """Cheap content fingerprint of the durable knowledge surfaces."""
    if not kdir or not os.path.isdir(kdir):
        return {"knowledge_dir": kdir, "available": False}
    digest = hashlib.sha256()
    files = 0
    for sub in KB_FINGERPRINT_DIRS:
        base = os.path.join(kdir, sub)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            for name in sorted(filenames):
                path = os.path.join(dirpath, name)
                digest.update(os.path.relpath(path, kdir).encode("utf-8"))
                try:
                    with open(path, "rb") as fh:
                        digest.update(fh.read())
                except OSError:
                    digest.update(b"<unreadable>")
                files += 1
    index_sha = ""
    index_path = os.path.join(kdir, "index.json")
    if os.path.isfile(index_path):
        with open(index_path, "rb") as fh:
            index_sha = hashlib.sha256(fh.read()).hexdigest()
    return {
        "knowledge_dir": kdir,
        "available": True,
        "content_sha256": digest.hexdigest(),
        "file_count": files,
        "index_sha256": index_sha,
    }


def resolve_knowledge_dir(python, root):
    rc, out, _ = run([python, os.path.join(root, "scripts", "agentware"),
                      "config", "--knowledge-dir-only"], root, 120)
    if rc == 0:
        return (out or "").strip()
    return ""


# --- capture -----------------------------------------------------------------

def git_head(root):
    rc, out, _ = run(["git", "rev-parse", "HEAD"], root, 60)
    return (out or "").strip() if rc == 0 else None


def capture(root, label, module_timeout, gate_timeout):
    python = sys.executable
    root = os.path.abspath(root)

    gate_env = dict(os.environ)
    gate_env.pop("PYTHONDONTWRITEBYTECODE", None)
    module_env = dict(gate_env)
    module_env.update(NESTED_GUARD_ENV)

    kdir = resolve_knowledge_dir(python, root)
    before = kb_fingerprint(kdir)

    started = time.time()
    modules = [run_module(python, root, dotted, module_timeout, module_env)
               for dotted in discover_modules(root)]
    gates = [run_gate(python, root, name, argv, gate_timeout, gate_env)
             for name, argv in GATES]
    elapsed = time.time() - started

    after = kb_fingerprint(kdir)

    totals = {"modules": len(modules)}
    for key in ("ran", "passed", "failures", "errors", "skipped",
                "expected_failures", "unexpected_successes"):
        totals[key] = sum(m.get(key, 0) for m in modules)
    totals["modules_ok"] = sum(1 for m in modules if m["status"] == "ok")
    totals["modules_not_ok"] = len(modules) - totals["modules_ok"]

    return {
        "schema_version": SCHEMA_VERSION,
        "label": label,
        "tree_root": redact(root),
        "captured_at": datetime.datetime.now(datetime.timezone.utc)
                               .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "git_head": git_head(root),
        "wall_seconds": round(elapsed, 1),
        "module_env": dict(NESTED_GUARD_ENV),
        "knowledge_dir_unchanged": (before == after),
        "knowledge_fingerprint_before": before,
        "knowledge_fingerprint_after": after,
        "totals": totals,
        "gates": gates,
        "modules": modules,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="tree root to capture")
    parser.add_argument("--label", required=True, help="baseline label (local|donor)")
    parser.add_argument("--out", required=True, help="destination JSON path")
    parser.add_argument("--module-timeout", type=int, default=DEFAULT_MODULE_TIMEOUT)
    parser.add_argument("--gate-timeout", type=int, default=DEFAULT_GATE_TIMEOUT)
    args = parser.parse_args(argv)

    data = capture(args.root, args.label, args.module_timeout, args.gate_timeout)

    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=False)
        fh.write("\n")

    t = data["totals"]
    print("baseline[%s] -> %s" % (data["label"], args.out))
    print("  modules: %d (ok %d / not-ok %d)"
          % (t["modules"], t["modules_ok"], t["modules_not_ok"]))
    print("  tests:   ran %d, passed %d, failures %d, errors %d, skipped %d"
          % (t["ran"], t["passed"], t["failures"], t["errors"], t["skipped"]))
    for g in data["gates"]:
        print("  gate %-14s rc=%s ok=%s%s"
              % (g["gate"], g["rc"], g.get("ok"),
                 (" failing=%s" % ",".join(g["failing"])) if g.get("failing") else ""))
    print("  knowledge dir unchanged: %s" % data["knowledge_dir_unchanged"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
