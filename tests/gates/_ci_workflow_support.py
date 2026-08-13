"""Shared header for the task-78 CI gate suites.

`tests/README.md`: a test file over 600 lines is split along class boundaries
with the shared header lifted into a sibling `_<name>_support.py`. This is that
sibling -- the workflow reader and the two constants both
`test_ci_workflow.py` (arms A/B/C: shape, entrypoint-only, coverage) and
`test_ci_gate_stages.py` (arms D/E/F: effectiveness, the documented command, the
known-failure ledger) need.

The reader is a small purpose-built YAML scanner rather than PyYAML, which is not
installed on a stock clone (INV-6). It extracts only the keys those arms assert
on: a gate that needs a full YAML implementation to run is one that stops running
the day the dependency is unavailable.
"""

import os
import re
import sys

from tests._paths import REPO_ROOT

sys.path.insert(0, os.path.join(REPO_ROOT, "tests"))

import _bootstrap_gate as bg      # noqa: E402
import _ci_gate as cg             # noqa: E402
import _suite_gate as sg          # noqa: E402
WORKFLOW_REL = os.path.join(".github", "workflows", "ci.yml")
WORKFLOW = os.path.join(REPO_ROOT, WORKFLOW_REL)

#: A `run:` line naming one of these is a gate command, and therefore a second
#: definition of "the checks" living in YAML. `_ci_gate.py` is the sanctioned
#: exception and is excluded by name at the comparison.
GATE_COMMAND_PATTERNS = (
    r"tests/_[a-z_]+\.py",
    r"-m\s+unittest",
    r"-m\s+pytest",
    r"scripts/agentware\s+(steering|audit|gate|eval|index)\b",
)

#: The entrypoint the workflow is allowed -- and required -- to call.
ENTRYPOINT_REL = "tests/_ci_gate.py"


def _read(rel):
    with open(os.path.join(REPO_ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _uncommented(text):
    """The workflow with full-line comments stripped.

    Every arm below reasons about what the workflow DOES. This file's comments
    quote the very commands arm B forbids (to explain why they are forbidden),
    so an arm that scanned raw bytes would fail on its own documentation.
    """
    return "\n".join(ln for ln in text.splitlines()
                     if not ln.lstrip().startswith("#"))


def run_lines(text):
    """Every shell line under a `run:` key, block scalars included."""
    lines, out, block_indent = _uncommented(text).splitlines(), [], None
    for line in lines:
        stripped = line.strip()
        if block_indent is not None:
            if stripped and (len(line) - len(line.lstrip())) <= block_indent:
                block_indent = None
            else:
                if stripped:
                    out.append(stripped)
                continue
        match = re.match(r"^(\s*)-?\s*run:\s*(.*)$", line)
        if match:
            indent, rest = len(match.group(1)), match.group(2).strip()
            if rest in ("|", ">", "|-", ">-"):
                block_indent = indent
            elif rest:
                out.append(rest)
    return out


def uses_refs(text):
    """Every `uses:` value in the workflow."""
    return re.findall(r"^\s*-?\s*uses:\s*(\S+)", _uncommented(text), re.M)


def top_level_keys(text):
    """Keys at column 0 — enough to assert the document's shape, no more."""
    return [m.group(1) for m in
            re.finditer(r"^([A-Za-z_][A-Za-z0-9_-]*):", _uncommented(text), re.M)]


def _block(text, key):
    """The lines nested under a column-0 `key:`, up to the next column-0 key.

    Scoping matters more than it looks: `on:` and `jobs:` both hold two-space
    keys, so a bare `^  (\\w+):` scan counts `push:` as a job and then reports a
    missing timeout on it. A gate whose failure message is a lie about the file
    costs more than no gate.
    """
    out, inside = [], False
    for line in _uncommented(text).splitlines():
        if re.match(r"^[A-Za-z_]", line):
            inside = line.split(":", 1)[0] == key
            continue
        if inside:
            out.append(line)
    return out


def job_names(text):
    """The job ids declared under `jobs:`."""
    return [m.group(1) for m in
            (re.match(r"^  ([A-Za-z_][A-Za-z0-9_-]*):\s*$", line)
             for line in _block(text, "jobs")) if m]


def trigger_events(text):
    """The event names declared under `on:`."""
    return [m.group(1) for m in
            (re.match(r"^  ([A-Za-z_][A-Za-z0-9_-]*):", line)
             for line in _block(text, "on")) if m]


