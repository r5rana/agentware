"""Task-10 PARITY MANIFEST: the schema, the dimension table and the probe grammar.

The union fixture (task 7) compares argparse objects. The env/config fixture
(task 8) compares keys. The preserved-surface freeze (task 9) compares node ids
and content anchors. Each answers "did THIS surface survive?". None answers the
question task 79 must answer: *does every capability from EITHER tree carry an
explicit, adjudicated disposition in the merged tree?*

The parity manifest is that ledger — one row per capability, with THREE statuses
and THREE verification modes bound by a closed compatibility matrix:

    status      meaning                                  allowed verification
    ----------  ---------------------------------------  -----------------------
    required    must be present in the merged tree       generated, curated
    excluded    deliberately not carried across          absence_probe
    superseded  replaced by another row's capability     all three

    generated      re-derived from a binding fixture; the row cannot drift from
                   its source because `--out` regenerates it byte-for-byte.
    curated        hand-written; carries a named test AND the mutation that must
                   redden that test, so the row is falsifiable, not prose.
    absence_probe  a runnable probe over the CLOSED grammar below, proving the
                   capability is genuinely ABSENT rather than merely undocumented.

A status is not a claim until something can refute it. `required` is refuted by
its source fixture's gate (generated) or by its named test (curated); `excluded`
is refuted by its probe finding the thing present; `superseded` is refuted by its
successor row not existing, or not being `required`.

Stdlib only. Never touches the real knowledge base.
"""

import os
import subprocess
import tempfile

SCHEMA = 1

#: Every row carries these, whatever its status or verification mode.
COMMON_FIELDS = ("id", "dimension", "capability", "sides", "status",
                 "verification")

STATUSES = ("required", "excluded", "superseded")

VERIFICATIONS = ("generated", "curated", "absence_probe")

#: The closed status -> verification compatibility matrix.
STATUS_MODES = {
    "required": ("generated", "curated"),
    "excluded": ("absence_probe",),
    "superseded": ("generated", "curated", "absence_probe"),
}

#: Extra fields a row must carry, keyed by status.
STATUS_FIELDS = {
    "required": (),
    "excluded": ("reason", "approver", "follow_up", "absence_probe"),
    "superseded": ("superseded_by",),
}

#: Extra fields a row must carry, keyed by verification mode.
VERIFICATION_FIELDS = {
    "generated": (),
    "curated": ("task", "evidence"),
    "absence_probe": ("absence_probe",),
}

#: A curated row's evidence is a named test plus the mutation that must redden
#: it. Prose alone is not evidence; a mutation nobody can run is not a proof.
EVIDENCE_FIELDS = ("test", "mutation")

SIDE_VALUES = ("donor", "local")

# --------------------------------------------------------------------------
# the closed absence-probe grammar
# --------------------------------------------------------------------------

#: Kind -> the fields that kind requires. Anything outside this table is a
#: schema violation, so an `excluded` row can never smuggle in unrunnable prose.
PROBE_KINDS = {
    "cli-absent": ("argv", "expect"),
    "grep-absent": ("pattern",),
    "path-absent": ("path",),
}

#: `grep-absent` must not find the manifest talking ABOUT the thing it claims is
#: absent. These paths are the ledger and its machinery, excluded by name so the
#: exclusion is visible rather than a silent regex quirk.
GREP_EXCLUDE_PATHS = (
    "tests/_parity_curated.py",
    "tests/_parity_merge_rows.py",
    "tests/_parity_manifest.py",
    "tests/_parity_schema.py",
    "tests/fixtures/parity-manifest.json",
    "tests/gates/test_parity_gate.py",
    "tests/gates/test_parity_manifest.py",
)

GREP_EXCLUDE_DIRS = (".git", "__pycache__", ".venv", "node_modules", ".mypy_cache")


def _run_cli_absent(root, probe):
    """True when the entrypoint REFUSES `argv` with the expected parser error."""
    entry = os.path.join(root, "scripts", "agentware")
    if not os.path.exists(entry):
        return False, "entrypoint missing: scripts/agentware"
    env = dict(os.environ)
    # Scope the knowledge dir to a throwaway path: if the flag turns out to
    # EXIST the command actually runs, and it must never run against the live
    # knowledge base (plan Constraints).
    with tempfile.TemporaryDirectory(prefix="aw-probe-kb-") as kdir:
        env["AGENTWARE_KNOWLEDGE_DIR"] = kdir
        try:
            proc = subprocess.run([entry] + list(probe["argv"]), env=env,
                                  capture_output=True, text=True, timeout=120,
                                  cwd=root)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, "probe could not run: %s" % (exc,)
    if proc.returncode == 0:
        return False, "argv was ACCEPTED (rc=0): %s" % (" ".join(probe["argv"]),)
    blob = (proc.stderr or "") + (proc.stdout or "")
    if probe["expect"] not in blob:
        return False, ("rc=%d but %r not in output; the refusal is not the "
                       "expected one" % (proc.returncode, probe["expect"]))
    return True, "refused rc=%d: %s" % (proc.returncode, probe["expect"])


