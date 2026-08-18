"""Task-16 gate: the dream cycle never mutates the PACKAGE tree.

The donor tree reintroduced dream step "0" — a tip-of-main `git pull` on the
package root with a hard-reset revert, guarded only by a syntax check. This tree
retired that step deliberately (feature 260712, task 13): it was a rival UNSIGNED
updater that defeated the signed-tag thesis, could stamp a schema out-of-band and
strand the machine, and raced the periodic tick on the same working tree with
non-cross-checking locks. Adopting the donor's step family wholesale would bring
it back, and NOTHING in the surface fixtures would notice: the step registry is a
module constant, not a flag; `--steps` takes a free-form string; and the union
parser is byte-identical either way.

So this gate asserts the INVARIANT rather than the deletion, over whichever tree
currently carries the modular library:

  1. REGISTRY      the dream step registry resolves exactly once, carries no
                   retired step id or label, and its rows are a subset of this
                   repo's retired set IN ORDER. The steps it is still missing
                   must equal the declared task-46 restore set exactly — so a
                   step going missing for any OTHER reason reddens here.
  2. RETIRED       none of the retired callables is DEFINED anywhere in the lib,
                   and none is referenced from executable code.
  3. NO HITS       the retired step's label and symbol names have zero literal
                   hits across the published corpus (see SCAN_EXEMPT for the
                   documented exemptions, each one a file whose JOB is to name
                   what it proves absent).
  4. NO MUTATION   no registered step's body reaches the package root, and the
                   dream corpus contains no hard-reset and no git call targeting
                   the package root. This is the invariant itself: the deletion
                   is a means, "dream never mutates the repo" is the end.
  5. BEHAVIOUR     the tree's own CLI refuses `--steps 0` naming it unknown, and
                   a dry-run plans no step whose payload pulls the package.

Tree resolution, in order: --tree, AGENTWARE_LIB_TREE, this repo (once the lib
lands here at plan task 25), then the task-11 staging destination. If NO tree
carries the lib the gate FAILS — "I could not find anything to check" must never
read the same as "everything checks out".

Stdlib only. Every probe runs in a subprocess with AGENTWARE_CONFIG_HOME and
AGENTWARE_KNOWLEDGE_DIR redirected to throwaway paths, so the operator's real
config and knowledge base are never read or written.
"""

import argparse
import ast
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _stage_donor  # noqa: E402

LIB_REL = os.path.join("scripts", "agentware_lib")
ENTRY_REL = os.path.join("scripts", "agentware")

REGISTRY_NAME = "DREAM_STEP_FUNCS"

#: The retired step's id and label. Assembled from parts on purpose: this file
#: names them in order to prove them ABSENT, and a literal here would make the
#: gate's own source a hit for the scan it performs.
RETIRED_STEP_ID = "0"
RETIRED_STEP_LABEL = "pkg" + "-pull"

#: The step function and the two helpers that existed only to serve it, plus the
#: migrate-after-pull hook this tree retired in the same change.
RETIRED_CALLABLES = (
    "_dream_step_pkg" + "_pull",
    "_dream_pkg" + "_syntax_check",
    "_dream_pkg" + "_pull_revert",
    "_dream_apply_migrations" + "_after_pull",
)

#: The literal tokens check 3 scans for, case-insensitively.
RETIRED_TOKENS = (RETIRED_STEP_LABEL, "pkg" + "_pull",
                  "_dream_pkg" + "_syntax_check")

#: This repo's retired set: the ordered step registry after step "0" was removed.
#: The merged registry must be a subset of it, in order.
LOCAL_RETIRED_STEPS = (
    ("1", "kb-pull"),
    ("2", "health-gate"),
    ("a", "index-rebuild"),
    ("b", "bench-redact"),
    ("c", "audit"),
    ("d", "eval-record"),
    ("e", "detect-report"),
    ("s", "stale-propose"),
    ("k", "skill-propose"),
    ("m", "transcript-mine"),
    ("g", "prefer-classify"),
    ("f", "kb-git-commit"),
)

