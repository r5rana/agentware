"""Hermetic test fixtures for the agentware CLI.

Everything here is stdlib-only (no pytest, no third-party deps). The helpers
build a *synthetic* knowledge base in a fresh tempdir and drive the real
`scripts/agentware` CLI against it via the AGENTWARE_KNOWLEDGE_DIR env var, so
tests NEVER touch the operator's real knowledge base and are fully
deterministic.

IMPORTANT: tests must never call `agentware init` — that command writes
~/.agentware/config.env and would clobber the operator's real pointer. We build
the synthetic index.json directly instead.
"""

import contextlib
import io
import importlib.util
import os
import shutil
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

# Repo root = parent of tests/. The CLI is a no-extension python script.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI_PATH = os.path.join(REPO_ROOT, "scripts", "agentware")

_CLI_MODULE = None


def load_cli():
    """Import scripts/agentware as a module (cached). Stdlib importlib only."""
    global _CLI_MODULE
    if _CLI_MODULE is None:
        loader = SourceFileLoader("agentware_cli", CLI_PATH)
        spec = importlib.util.spec_from_loader("agentware_cli", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        _CLI_MODULE = mod
    return _CLI_MODULE


def run_cli(argv, kdir, stdin_text=None):
    """Run the CLI with AGENTWARE_KNOWLEDGE_DIR=kdir; capture (code, out, err).

    Restores any pre-existing env var afterward so test ordering is irrelevant.
    """
    mod = load_cli()
    prev = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
    os.environ["AGENTWARE_KNOWLEDGE_DIR"] = kdir
    out, err = io.StringIO(), io.StringIO()
    prev_stdin = sys_stdin = None
    try:
        if stdin_text is not None:
            import sys as _sys
            sys_stdin = _sys
            prev_stdin = _sys.stdin
            _sys.stdin = io.StringIO(stdin_text)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mod.main(argv)
    finally:
        if sys_stdin is not None:
            sys_stdin.stdin = prev_stdin
        if prev is None:
            os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None)
        else:
            os.environ["AGENTWARE_KNOWLEDGE_DIR"] = prev
    return code, out.getvalue(), err.getvalue()


# --- Synthetic KB content ----------------------------------------------------
# Distinctive vocabulary per entry so later recall/eval tests can assert that a
# query whose terms appear only in entry X ranks X first. created dates are
# fixed for determinism.
_ENTRIES = [
    {
        "id": "learn-geofence-reminders",
        "title": "Geofence Reminders Not Firing",
        "category": "learnings",
        "path": "learnings/geofence-reminders.md",
        "tags": ["geofence", "ios", "reminders", "expo"],
        "created": "2026-01-02",
        "summary": "Why arrival geofence reminders never fired and the fixes.",
        "body": (
            "# Geofence Reminders Not Firing\n\n"
            "Geofence-based 'remind me when I arrive' reminders never fired on "
            "iOS because defineTask was nested instead of top-level, the "
            "background location task was never registered at startup, and the "
            "expo-location config plugin plus UIBackgroundModes were missing. "
            "Register geofences with syncGeofences at app launch.\n"
        ),
    },
    {
        "id": "learn-macos-no-timeout",
        "title": "macOS Ships No Timeout Command",
        "category": "learnings",
        "path": "learnings/macos-no-timeout.md",
        "tags": ["macos", "shell", "timeout"],
        "created": "2026-01-03",
        "summary": "macOS has no timeout/gtimeout; do not rely on it in scripts.",
        "body": (
            "# macOS Ships No Timeout Command\n\n"
            "macOS does not ship the GNU coreutils timeout or gtimeout binary. "
            "Shell scripts that wrap a command in timeout will fail on a stock "
            "mac. Use a perl alarm wrapper or a background process with kill "
            "instead.\n"
        ),
    },
    {
        "id": "config-python-runtime",
        "title": "Python Runtime Conventions",
        "category": "configurations",
        "path": "configurations/python-runtime.md",
        "tags": ["python", "runtime", "stdlib"],
        "created": "2026-01-04",
        "summary": "Pure python3 stdlib only; pin dependency versions.",
        "body": (
            "# Python Runtime Conventions\n\n"
            "The toolkit is pure python3 standard library with zero third-party "
            "dependencies. Never add an open dependency range; pin versions. "
            "argparse drives the command dispatch.\n"
        ),
    },
    {
        "id": "ref-bm25-ranking",
        "title": "BM25 Deterministic Ranking",
        "category": "references",
        "path": "references/bm25-ranking.md",
        "tags": ["retrieval", "ranking", "bm25", "search"],
        "created": "2026-01-05",
        "summary": "Hand-rolled BM25 lexical ranking for deterministic recall.",
        "body": (
            "# BM25 Deterministic Ranking\n\n"
            "BM25 is a bag-of-words lexical ranking function with parameters k1 "
            "and b. It scores documents by term frequency saturation and inverse "
            "document frequency without any embeddings, vectors, or neural "
            "network, so identical inputs yield byte-identical ordering.\n"
        ),
    },
]