def _text_files(root):
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in GREP_EXCLUDE_DIRS)
        for name in sorted(names):
            path = os.path.join(base, name)
            rel = os.path.relpath(path, root)
            if rel in GREP_EXCLUDE_PATHS or os.path.islink(path):
                continue
            yield rel, path


def _run_grep_absent(root, probe):
    """True when the case-insensitive pattern appears in NO tracked text file."""
    needle = probe["pattern"].lower()
    hits = []
    for rel, path in _text_files(root):
        try:
            with open(path, "r", encoding="utf-8", errors="strict") as fh:
                body = fh.read()
        except (UnicodeDecodeError, OSError):
            continue
        if needle in body.lower():
            hits.append(rel)
            if len(hits) >= 5:
                break
    if hits:
        return False, "pattern %r present in %s" % (probe["pattern"],
                                                    ", ".join(hits))
    return True, "pattern %r absent tree-wide" % (probe["pattern"],)


def _run_path_absent(root, probe):
    target = os.path.join(root, probe["path"])
    if os.path.exists(target):
        return False, "path present: %s" % (probe["path"],)
    return True, "path absent: %s" % (probe["path"],)


PROBE_RUNNERS = {
    "cli-absent": _run_cli_absent,
    "grep-absent": _run_grep_absent,
    "path-absent": _run_path_absent,
}


def run_probe(root, probe):
    """Run one absence probe. Returns (ok, detail)."""
    kind = probe.get("kind")
    if kind not in PROBE_KINDS:
        return False, "unknown probe kind: %r" % (kind,)
    missing = [f for f in PROBE_KINDS[kind] if f not in probe]
    if missing:
        return False, "probe %s missing %s" % (kind, ", ".join(missing))
    return PROBE_RUNNERS[kind](root, probe)


# --------------------------------------------------------------------------
# the dimension table
# --------------------------------------------------------------------------
#
# `tier` is how the dimension is SEEDED, not a restriction on what it may hold:
# a generated dimension may later gain curated rows and vice versa.
#
# `donor_delta` says the donor tree holds something this tree does not. For the
# four generated dimensions it is RE-DERIVED from the source fixture and the
# declared value is asserted against it, so the declaration cannot lie. For the
# curated dimensions it is declared with the evidence that justifies it.
#
# A dimension with a donor delta and zero donor-side rows is the exact shape of
# a silent one-sided merge, so the gate rejects it.