#: The four steps the donor dropped ("s"/"k"/"m"/"g"), RESTORED as four step
#: files by plan task 46. This tuple is now EMPTY, and that emptiness is the
#: assertion: the registry must carry this repo's full ordered set with nothing
#: missing. It stays here rather than being deleted because an empty expectation
#: is a live check, while a deleted one is no check at all -- the donor dropped
#: four steps once and nothing failed, which is exactly how they went unnoticed.
PENDING_RESTORE_STEPS = ()

#: The package root as the lib names it, and the git verbs that would advance it.
REPO_ROOT_NAMES = ("REPO_ROOT",)
MUTATING_GIT_ARGS = (("reset", "--hard"), ("pull",), ("merge",), ("rebase",))

#: Files whose JOB is to name the retired step, exempt from the literal scan and
#: listed BY NAME so the exemption is reviewable rather than a silent regex hole.
#: `tests/` is exempt wholesale for the same reason: a proof of absence must be
#: allowed to say what is absent.
#:
#: `{relative path: identity anchor}` — the exemption is bound to the FILE'S
#: IDENTITY, never to its PATH alone. Unlike a policy row that only TIGHTENS,
#: this table is pure EXEMPTION: an entry makes a file invisible to check 3, so
#: a path-keyed entry outliving its file is a pre-authorised blind spot, and
#: anything landing at that path later is un-scanned by inheritance. Requiring
#: the anchor closes that: the sole entry is the preserved monolith, exempt
#: because it carries the RETIREMENT NOTE verbatim, so the note itself is what
#: earns the exemption and a file that does not carry it is simply scanned.
#:
#: Three states, each with its own name so none of them is silent:
#: `stale_exemptions` (the path is gone — the retirement landed, inert),
#: `impostor_exemptions` (a DIFFERENT file sits at the path — not exempt, and
#: scanned like any other published file), and `unused_exemptions` (the file is
#: the right one but no longer carries a retired token — an exemption that
#: stopped being earned, which is the one shape that hides live code).
SCAN_EXEMPT_DIRS = ("tests",)
SCAN_EXEMPT_FILES = {
    os.path.join("scripts", "agentware_legacy.py"):
        "RETIRED (feature 260712-scheduler-updater, Task 13)",
}

SCAN_SKIP_DIRS = (".git", "__pycache__", ".venv", "node_modules", ".mypy_cache",
                  ".cache", "logs")


# --------------------------------------------------------------------------
# tree resolution
# --------------------------------------------------------------------------

def candidate_trees(explicit=None):
    """Every tree that might carry the lib, most authoritative first."""
    out = []
    if explicit:
        out.append(explicit)
    env = os.environ.get("AGENTWARE_LIB_TREE", "").strip()
    if env:
        out.append(env)
    out.append(REPO)
    dest = _stage_donor.resolve_dest()
    out.append(dest)
    out.append(os.path.join(dest, "donor"))
    return out


def resolve_tree(explicit=None):
    """(tree, problems) — the first candidate that actually carries the lib."""
    tried = []
    for cand in candidate_trees(explicit):
        tried.append(cand)
        if os.path.isdir(os.path.join(cand, LIB_REL)):
            return cand, []
    return None, ["no tree carries %s; tried: %s" % (LIB_REL, ", ".join(tried))]


# --------------------------------------------------------------------------
# static reading
# --------------------------------------------------------------------------

def lib_sources(tree):
    """(relative path, parsed module) for every Python file under the lib."""
    lib = os.path.join(tree, LIB_REL)
    for base, dirs, names in os.walk(lib):
        dirs[:] = sorted(d for d in dirs if d not in SCAN_SKIP_DIRS)
        for name in sorted(n for n in names if n.endswith(".py")):
            path = os.path.join(base, name)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    body = fh.read()
                yield os.path.relpath(path, tree), ast.parse(body)
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue


def _registry_rows(node):
    """The (id, label) pairs of a registry assignment, or None if not literal."""
    if not isinstance(node, (ast.Tuple, ast.List)):
        return None
    rows = []
    for element in node.elts:
        if not isinstance(element, (ast.Tuple, ast.List)) or len(element.elts) < 2:
            return None
        sid, label = element.elts[0], element.elts[1]
        if not (isinstance(sid, ast.Constant) and isinstance(label, ast.Constant)):
            return None
        rows.append((sid.value, label.value))
    return rows


def find_registry(tree):
    """(rows, site, problems) — the ONE dream step registry the lib defines."""
    found = []
    for rel, mod in lib_sources(tree):
        for node in ast.walk(mod):
            if not isinstance(node, ast.Assign):
                continue
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if REGISTRY_NAME not in names:
                continue
            found.append((rel, _registry_rows(node.value)))
    if not found:
        return None, None, ["no %s assignment found under %s"
                            % (REGISTRY_NAME, LIB_REL)]
    if len(found) > 1:
        return None, None, ["%s is assigned in %d places: %s"
                            % (REGISTRY_NAME, len(found),
                               ", ".join(rel for rel, _ in found))]
    rel, rows = found[0]
    if rows is None:
        return None, rel, ["%s in %s is not a literal table of (id, label, fn) "
                           "rows, so no gate can read it" % (REGISTRY_NAME, rel)]
    return rows, rel, []


def function_sources(tree):
    """name -> source text, for every top-level def in the lib's dream corpus."""
    out = {}
    lib = os.path.join(tree, LIB_REL)
    for base, dirs, names in os.walk(lib):
        dirs[:] = sorted(d for d in dirs if d not in SCAN_SKIP_DIRS)
        for name in sorted(n for n in names if n.endswith(".py")):
            path = os.path.join(base, name)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    body = fh.read()
                mod = ast.parse(body)
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue
            rel = os.path.relpath(path, tree)
            for node in ast.walk(mod):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.setdefault(node.name, (rel, ast.get_source_segment(
                        body, node) or ""))
    return out


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def check_registry(tree):
    """Check 1: the registry matches this repo's retired set."""
    rows, rel, problems = find_registry(tree)
    if problems:
        return problems
    ids = [sid for sid, _ in rows]
    labels = [label for _, label in rows]
    if RETIRED_STEP_ID in ids:
        problems.append("%s (%s) still registers the retired step id %r"
                        % (rel, REGISTRY_NAME, RETIRED_STEP_ID))
    if RETIRED_STEP_LABEL in labels:
        problems.append("%s (%s) still registers the retired step label %r"
                        % (rel, REGISTRY_NAME, RETIRED_STEP_LABEL))
    local_ids = [sid for sid, _ in LOCAL_RETIRED_STEPS]
    local_labels = dict(LOCAL_RETIRED_STEPS)
    unknown = [sid for sid in ids if sid not in local_labels]
    if unknown:
        problems.append("step id(s) %s are in neither this repo's retired set "
                        "nor the retirement: %s"
                        % (", ".join(repr(u) for u in unknown), rel))
    ordered = [sid for sid in local_ids if sid in set(ids)]
    if ordered != [sid for sid in ids if sid in local_labels]:
        problems.append("registry order %s disagrees with this repo's order %s"
                        % (ids, local_ids))
    for sid, label in rows:
        want = local_labels.get(sid)
        if want is not None and label != want:
            problems.append("step %r is labelled %r here and %r in this repo"
                            % (sid, label, want))
    missing = tuple(sid for sid in local_ids if sid not in set(ids))
    if missing != tuple(PENDING_RESTORE_STEPS):
        problems.append("the registry is missing %s; exactly %s is expected "
                        "(plan task 46 restored s/k/m/g, so the expected set is "
                        "now EMPTY -- any absence here is a dropped step)"
                        % (list(missing), list(PENDING_RESTORE_STEPS)))
    return problems


