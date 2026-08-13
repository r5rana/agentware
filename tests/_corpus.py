"""The MODULARITY CORPUS — which files the ceilings govern, and at what cap.

Plan task 67 makes modularity permanent ACROSS THE WHOLE TREE. Before it, the
ceiling gate measured `scripts/agentware_lib/**` and nothing else, so the four
subsystems most likely to become the NEXT monolith were unmeasured: the 1,146-line
dashboard, the split loop and hook scripts, the test corpus's own helper modules,
and the webui source. A cap that covers only the code that was already split is a
record of past work, not an invariant over future work.

This module is the ONE definition of that corpus. `_manifest_gen` enumerates it
into `catalog/modules.json` (so every file on disk is CLAIMED by a manifest row)
and `_ceiling_gate` measures it (so every file is within its cap or carries a
declared, shrink-only debt row). Both read the same table here, which is what
stops the manifest and the gate disagreeing about what the tree contains — the
same discipline `_ceiling_gate` holds for the branch metric.

## The layers and their caps

Straight from the plan's "Ceilings" section, with one addition:

| layer          | what it holds                                   | cap |
|----------------|-------------------------------------------------|-----|
| `entrypoint`   | `scripts/agentware`, the facade                 | 120 |
| `lib`          | `scripts/agentware_lib/**/*.py`                 | 600 |
| `hooks`        | `scripts/hooks/**` (harness hook scripts)       | 250 |
| `loop`         | `scripts/loop/*.sh` (orchestrator libs)         | 250 |
| `scripts`      | anything else under `scripts/` (dashboard, ...) | 250 |
| (`entrypoint`) | also `scripts/agentware_dashboard/__main__.py`   | 120 |
| `orchestrator` | `agentware.sh`                                  | 250 |
| `tests`        | `tests/**/*.py` outside `tests/fixtures/`       | 600 |
| `webui`        | `webui/src/**` source (`.ts`/`.tsx`/`.css`)     | 600 |

`webui` is the addition: the plan's ceiling list predates the webui landing and
names no cap for it, so it takes the same 600 every other MODULE-shaped layer
carries (`lib`, `tests`). The 250 rung is deliberately not extended to it — 250
is the cap for PROCEDURAL files (shell scripts, an entrypoint, a server), and a
React component tree is neither.

## What is excluded, and why an exclusion is not a hole

Completeness is asserted as `claimed = has an entry OR matches a declared
exclusion`, and each exclusion carries a reason. That is what keeps "every file on
disk is claimed" a real check rather than a walk over whatever the enumerator
happened to find: adding a directory to the tree cannot silently drop it out of
the corpus, because the enumerator would have to be taught to skip it HERE, in a
table a reviewer reads.

Stdlib only, read-only, deterministic, py3.7-safe.
"""

import os

HERE = os.path.dirname(os.path.realpath(__file__))

# The tree under measurement. Defaults to the checkout this file lives in; the
# override lets the gate run FROM the package repo AGAINST a staged tree, which
# is the only window in which the two differ (same idiom as the staging tool's
# AGENTWARE_DONOR_STAGING).
REPO_ROOT = os.environ.get("AGENTWARE_CEILING_ROOT") or os.path.dirname(HERE)

# --- the ceilings -----------------------------------------------------------

#: Per-layer FILE line caps.
LAYER_CAPS = {
    "entrypoint": 120,
    "lib": 600,
    "hooks": 250,
    "loop": 250,
    "scripts": 250,
    "orchestrator": 250,
    "tests": 600,
    "webui": 600,
}

#: The cap no FILE cap can express (plan: "per function 120 lines / 12
#: branches"). Applies to every Python file in the corpus — the one language in
#: it the stdlib can parse without a dependency, which INV-6 forbids adding.
FUNC_LINE_CAP = 120
FUNC_BRANCH_CAP = 12

LIB_LAYER = "lib"
LIB_PREFIX = "scripts/agentware_lib/"

#: Extensions that make a file part of the corpus, per layer family.
_SCRIPT_EXTS = (".py", ".sh")
_WEBUI_EXTS = (".ts", ".tsx", ".css")

#: Extensionless executables under `scripts/` are shell or Python by shebang, so
#: extension alone cannot find them. They are named, because there are three and
#: naming them is cheaper than sniffing bytes — and a fourth arriving unclaimed
#: is exactly the finding the completeness check exists to make.
EXTENSIONLESS = ("scripts/agentware", "scripts/aw-knowledge-dir",
                 "scripts/hooks/commit-msg")


class Exclusion(object):
    """One declared skip: a path prefix, why it is not code under the ceilings."""

    def __init__(self, prefix, reason):
        self.prefix = prefix
        self.reason = reason