DIMENSIONS = [
    {
        "name": "cli_surface",
        "title": "CLI verb, subverb and flag surface",
        "tier": "generated",
        "source": "tests/fixtures/surface-union.json",
        "gate": "tests/_surface_union.py",
        "donor_delta": True,
        "donor_delta_evidence": "surface-union sides.nodes.donor_only > 0",
        "plan_tasks": [7, 29, 32, 52, 54, 73],
    },
    {
        "name": "env_config",
        "title": "Environment variables and config keys",
        "tier": "generated",
        "source": "tests/fixtures/env-config-parity.json",
        "gate": "tests/_env_config_capture.py",
        "donor_delta": True,
        "donor_delta_evidence": "env-config-parity counts.donor_only > 0",
        "plan_tasks": [8, 53],
    },
    {
        "name": "test_corpus",
        "title": "Test modules and their frozen node ids",
        "tier": "generated",
        "source": "tests/fixtures/preserved-surface.json",
        "gate": "tests/_preserved_freeze.py",
        "donor_delta": True,
        "donor_delta_evidence": "preserved-surface node_ids.pending_in_destination > 0",
        "plan_tasks": [9, 58],
    },
    {
        "name": "assets",
        "title": "Executables, templates, catalogs and fixture families",
        "tier": "generated",
        "source": "tests/fixtures/preserved-surface.json",
        "gate": "tests/_preserved_freeze.py",
        "donor_delta": False,
        "donor_delta_evidence": (
            "preserved-surface assets.pending_in_destination == 0 — the asset "
            "backlog is TERMINAL. Task 25 landed catalog/modules.json, task 51 "
            "the decision template, and task 60 the last row, "
            "fixture_family:help_golden, regenerated locally because the "
            "donor's eighty goldens were rendered by a different argparse and "
            "could never have been copied."),
        "plan_tasks": [9, 51, 60],
    },
    {
        "name": "retrieval_policy",
        "title": "Retrieval ranking, pinning and read-time trust policy",
        "tier": "curated",
        "source": None,
        "gate": None,
        "donor_delta": True,
        "donor_delta_evidence": ("the donor pins BM25 scores exactly (mutating K1 "
                                 "or B survives every order-based test here) and "
                                 "adds the 'shared' recall scope"),
        "plan_tasks": [24, 49, 55, 59],
    },
    {
        "name": "kb_integrity",
        "title": "Knowledge-base durability, locking and poison tolerance",
        "tier": "curated",
        "source": None,
        "gate": None,
        "donor_delta": True,
        "donor_delta_evidence": ("the donor's content corpus_fingerprint is the "
                                 "decided cache-freshness authority, and its "
                                 "roster/injection lint is wired into index validate"),
        "plan_tasks": [15, 22, 23, 24, 53, 57],
    },
    {
        "name": "loop_orchestration",
        "title": "Loop phases, hook gates and completion promises",
        "tier": "curated",
        "source": None,
        "gate": None,
        "donor_delta": True,
        "donor_delta_evidence": ("the donor holds the pre/post-hook org lint gates, "
                                 "the per-run retrieval-quality gate and working "
                                 "SessionStart roster injection"),
        "plan_tasks": [18, 33, 34, 35, 45, 47, 53],
    },
    {
        "name": "org_team",
        "title": "Org root, team scoping and shared-corpus governance",
        "tier": "curated",
        "source": None,
        "gate": None,
        "donor_delta": True,
        "donor_delta_evidence": "the entire org/team kernel is donor-only; this tree has none of it",
        "plan_tasks": [17, 36, 50, 63, 64, 65, 66],
    },
    {
        "name": "security_trust",
        "title": "Update trust, staging isolation and untrusted-content handling",
        "tier": "curated",
        "source": None,
        "gate": None,
        "donor_delta": True,
        "donor_delta_evidence": ("the donor holds the scoped staging allowlist with "
                                 "its blob cap, push retry with backoff, and "
                                 "injection scanning of knowledge content"),
        "plan_tasks": [40, 41, 53, 61],
    },
    {
        "name": "dream_maintenance",
        "title": "Nightly dream steps, its lock and its cadence guards",
        "tier": "curated",
        "source": None,
        "gate": None,
        "donor_delta": True,
        "donor_delta_evidence": ("the donor's dream steps live in a package with one "
                                 "file per step behind a registry, which this tree "
                                 "adopts as the destination shape"),
        "plan_tasks": [16, 21, 46, 61],
    },
    {
        "name": "docs_templates",
        "title": "Top-level documents and the knowledge template corpus",
        "tier": "curated",
        "source": None,
        "gate": None,
        "donor_delta": True,
        "donor_delta_evidence": ("the donor carries a decision template, declared "
                                 "frontmatter on every template, an architecture "
                                 "README section and the artifact capture standard"),
        "plan_tasks": [20, 51, 61, 74, 75, 76],
    },
    {
        "name": "evaluation_quality",
        "title": "Retrieval evaluation, gold fixtures and golden help output",
        "tier": "curated",
        "source": None,
        "gate": None,
        "donor_delta": True,
        "donor_delta_evidence": ("the donor holds the exact-score BM25 pin; its "
                                 "80-member help_golden corpus is landed and "
                                 "superseded by the local 125-surface superset "
                                 "(task 60)"),
        "plan_tasks": [26, 27, 59, 60],
    },
    {
        "name": "modularity",
        "title": "Package decomposition, ceilings and the modularity ratchets",
        "tier": "curated",
        "source": None,
        "gate": None,
        "donor_delta": True,
        "donor_delta_evidence": ("the donor IS the modular tree: agentware_lib, the "
                                 "split loop and catalog/modules.json have no local "
                                 "equivalent"),
        "plan_tasks": [21, 25, 32, 56, 67, 68, 69, 70, 73],
    },
]

DIMENSION_NAMES = tuple(d["name"] for d in DIMENSIONS)

DIMENSION_BY_NAME = {d["name"]: d for d in DIMENSIONS}