def check_retired_callables(tree):
    """Check 2: no retired callable is defined or called anywhere in the lib."""
    problems = []
    retired = set(RETIRED_CALLABLES)
    for rel, mod in lib_sources(tree):
        for node in ast.walk(mod):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in retired:
                    problems.append("%s defines the retired callable %s"
                                    % (rel, node.name))
            elif isinstance(node, ast.Name) and node.id in retired:
                problems.append("%s references the retired callable %s"
                                % (rel, node.id))
            elif isinstance(node, ast.Attribute) and node.attr in retired:
                problems.append("%s references the retired callable %s"
                                % (rel, node.attr))
            elif isinstance(node, ast.Constant) and node.value in retired:
                problems.append("%s names the retired callable %s as a string"
                                % (rel, node.value))
    return sorted(set(problems))


def _read(path):
    """The file's text, or None when it is absent or unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def _exempt_by_identity(tree, rel):
    """True when `rel` is listed AND is still the file the exemption was written
    for — the whole difference between exempting a FILE and exempting a PATH.

    A path-keyed exemption grants itself to whatever occupies the path next, so
    it survives its subject and turns into a standing invitation. Checking the
    anchor makes the exemption non-transferable: the monolith carries the
    retirement note it is exempt FOR, and anything else at that path — a
    resurrection, a stub, a rewrite — is scanned like every other published file.
    """
    anchor = SCAN_EXEMPT_FILES.get(rel)
    if anchor is None:
        return False
    body = _read(os.path.join(tree, rel))
    return body is not None and anchor in body


def _scannable(tree):
    """Every published file the literal scan covers, with its exemptions."""
    for base, dirs, names in os.walk(tree):
        dirs[:] = sorted(d for d in dirs if d not in SCAN_SKIP_DIRS)
        rel_base = os.path.relpath(base, tree)
        top = rel_base.split(os.sep)[0]
        if top in SCAN_EXEMPT_DIRS:
            dirs[:] = []
            continue
        for name in sorted(names):
            path = os.path.join(base, name)
            rel = os.path.relpath(path, tree)
            if os.path.islink(path) or _exempt_by_identity(tree, rel):
                continue
            yield rel, path


def scan_hits(tree, needle):
    """Every scannable file whose path or body carries `needle`."""
    needle = needle.lower()
    hits = []
    for rel, path in _scannable(tree):
        if needle in rel.lower():
            hits.append(rel + " (filename)")
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                body = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        if needle in body.lower():
            hits.append(rel)
    return hits


def stale_exemptions(tree):
    """Listed exempt files that DO NOT EXIST in `tree` — retired, so inert.

    Not a problem in itself: the whole point of the shim retirement is that this
    file goes away. Split out as its own answer so "the exemption retired with
    its file" is a state the gate can NAME, rather than one it silently shares
    with "the exemption is still hiding something". Inert is now a PROPERTY and
    not a hope: with the anchor required, a stale entry cannot be inherited by
    whatever lands at the path next (see `impostor_exemptions`).
    """
    return sorted(rel for rel in SCAN_EXEMPT_FILES
                  if not os.path.isfile(os.path.join(tree, rel)))


def impostor_exemptions(tree):
    """Listed paths occupied by a file that is NOT the one exempted.

    Reported, not punished: the file is scanned like any other, so it hides
    nothing and check 3 speaks for it if it names the retired step. Named
    because the alternative is the silent state this whole table exists to
    avoid — an entry that looks like it is exempting something while the thing
    it was written for is long gone.
    """
    out = []
    for rel in SCAN_EXEMPT_FILES:
        if not os.path.isfile(os.path.join(tree, rel)):
            continue
        if not _exempt_by_identity(tree, rel):
            out.append(rel)
    return sorted(out)


def unused_exemptions(tree):
    """Exempted files that carry NO retired token — dead weight.

    An exemption is only defensible while it is EARNED. A file kept on this list
    after the text that needed exempting is edited out is an un-argued blind
    spot over live, shipped code — the one shape of this table that can mask a
    real finding, and the one nothing else would report. Scoped to files that
    are genuinely exempt: an impostor is already being scanned, so calling its
    entry a blind spot would be false.
    """
    out = []
    for rel in SCAN_EXEMPT_FILES:
        if not _exempt_by_identity(tree, rel):
            continue
        body = (_read(os.path.join(tree, rel)) or "").lower()
        if not any(tok.lower() in body for tok in RETIRED_TOKENS):
            out.append(rel)
    return sorted(out)


def check_no_hits(tree):
    """Check 3: the retired step's names have zero hits in published files."""
    problems = []
    for token in RETIRED_TOKENS:
        hits = scan_hits(tree, token)
        if hits:
            problems.append("%d file(s) still name the retired step: %s"
                            % (len(hits), ", ".join(hits[:8])))
    for rel in unused_exemptions(tree):
        problems.append("%s is scan-exempt but carries no retired token — an "
                        "exemption that is not earned is a blind spot over "
                        "live code; drop it from SCAN_EXEMPT_FILES" % rel)
    return problems