# Knowledge subdirs scaffolded so filesystem_sync / audit see a clean tree.
_SUBDIRS = ("learnings", "projects", "configurations", "prompts",
            "references", "skills")


def _index_from(entries):
    """Build a valid {entries, tags} index dict (without the 'body' field)."""
    clean, tags = [], {}
    for e in entries:
        item = {k: v for k, v in e.items() if k != "body"}
        clean.append(item)
        for t in e["tags"]:
            tags.setdefault(t, [])
            if e["id"] not in tags[t]:
                tags[t].append(e["id"])
    return {"entries": clean, "tags": tags}


def build_synthetic_kb(root, entries=None):
    """Materialize a synthetic KB under `root`. Returns the index data dict."""
    import json
    entries = entries if entries is not None else _ENTRIES
    for sub in _SUBDIRS:
        d = os.path.join(root, sub)
        os.makedirs(d, exist_ok=True)
        roster = os.path.join(d, "index.md")
        if not os.path.exists(roster):
            with open(roster, "w", encoding="utf-8") as f:
                f.write("# %s\n\n_Roster of %s._\n" % (sub.capitalize(), sub))
    for e in entries:
        abs = os.path.join(root, e["path"])
        os.makedirs(os.path.dirname(abs), exist_ok=True)
        with open(abs, "w", encoding="utf-8") as f:
            f.write(e["body"])
    data = _index_from(entries)
    with open(os.path.join(root, "index.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return data


def build_large_loop_kb(root, n_features=40, sessions_per_feature=3,
                        turns_per_session=6, filler_chars=0):
    """Materialize a REALISTICALLY LARGE synthetic loop KB for perf benchmarks.

    Creates `n_features` work features (each: plan.md with markers + .loop state +
    a metrics.jsonl emission), a shared logs/metrics.jsonl, and per-feature session
    transcripts (logs/sessions/<sid>/main.jsonl + a subagent) that REFERENCE the
    feature work dir so collect_sessions(feature=…) matches them. This reproduces
    the O(features × sessions × file) hot path the loop endpoints exercise.

    Synthetic only (R-LOC-03) — never the operator KB. Returns the feature list.
    """
    import json
    build_synthetic_kb(root)
    os.makedirs(os.path.join(root, "logs"), exist_ok=True)
    sessions_root = os.path.join(root, "logs", "sessions")
    os.makedirs(sessions_root, exist_ok=True)
    metrics_path = os.path.join(root, "logs", "metrics.jsonl")

    features = ["2601%02d-bench-feature-%02d" % (i % 100, i)
                for i in range(n_features)]

    metrics_lines = []
    for fi, feature in enumerate(features):
        fdir = os.path.join(root, "work", feature)
        os.makedirs(os.path.join(fdir, ".loop"), exist_ok=True)
        # A plan with a handful of markers (some done, some open).
        plan = ["# Plan — %s\n" % feature, "\n"]
        for t in range(1, 9):
            mark = "✅" if t <= 5 else ("🟡" if t == 6 else "⬜")
            plan.append("- %s **%d** task %d\n" % (mark, t, t))
        plan.append("\n<promise>%s_DONE</promise>\n" % feature.upper())
        with open(os.path.join(fdir, "plan.md"), "w", encoding="utf-8") as f:
            f.write("".join(plan))
        with open(os.path.join(fdir, ".loop", ".iteration"), "w") as f:
            f.write("%d\n" % (fi % 7 + 1))
        # Per-feature metrics.jsonl emission: a few iterations + transitions + terminal.
        base_ss = "%02d" % (fi % 60)
        for it in range(1, 6):
            metrics_lines.append({
                "ts": "2026-02-%02dT00:%s:%02dZ" % (fi % 28 + 1, base_ss, it),
                "feature": feature, "stage": "loop-main", "phase": "main",
                "iteration": it, "max": 45, "tasks_total": 8,
                "tasks_remaining": max(0, 8 - it), "tasks_done_delta": 1,
                "self_heal_count": fi % 3,
                "input_tokens": 1000 + it, "output_tokens": 500 + it,
            })
            metrics_lines.append({
                "event": "task_transition", "feature": feature, "iteration": it,
                "ts": "2026-02-%02dT00:%s:%02dZ" % (fi % 28 + 1, base_ss, it + 30),
                "task": str(it), "from": "open", "to": "done", "approx": False,
            })
        metrics_lines.append({
            "event": "terminal", "feature": feature, "outcome": "completed",
            "ts": "2026-02-%02dT01:00:00Z" % (fi % 28 + 1),
            "iterations_used": 5, "max": 45, "self_heal_count": fi % 3,
            "tasks_total": 8, "tasks_done": 5, "promise_status": "signalled",
        })

    with open(metrics_path, "w", encoding="utf-8") as f:
        for o in metrics_lines:
            f.write(json.dumps(o) + "\n")

    # Per-feature sessions: each main.jsonl references work/<feature> so the
    # substring feature-match folds it in. Several assistant turns w/ usage + a
    # tool_use, plus one subagent transcript, to mirror real transcript weight.
    sidx = 0
    for fi, feature in enumerate(features):
        for s in range(sessions_per_feature):
            sid = "2026-02-%02d_%06d" % (fi % 28 + 1, sidx)
            sidx += 1
            sdir = os.path.join(sessions_root, sid)
            os.makedirs(os.path.join(sdir, "subagents"), exist_ok=True)
            lines = []
            lines.append({"type": "user", "ts": "2026-02-01T00:00:00Z",
                          "timestamp": "2026-02-%02dT00:00:00Z" % (fi % 27 + 1),
                          "message": {"content":
                                      "AGENTWARE_STAGE=loop-main work on "
                                      "work/%s" % feature}})
            filler = ("x" * filler_chars) if filler_chars else ""
            for turn in range(turns_per_session):
                lines.append({
                    "type": "assistant",
                    "ts": "2026-02-01T00:00:%02dZ" % (turn % 59 + 1),
                    "timestamp": "2026-02-%02dT00:%02d:00Z" % (
                        fi % 27 + 1, turn % 59),
                    "agentware_stage": "loop-main",
                    "message": {
                        "model": "claude-sonnet-4-20250514",
                        "usage": {"input_tokens": 2000 + turn,
                                  "output_tokens": 800 + turn,
                                  "cache_read_input_tokens": 100},
                        "content": [
                            {"type": "text",
                             "text": "Editing work/%s %s" % (feature, filler)},
                            {"type": "tool_use", "name": "Edit",
                             "input": {"file_path": "work/%s/x" % feature}},
                        ],
                    }})
            with open(os.path.join(sdir, "main.jsonl"), "w",
                      encoding="utf-8") as f:
                for o in lines:
                    f.write(json.dumps(o) + "\n")
            with open(os.path.join(sdir, "subagents", "a.jsonl"), "w",
                      encoding="utf-8") as f:
                f.write(json.dumps({
                    "type": "assistant", "ts": "2026-02-01T00:00:30Z",
                    "message": {"model": "claude-sonnet-4-20250514",
                                "usage": {"input_tokens": 500,
                                          "output_tokens": 200},
                                "content": [{"type": "text", "text": "sub"}]}}) + "\n")
    return features


class SyntheticKBTestCase(unittest.TestCase):
    """Base TestCase that stands up a fresh synthetic KB per test."""

    def setUp(self):
        self.kdir = tempfile.mkdtemp(prefix="agentware-test-kb-")
        self.addCleanup(shutil.rmtree, self.kdir, True)
        self.index_data = build_synthetic_kb(self.kdir)

    def run_cli(self, argv, stdin_text=None):
        return run_cli(argv, self.kdir, stdin_text=stdin_text)

    def read_index(self):
        import json
        with open(os.path.join(self.kdir, "index.json"), encoding="utf-8") as f:
            return json.load(f)