#: Declared exclusions. A file matching one of these is CLAIMED (the manifest
#: records the exclusion) but carries no entry and no cap.
EXCLUSIONS = (
    Exclusion("tests/fixtures/",
              "fixture DATA, not code under test — including SWE task repos "
              "that ship their own test_hidden.py graders, which are meant to "
              "be run BY a test and never collected as one"),
    Exclusion("webui/src/fixtures/",
              "recorded API response fixtures (JSON), not source"),
    Exclusion("webui/dist/",
              "vite build output, regenerated from webui/src"),
    Exclusion("webui/node_modules/",
              "installed third-party packages, not authored here"),
    Exclusion("__pycache__/", "interpreter bytecode cache"),
)

#: Ordered (prefix, layer) rules. FIRST match wins, so the specific prefixes sit
#: above the general `scripts/` catch-all.
_PREFIX_RULES = (
    ("scripts/agentware_lib/", "lib"),
    ("scripts/hooks/", "hooks"),
    ("scripts/loop/", "loop"),
    ("scripts/", "scripts"),
    ("tests/", "tests"),
    ("webui/src/", "webui"),
)

#: Exact-path rules, checked before the prefixes.
#:
#: An ENTRYPOINT is a file whose whole job is argv in, a call into a package out.
#: There are two: the CLI facade, and the dashboard sidecar's `__main__` (task 69
#: decomposed the 1,146-line server into a package with the same discipline). The
#: dashboard entrypoint is named HERE rather than left to the `scripts/` prefix
#: rule at 250, because "at most 120 lines" is the claim task 69 makes about it,
#: and a claim measured once is a claim about the day it was written.
_EXACT_RULES = {
    "scripts/agentware": "entrypoint",
    "scripts/agentware_dashboard/__main__.py": "entrypoint",
    "agentware.sh": "orchestrator",
}

#: Directory roots the enumerator walks.
_ROOTS = ("scripts", "tests", os.path.join("webui", "src"))


def is_excluded(rel):
    """The Exclusion claiming `rel`, or None."""
    for ex in EXCLUSIONS:
        if rel.startswith(ex.prefix) or ("/" + ex.prefix) in ("/" + rel):
            return ex
    return None


def layer_of(rel):
    """The corpus layer a repo-relative path belongs to, or None if outside."""
    if rel in _EXACT_RULES:
        return _EXACT_RULES[rel]
    for prefix, layer in _PREFIX_RULES:
        if rel.startswith(prefix):
            return layer
    return None


def cap_of(layer):
    """The FILE line cap for a corpus layer."""
    return LAYER_CAPS[layer]


def _is_corpus_ext(rel, layer):
    if rel in EXTENSIONLESS:
        return True
    ext = os.path.splitext(rel)[1]
    if layer == "webui":
        return ext in _WEBUI_EXTS
    if layer == "tests":
        return ext == ".py"
    return ext in _SCRIPT_EXTS


def is_member(rel):
    """True iff a repo-relative path is a corpus member (not excluded)."""
    if is_excluded(rel):
        return False
    layer = layer_of(rel)
    if layer is None:
        return False
    return _is_corpus_ext(rel, layer)


def corpus_files(root=None):
    """Every corpus member on disk, repo-relative, sorted.

    Walks the filesystem rather than `git ls-files`: the gate must hold in a
    staged tree that is not a git checkout, and a subprocess would make it
    non-hermetic.
    """
    root = root or REPO_ROOT
    out = []
    for start in _ROOTS + ("",):
        base = os.path.join(root, start) if start else root
        if start:
            for dirpath, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                for name in files:
                    rel = os.path.relpath(os.path.join(dirpath, name),
                                          root).replace(os.sep, "/")
                    if is_member(rel):
                        out.append(rel)
        else:
            for name in sorted(os.listdir(base)):
                if is_member(name):
                    out.append(name)
    return sorted(set(out))


def python_files(root=None):
    """Corpus members the stdlib can parse for the per-FUNCTION caps."""
    base = root or REPO_ROOT
    return [f for f in corpus_files(root)
            if f.endswith(".py")
            or (f in EXTENSIONLESS
                and _has_python_shebang(os.path.join(base, f)))]


def _has_python_shebang(path):
    try:
        with open(path, "rb") as f:
            first = f.readline(200)
    except (IOError, OSError):
        return False
    return b"python" in first


def caps_block():
    """The `caps` section the manifest publishes, for consumers outside tests/."""
    return {
        "file_lines_by_layer": dict(LAYER_CAPS),
        "function_lines": FUNC_LINE_CAP,
        "function_branches": FUNC_BRANCH_CAP,
    }


def exclusions_block():
    """The declared exclusions, as manifest rows."""
    return [{"prefix": ex.prefix, "reason": ex.reason} for ex in EXCLUSIONS]