def _mutating_git_calls(source):
    """Literal git argv sequences in `source` that would advance a work tree."""
    try:
        mod = ast.parse(source)
    except SyntaxError:
        return []
    found = []
    for node in ast.walk(mod):
        if not isinstance(node, ast.Call):
            continue
        literals = [a.value for a in node.args
                    if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        targets = [a.attr if isinstance(a, ast.Attribute) else
                   a.id if isinstance(a, ast.Name) else None
                   for a in node.args]
        if not any(t in REPO_ROOT_NAMES for t in targets):
            continue
        for verbs in MUTATING_GIT_ARGS:
            if all(v in literals for v in verbs):
                found.append(" ".join(verbs))
    return found


def check_no_package_mutation(tree):
    """Check 4: no dream step reaches, or advances, the package root."""
    rows, _rel, problems = find_registry(tree)
    if problems:
        return problems
    sources = function_sources(tree)
    registry_fns = _registry_functions(tree)
    problems = []
    for sid, label in rows:
        fn_name = registry_fns.get(sid)
        if fn_name is None:
            problems.append("step %r (%s) has no resolvable handler name"
                            % (sid, label))
            continue
        entry = sources.get(fn_name)
        if entry is None:
            problems.append("step %r handler %s is not defined in the lib"
                            % (sid, fn_name))
            continue
        rel, src = entry
        for root_name in REPO_ROOT_NAMES:
            if root_name in src:
                problems.append("dream step %r (%s) in %s reaches the package "
                                "root (%s) — dream never mutates the repo"
                                % (sid, label, rel, root_name))
        for verb in _mutating_git_calls(src):
            problems.append("dream step %r (%s) in %s runs `git %s` on the "
                            "package root" % (sid, label, rel, verb))
    problems.extend(_corpus_hard_reset_problems(tree))
    return problems


def _registry_functions(tree):
    """step id -> the handler NAME the registry names for it."""
    out = {}
    for rel, mod in lib_sources(tree):
        for node in ast.walk(mod):
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(t, ast.Name) and t.id == REGISTRY_NAME
                       for t in node.targets):
                continue
            if not isinstance(node.value, (ast.Tuple, ast.List)):
                continue
            for element in node.value.elts:
                if not isinstance(element, (ast.Tuple, ast.List)):
                    continue
                if len(element.elts) < 3:
                    continue
                sid, fn = element.elts[0], element.elts[2]
                if not isinstance(sid, ast.Constant):
                    continue
                if isinstance(fn, ast.Name):
                    out[sid.value] = fn.id
                elif isinstance(fn, ast.Attribute):
                    out[sid.value] = fn.attr
    return out


def _corpus_hard_reset_problems(tree):
    """No file in the lib's dream corpus may hard-reset anything at all."""
    problems = []
    for base, dirs, names in os.walk(os.path.join(tree, LIB_REL)):
        dirs[:] = sorted(d for d in dirs if d not in SCAN_SKIP_DIRS)
        for name in sorted(n for n in names if n.endswith(".py")):
            if "dream" not in name.lower() and "dream" not in base.lower():
                continue
            path = os.path.join(base, name)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    body = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            if '"--hard"' in body or "'--hard'" in body:
                problems.append("%s hard-resets a work tree"
                                % os.path.relpath(path, tree))
    return problems


