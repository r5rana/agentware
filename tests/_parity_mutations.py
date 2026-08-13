"""Task-79 PARITY MUTATION SWEEP: the closed mutation grammar.

Feature 260813-modular-core-adoption, task 79.

WHAT THIS FIXES
---------------
Every curated parity row already carried a `mutation` field, and the schema
called it "the proof that makes the row falsifiable rather than prose". It was
prose. The only thing enforced about it was `len(mutation) > 20`
(`tests/gates/test_parity_manifest.py`), so a row could name a mutation that
nobody had ever run, that could not be run, or that did not redden anything --
and the ledger would still report a clean parity gate. That is the same defect
task 78 found in three of the five documented structural checkers: a command
that cannot fail, recorded by seven worklogs as verification.

So the prose stays (a human needs the sentence), and a RUNNABLE probe lands
beside it, over the same shape of closed grammar the task-10 absence probes
already use: a kind, a fixed field set per kind, and a runner. Anything outside
the table is a schema violation, so a row can never smuggle in an unrunnable
"mutation" again.

WHY APPLICATION IS FAIL-LOUD
----------------------------
The failure mode a mutation sweep must not have is the SILENT NO-OP: a `find`
string that no longer appears in the file, a `delete-path` whose target moved.
The mutation then changes nothing, the named test stays green, and the sweep
reports "this row's test did not redden" -- which reads as a missing capability
when the truth is a stale probe. Worse, if the sweep inverted its polarity the
no-op would read as a PASS. Every applier here therefore returns
(False, reason) when it did not actually change the tree, and the sweep treats
that as a hard error distinct from "the test stayed green".

WHY THE HARNESS PATHS ARE UNMUTABLE
-----------------------------------
The sweep executes its child runner from INSIDE the sandbox, so a mutation that
edited the sweep or this grammar would be rewriting the instrument mid-
measurement. Those paths are refused at validation time rather than trusted not
to be named.

Stdlib only. Every applier is given a SANDBOX root; nothing here may be pointed
at the working tree by the sweep (`_parity_sweep.py` enforces that), and nothing
here touches the knowledge base.
"""

import os
import shutil

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)

#: Kind -> the fields that kind requires. The table IS the grammar; a spec whose
#: kind is absent here cannot be validated and therefore cannot ship.
MUTATION_KINDS = {
    # Substitute bytes in an existing tracked file. The canonical mutation:
    # weaken one assertion, flip one literal, drop one branch.
    "replace": ("path", "find", "replace"),
    # Remove a file or a whole directory -- "drop the thing the row claims".
    "delete-path": ("path",),
    # Add text to the end of an existing file.
    "append": ("path", "text"),
    # Create a file that is not there (or overwrite one) -- "plant the defect
    # the gate must notice", e.g. an unclaimed module, a resurrected monolith.
    "write": ("path", "text"),
    # Move a file aside: the shape a relocation or a rename takes.
    "rename": ("path", "to"),
}

#: Optional fields any kind may carry.
OPTIONAL_FIELDS = ("count",)

#: Paths a mutation may NEVER name, because mutating them would rewrite the
#: instrument taking the measurement rather than the subject under test. These
#: two are EXECUTED inside the sandbox: the sweep's child runner and the grammar
#: it imports.
#:
#: `tests/_parity_mutation_rows.py` is deliberately NOT here. It is data the
#: instrument READS, and the parent has already read it before any sandbox is
#: touched -- the spec under test is passed in explicitly, so the sweep's own
#: behaviour never depends on the sandbox's copy. That distinction matters,
#: because the table IS the subject of a parity row: the claim "every curated
#: row carries a runnable probe" can only be refuted by taking one away.
UNMUTABLE = (
    "tests/_parity_sweep.py",
    "tests/_parity_mutations.py",
)

#: Directory prefixes a mutation may never reach into: version-control internals
#: (a mutation there is not a source change, it is a corrupted sandbox) and the
#: fixture corpora that carry other repositories' graders.
UNMUTABLE_PREFIXES = (".git/",)


def spec_errors(spec, row_id="<row>"):
    """Static validation of one mutation spec. Returns a list of violations."""
    bad = []
    if not isinstance(spec, dict):
        return ["row %s: mutation probe is %s, not a mapping"
                % (row_id, type(spec).__name__)]
    kind = spec.get("kind")
    if kind not in MUTATION_KINDS:
        return ["row %s: unknown mutation kind %r (known: %s)"
                % (row_id, kind, ", ".join(sorted(MUTATION_KINDS)))]
    required = MUTATION_KINDS[kind]
    for field in required:
        if field not in spec:
            bad.append("row %s: mutation %s is missing %s" % (row_id, kind, field))
    allowed = set(required) | set(OPTIONAL_FIELDS) | {"kind"}
    for field in sorted(set(spec) - allowed):
        bad.append("row %s: mutation %s carries unknown field %r"
                   % (row_id, kind, field))
    bad.extend(_path_errors(spec, row_id))
    if kind == "replace" and spec.get("find") == spec.get("replace"):
        bad.append("row %s: replace mutation substitutes a string for itself, "
                   "which changes nothing" % row_id)
    return bad