def run_cli(tree, argv):
    """(rc, stdout, stderr) — the tree's own entrypoint, fully redirected."""
    entry = os.path.join(tree, ENTRY_REL)
    if not os.path.exists(entry):
        return None, "", "entrypoint missing: %s" % ENTRY_REL
    env = dict(os.environ)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    with tempfile.TemporaryDirectory(prefix="aw-dreampkg-") as scratch:
        kdir = os.path.join(scratch, "kb")
        os.makedirs(kdir)
        with open(os.path.join(kdir, ".initialized"), "w") as fh:
            fh.write("gate\n")
        with open(os.path.join(kdir, "MAIN.md"), "w") as fh:
            fh.write("# knowledge base\n")
        env["AGENTWARE_KNOWLEDGE_DIR"] = kdir
        env["AGENTWARE_CONFIG_HOME"] = os.path.join(scratch, "config")
        os.makedirs(env["AGENTWARE_CONFIG_HOME"])
        try:
            proc = subprocess.run([sys.executable, entry] + list(argv),
                                  env=env, cwd=tree, capture_output=True,
                                  text=True, timeout=300)
        except (OSError, subprocess.SubprocessError) as exc:
            return None, "", "could not run: %s" % (exc,)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def check_behaviour(tree):
    """Check 5: the CLI refuses the retired step and plans no package pull."""
    problems = []
    rc, out, err = run_cli(tree, ["dream", "--steps", RETIRED_STEP_ID,
                                  "--dry-run"])
    blob = out + err
    if rc is None:
        problems.append("refusal probe: %s" % err)
    elif rc == 0:
        problems.append("--steps %s was ACCEPTED (rc=0): the retired step is "
                        "still runnable" % RETIRED_STEP_ID)
    elif "unknown dream step" not in blob:
        problems.append("--steps %s failed rc=%s but not as an unknown step: %r"
                        % (RETIRED_STEP_ID, rc, blob.strip()[:200]))

    rc, out, err = run_cli(tree, ["dream", "--dry-run", "--format", "json"])
    if rc is None:
        problems.append("dry-run probe: %s" % err)
        return problems
    if rc != 0:
        problems.append("dream --dry-run exited %s: %s" % (rc, err.strip()[:200]))
        return problems
    try:
        payload = json.loads(out)
    except ValueError as exc:
        problems.append("dream --dry-run emitted no JSON (%s)" % (exc,))
        return problems
    for step in payload.get("steps") or []:
        if step.get("step") == RETIRED_STEP_ID:
            problems.append("the dry-run plan still contains step %r"
                            % RETIRED_STEP_ID)
        would = json.dumps(step).lower()
        if "pull agentware" in would or "agentware repo" in would:
            problems.append("step %r plans a package pull: %s"
                            % (step.get("step"), would[:160]))
    return problems


CHECKS = (
    ("registry", check_registry),
    ("retired", check_retired_callables),
    ("no_hits", check_no_hits),
    ("no_mutation", check_no_package_mutation),
    ("behaviour", check_behaviour),
)


def check(tree):
    problems = []
    for name, fn in CHECKS:
        problems.extend("[%s] %s" % (name, p) for p in fn(tree))
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tree", help="tree carrying scripts/agentware_lib")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    tree, problems = resolve_tree(args.tree)
    if not problems:
        problems = check(tree)
    if args.json:
        print(json.dumps({"tree": tree, "ok": not problems,
                          "problems": problems}, indent=2))
    else:
        print("tree: %s" % (tree or "(none)"))
        for p in problems:
            print("PROBLEM %s" % p)
        print("%s %d problem(s)" % ("FAIL" if problems else "OK", len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