def _path_errors(spec, row_id):
    """The path vocabulary: relative, inside the tree, and not the instrument."""
    bad = []
    for field in ("path", "to"):
        rel = spec.get(field)
        if rel is None:
            continue
        if not isinstance(rel, str) or not rel:
            bad.append("row %s: %s is not a path" % (row_id, field))
            continue
        if os.path.isabs(rel) or rel.startswith("../") or "/../" in rel:
            bad.append("row %s: %s %r must be a relative path inside the tree"
                       % (row_id, field, rel))
        if rel in UNMUTABLE:
            bad.append("row %s: %s %r is the sweep's own instrument and may "
                       "never be mutated" % (row_id, field, rel))
        if rel.startswith(UNMUTABLE_PREFIXES):
            bad.append("row %s: %s %r reaches into version-control internals"
                       % (row_id, field, rel))
    return bad


# --------------------------------------------------------------------------
# the appliers -- each returns (changed, detail); False means NOTHING happened
# --------------------------------------------------------------------------

def _resolve(root, rel):
    target = os.path.realpath(os.path.join(root, rel))
    if not target.startswith(os.path.realpath(root) + os.sep):
        return None
    return target


def _apply_replace(root, spec):
    target = _resolve(root, spec["path"])
    if target is None or not os.path.isfile(target):
        return False, "replace target is not a file: %s" % spec["path"]
    with open(target, "r", encoding="utf-8", errors="strict") as fh:
        before = fh.read()
    if spec["find"] not in before:
        return False, ("replace found no occurrence of %r in %s -- the probe is "
                       "STALE, not the capability" % (spec["find"], spec["path"]))
    count = int(spec.get("count", 1))
    after = before.replace(spec["find"], spec["replace"], count) if count > 0 \
        else before.replace(spec["find"], spec["replace"])
    if after == before:
        return False, "replace produced identical bytes in %s" % spec["path"]
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(after)
    return True, "replaced %d occurrence(s) in %s" % (
        before.count(spec["find"]) if count <= 0 else count, spec["path"])


def _apply_delete(root, spec):
    target = _resolve(root, spec["path"])
    if target is None or not os.path.exists(target):
        return False, ("delete-path target is already absent: %s -- the probe is "
                       "STALE" % spec["path"])
    if os.path.isdir(target) and not os.path.islink(target):
        shutil.rmtree(target)
        return True, "removed directory %s" % spec["path"]
    os.unlink(target)
    return True, "removed file %s" % spec["path"]


def _apply_append(root, spec):
    target = _resolve(root, spec["path"])
    if target is None or not os.path.isfile(target):
        return False, "append target is not a file: %s" % spec["path"]
    if not spec["text"]:
        return False, "append text is empty, which changes nothing"
    with open(target, "a", encoding="utf-8") as fh:
        fh.write(spec["text"])
    return True, "appended %d byte(s) to %s" % (len(spec["text"]), spec["path"])


def _apply_write(root, spec):
    target = _resolve(root, spec["path"])
    if target is None:
        return False, "write target escapes the tree: %s" % spec["path"]
    if os.path.isdir(target):
        return False, "write target is a directory: %s" % spec["path"]
    if os.path.isfile(target):
        with open(target, "r", encoding="utf-8", errors="replace") as fh:
            if fh.read() == spec["text"]:
                return False, ("write would reproduce the existing bytes of %s"
                               % spec["path"])
    parent = os.path.dirname(target)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(spec["text"])
    return True, "wrote %d byte(s) to %s" % (len(spec["text"]), spec["path"])


def _apply_rename(root, spec):
    src = _resolve(root, spec["path"])
    dst = _resolve(root, spec["to"])
    if src is None or not os.path.exists(src):
        return False, "rename source is absent: %s -- the probe is STALE" % spec["path"]
    if dst is None:
        return False, "rename destination escapes the tree: %s" % spec["to"]
    if os.path.exists(dst):
        return False, "rename destination already exists: %s" % spec["to"]
    parent = os.path.dirname(dst)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    os.rename(src, dst)
    return True, "renamed %s -> %s" % (spec["path"], spec["to"])


APPLIERS = {
    "replace": _apply_replace,
    "delete-path": _apply_delete,
    "append": _apply_append,
    "write": _apply_write,
    "rename": _apply_rename,
}


def apply_mutation(root, spec, row_id="<row>"):
    """Apply one mutation to `root`. Returns (changed, detail).

    `changed=False` is ALWAYS an error for the caller: it means the probe did
    not touch the tree, so whatever the named test then reports is measuring
    nothing. It is never a quiet pass.
    """
    errors = spec_errors(spec, row_id)
    if errors:
        return False, "; ".join(errors)
    return APPLIERS[spec["kind"]](root, spec)
