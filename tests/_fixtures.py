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
import inspect
import io
import importlib.util
import os
import shutil
import sys
import tempfile
import types
import unittest
from importlib.machinery import SourceFileLoader

# Repo root = parent of tests/. The CLI is a no-extension python script.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI_PATH = os.path.join(REPO_ROOT, "scripts", "agentware")

#: The preserved monolith, byte-identical to `aw-pre-merge:scripts/agentware`.
#: Present only during the strangler window (feature 260813 tasks 25..56).
LEGACY_PATH = os.path.join(REPO_ROOT, "scripts", "agentware_legacy.py")

#: The product's own recursion guard (`retrieval.ledger._record_test_passrate_detail`,
#: `ops.audit._audit_tests_check`): when set, a verb that would shell the suite to
#: measure a pass-rate records "not measured" instead. Named here rather than
#: spelled inline so `run_cli` and the gate that proves it stay on ONE string.
NESTED_UNITTEST_ENV = "AGENTWARE_NESTED_UNITTEST"

_CLI_MODULE = None

#: `sys.modules` key holding the ONE CLI module object the suite may have. Not a
#: real import path -- nothing can reach it with an `import` statement -- and
#: deliberately outside the `agentware_lib` namespace the write-through scans.
_CLI_SYS_KEY = "agentware_cli"


def first_party_modules():
    """Top-level module names that SHIP IN THIS REPO under `scripts/`.

    INV-6 is "zero hard DEPENDENCIES", not "zero imports": a name that resolves
    to a file the clone already carries installs nothing, downloads nothing and
    cannot go missing. `agentware_lib` is the package's own decomposed body, so
    the stdlib-only scans must not read it as a third-party dependency — but
    they must go on catching a real one.

    DERIVED from disk, never hardcoded: a hardcoded allowlist is how a scan
    keeps passing after the thing it allowed was renamed, and this migration
    renames modules constantly. Anything that appears under `scripts/` without
    being committed here would still have to be a tracked repo file to appear at
    all, which is the property the check actually cares about.
    """
    scripts = os.path.join(REPO_ROOT, "scripts")
    names = set()
    for entry in os.listdir(scripts):
        full = os.path.join(scripts, entry)
        if entry.endswith(".py"):
            names.add(entry[:-3])
        elif os.path.isdir(full) and os.path.isfile(
                os.path.join(full, "__init__.py")):
            names.add(entry)
    return names


def cli_source():
    """The CLI IMPLEMENTATION source, wherever it currently lives.

    A source-scanning test asserts on the implementation, not on a path. Task 25
    turned `scripts/agentware` into a thin facade and moved the monolith's body
    to `scripts/agentware_legacy.py` (which the facade execs into its own
    namespace), so a test still reading CLI_PATH alone would scan a 74-line
    docstring and pass — or fail — for reasons that have nothing to do with the
    behaviour it guards. That is the silent-vacuous shape this migration keeps
    finding, so the seam is defined ONCE here rather than re-pinned per test.

    Returns the entrypoint, the legacy module while it exists, AND the module that
    owns each PORTED verb (feature 260813 task 38). That third part is not an
    embellishment: from the first port the implementation is SPLIT across two
    trees, so a scan reading only the monolith reports a ported function as
    "removed" rather than as "moved", and by task 56 — when the monolith is gone —
    it would scan a facade with no `def` in it and report every guarded property as
    satisfied. The set is resolved LIVE from `strangler.PORTED_VERBS` crossed with
    the DISCOVERED verb registration, so it grows with each port and needs no
    edit here; ordering is that registration's, so the concatenation is
    deterministic. Every part starts its defs at column 0, so line-order scans that
    track the enclosing definition are unaffected by the concatenation.
    """
    parts = []
    for path in (CLI_PATH, LEGACY_PATH):
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                parts.append(fh.read())
    for path in ported_module_paths():
        with open(path, encoding="utf-8") as fh:
            parts.append(fh.read())
    return "\n".join(parts)


def ported_module_paths():
    """Absolute paths of the lib modules owning a verb in ``PORTED_VERBS``.

    In discovered display order, de-duplicated (one module may own several verbs)
    and filtered to files that exist. Importing the facade first is what puts
    `scripts/` on `sys.path`, so this resolves under a bare
    `python3 -m unittest` from the repo root exactly as `load_lib` does.
    """
    load_cli()
    from agentware_lib import strangler, verbs
    seen, out = set(), []
    for verb, module, _attr in verbs.registration():
        if verb not in strangler.PORTED_VERBS or module in seen or not module:
            continue
        seen.add(module)
        path = os.path.join(REPO_ROOT, "scripts",
                            *(module.split("."))) + ".py"
        if os.path.isfile(path):
            out.append(path)
    # ...plus EVERY other lib module, walked from disk in sorted order.
    #
    # A verb's implementation is no longer one module — `update` alone spans six
    # (pkgstate / pkgchannel / pkgapply / pkgreconcile / pkgupdater + the CLI) —
    # so a scan seeing only the registrar reports properties proven by the other
    # five as ABSENT. That is the silent-vacuous shape this helper exists to
    # prevent, and by task 56 the monolith is gone and the lib IS the whole
    # implementation, so walking it is also the end state.
    #
    # Walked from DISK, deliberately NOT from `sys.modules`: that set accumulates
    # as tests import things, so the scanned surface would depend on test ORDER —
    # a gate that passes alone and fails in company (or worse, the reverse).
    lib_root = os.path.join(REPO_ROOT, "scripts", "agentware_lib")
    for base, dirs, names in os.walk(lib_root):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(names):
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            if path not in out:
                out.append(path)
    return out


def _same_symbol_two_copies(name, cur, previous):
    """True when `cur` and `previous` are the SAME symbol living in two places.

    This exists only for the strangler window, and it disappears with the shim.
    While a verb is unported, the preserved monolith and the lib each define a
    copy of the shared helpers (`_run_gate`, `save_lanes`, `_mq_integrate_*`, …).
    They are equal in behaviour but are DISTINCT function objects, so the
    name-plus-value match above forwards nothing — and a test that patches the
    facade then stubs the copy the code under test never calls. That failure is
    silent in the worst way: the stub is installed, the assertion runs, and the
    REAL collaborator did the work.

    The match is deliberately narrow — both sides must be plain functions
    carrying the SAME `__name__` and the SAME parameter list. Two unrelated
    functions that agree on both of those, under a name the facade also exports,
    is not a case this codebase has; a same-named helper with a different
    signature (the realistic collision) is excluded by the signature check.
    """
    if not (inspect.isfunction(cur) and inspect.isfunction(previous)):
        return False
    if cur.__name__ != previous.__name__ or cur.__name__ != name:
        return False
    try:
        return (inspect.signature(cur).parameters.keys()
                == inspect.signature(previous).parameters.keys())
    except (TypeError, ValueError):
        return False


_LIB_WALKED = [False]


def _import_all_lib_modules():
    """Import every `agentware_lib` submodule once, so name resolution is TOTAL.

    LAZY and memoized: called only when a name cannot be found among the modules
    already imported. Doing it eagerly at `load_cli()` changed the import state of
    every test in the suite -- ~217 extra modules imported before anything ran --
    and produced 13 order-dependent failures that all passed in isolation. While
    the monolith exists this never runs at all, because `__getattr__` never fires.

    `_lib_owner_of` searches `sys.modules`, which holds only what something
    happened to import. Without this walk the answer to "which module owns this
    name" would depend on which tests ran first — found in one session, missing in
    another. That is the worst possible property for a harness, so the walk makes
    it order-independent. Failures are skipped: an optional-dependency module that
    cannot import is not a reason to fail every test.

    `scripts/` goes on sys.path here as well as in the facade, because this runs
    BEFORE the facade is exec'd.
    """
    import pkgutil
    scripts_dir = os.path.dirname(os.path.abspath(CLI_PATH))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    try:
        import agentware_lib
    except Exception:                       # pragma: no cover - no lib present
        return
    for info in pkgutil.walk_packages(agentware_lib.__path__,
                                      prefix="agentware_lib."):
        try:
            importlib.import_module(info.name)
        except Exception:                   # pragma: no cover - optional deps
            continue


def _lib_owner_of(name):
    """The lib module that DEFINES `name`, else the first that RE-EXPORTS it.

    Prefers the definer (`value.__module__ == modname`) over a re-exporter,
    because `_ConfigMirrorModule.__setattr__` forwards a patch to every binding
    and the patch must reach the definition rather than one of its copies.
    Deterministic: candidates are considered in sorted module order, so two
    modules re-exporting the same name always resolve the same way.
    """
    hit = _scan_imported_for(name)
    if hit[0] is not None:
        return hit
    # Not among the modules imported so far -- walk the package once, then retry.
    _import_all_lib_modules()
    return _scan_imported_for(name)


class _Absent:
    """Sentinel for "this name had no previous value on the mirror".

    A plain `None` cannot carry that meaning: `SOME_FLOOR = None` is a real,
    common patch (turn the feature OFF), and a dict `.get()` returning None for
    both cases makes the two indistinguishable. Every place that has to decide
    "is there a previous value to key the write-through on" asks `is _ABSENT`.
    """

    __slots__ = ()

    def __repr__(self):  # pragma: no cover - diagnostics only
        return "<absent>"


_ABSENT = _Absent()


def _is_lib_submodule_ref(val):
    """True iff `val` is a reference to an `agentware_lib` SUBMODULE.

    THE EXCLUSION RULE, NARROWED (task 94). b6a9de6 skipped EVERY `ModuleType`
    value, which fixed the real collision it was written for -- a package
    attribute (`agentware_lib.retrieval.acr`, the submodule) shadowing the
    function of the same name defined inside it -- but over-reached. The
    monolith opens with `import subprocess`, `import time`, `import os`,
    `import json` and a dozen more, so those STDLIB module objects genuinely
    WERE in the namespace this shim stands in for; only `agentware_lib`
    submodules never were (the monolith reaches the lib through `from ...
    import <symbol>`, never by binding a package attribute).

    Keeping the narrow rule is also what makes an external module a HONEST
    patch seam: `sys.modules` holds one object per module, so a test that
    stubs `CLI.subprocess.run` is mutating the very object the lib
    implementation reads. There is no copy to go vacuous against.
    """
    return (isinstance(val, types.ModuleType)
            and getattr(val, "__name__", "").startswith("agentware_lib"))


def _scan_imported_for(name):
    """`_lib_owner_of` over the ALREADY-imported lib modules only."""
    fallback = None
    for modname in sorted(m for m in sys.modules
                          if isinstance(m, str) and m.startswith("agentware_lib")):
        m = sys.modules.get(modname)
        if m is None or name not in vars(m):
            continue
        val = vars(m)[name]
        if _is_lib_submodule_ref(val):
            continue          # a submodule named like the symbol, not the symbol
        if getattr(val, "__module__", None) == modname:
            return m, val
        if fallback is None:
            fallback = (m, val)
    return fallback if fallback else (None, None)


class _ConfigMirrorModule(types.ModuleType):
    """CLI module type that MIRRORS config-redirect writes onto the lib.

    STRANGLER-WINDOW TEST BRIDGE (feature 260813; remove with the passthrough at
    task 56). ~180 tests isolate state by rebinding `mod.HOME_CONFIG` /
    `mod.CONFIG_PATHS` on the CLI module. As each port moves a reader into
    `agentware_lib`, that reader resolves the value from
    `agentware_lib.core.config` instead, and a rebind here stops reaching it — so
    the test silently falls back to the OPERATOR'S REAL `~/.agentware` and
    read/writes live state. That is not a failing test, it is a dangerous passing
    one; task 40 hit it across 117 tests in 18 files at once.

    Mirroring here rather than in the shipped monolith is deliberate. The monolith
    is `exec`'d into the facade's globals, so it cannot reach its own module
    object (`sys.modules[__name__]` is not it) — and test-harness scaffolding does
    not belong in shipped code regardless. This class is applied to the module
    object THIS function creates, so it is confined to the suite.

    Narrow by design: only the two documented monkeypatch globals mirror
    (configkeys.py names them); every other attribute write is ordinary. Reads are
    untouched — the value still lives in the module's own dict.
    """

    _MIRRORED = ("HOME_CONFIG", "CONFIG_PATHS")

    #: name -> owning agentware_lib module, for every symbol the CLI RE-EXPORTS
    #: from the lib. Built once, lazily, by asking each attribute where it was
    #: defined. This is what makes `mock.patch.object(mod, "_git_timed")` still
    #: reach the ported reader: the patch target moved with the implementation,
    #: and a rebind on the CLI alone would be a patch that silently patches
    #: nothing while the real function keeps running.
    def _lib_bindings(self, name, previous):
        """Every lib module binding `name` to the value being REPLACED.

        `previous` is `_ABSENT` when the mirror held no prior value at all —
        distinct from a prior value that genuinely WAS None (see below).

        Matched on NAME plus value, not on identity alone. Identity is too strict:
        `CLI.REPO_ROOT` and `core.paths.REPO_ROOT` are computed independently, so
        they are EQUAL but not the same object, and an identity-only sweep would
        silently forward nothing — a patch that patches nothing while the real
        value keeps being used.

        Resolved FRESH on every write rather than cached: a cached map goes stale
        the moment a later test imports another lib module. Keying on the PREVIOUS
        value also makes restore work for free — after a patch those modules hold
        the mock, so `mock.patch` undoing itself finds exactly the same bindings.
        """
        # NOTHING is excluded by VALUE any more (task 94). Interning was the
        # original worry — which is why numbers were skipped at first — but the
        # match below is on NAME *and* value, and the name is discriminator
        # enough: a lib module binding the same NAME to the same value is the
        # same constant. Excluding ints meant every numeric THRESHOLD a test
        # patches (checkup's corpus cap, the worktree cap, the p50 budget)
        # silently failed to forward, so the probe graded against the shipped
        # default while the test believed otherwise.
        #
        # `None` was the last value still excluded, and it was excluded for a
        # reason that is NOT about the value at all: `self.__dict__.get(name)`
        # returned None both for "the previous value WAS None" and for "there is
        # no previous value", and only the second may forward nowhere. The two
        # are now told apart by the `_ABSENT` sentinel at the call site, so a
        # genuine None forwards like any other value. Conflating them cost a
        # PERMANENT hole: a test setting a constant to None (the ubiquitous
        # "disable this feature" patch) left every lib copy stuck at None, and
        # because the NEXT write then had `previous is None` too, it forwarded
        # nowhere either — the mirror alone took the new value while the product
        # kept reading None for the rest of the process, across test modules.
        if previous is _ABSENT:
            return []
        out = []
        for modname, m in list(sys.modules.items()):
            if not (isinstance(modname, str)
                    and modname.startswith("agentware_lib") and m is not None):
                continue
            try:
                if name not in vars(m):
                    continue
                cur = vars(m)[name]
                if cur is previous or cur == previous:
                    out.append(m)
                elif _same_symbol_two_copies(name, cur, previous):
                    out.append(m)
            except Exception:
                continue
        return out


    def __getattr__(self, name):
        """Resolve a name the CLI module does not define from `agentware_lib`.

        TASK 56. While the monolith exists this never fires: it is `exec`'d into
        the module's globals, so every legacy symbol is already in the module dict
        and normal lookup wins. Once the monolith is DELETED it carries the whole
        suite — 2,273 of the 2,411 failures in the deletion probe were this single
        AttributeError, ~3,000 reads of `CLI.<name>` with nothing to hit.

        Resolving here rather than repointing every call site is deliberate, and
        it is not the shim rebuilt in the harness: the value is fetched from the
        module that OWNS it and CACHED in this module's dict, which is what keeps
        the write-through in `__setattr__` working. That forwarding keys on the
        PREVIOUS value, so a name never read would have no previous value and a
        patch on it would forward nowhere — read-then-patch is the dominant shape
        in this suite, so caching on read is what keeps patches honest rather
        than silently vacuous.
        """
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        owner, val = _lib_owner_of(name)
        if owner is None:
            raise AttributeError(
                "module %r has no attribute %r, and no agentware_lib module "
                "defines it either" % (self.__name__, name))
        types.ModuleType.__setattr__(self, name, val)
        return val

    def __setattr__(self, name, value):
        # `_ABSENT`, not None: "no previous value" and "the previous value was
        # None" are DIFFERENT facts and only the first may forward nowhere.
        previous = self.__dict__.get(name, _ABSENT)
        if isinstance(previous, types.ModuleType):
            # REFUSE a wholesale MODULE rebind, loudly (task 94).
            #
            # `CLI.subprocess` / `CLI.os` / `CLI.time` are the stdlib module
            # OBJECTS the monolith imported at its head, and the suite patches
            # them the only way that works: mutating an attribute ON the shared
            # object (`CLI.subprocess.run = fake`), which every importer sees
            # because `sys.modules` holds ONE instance.
            #
            # A wholesale rebind has no honest handling. Forwarding it would
            # swap the module out of every lib module that imports it -- 144
            # for `os`, 113 for `json`, 34 for `subprocess`, measured -- a
            # process-wide swap whose restore depends entirely on the reverse
            # forward finding all of them again. NOT forwarding is worse: the
            # rebind lands on this module alone, the implementation keeps
            # reading the real module, and the test passes while patching
            # nothing. Both are silent, so neither is allowed; the seam that
            # does work is named instead.
            raise AttributeError(
                "refusing to rebind module attribute %r on %r: patch the "
                "attribute ON the module instead (e.g. `%s.%s = fake`), which "
                "reaches every reader through sys.modules. A wholesale rebind "
                "either forwards into every lib module that imports it or "
                "silently patches nothing." % (
                    name, self.__name__, name, "<attr>"))
        types.ModuleType.__setattr__(self, name, value)
        # Resolved BEFORE any forward: `_lib_bindings` matches on the PREVIOUS
        # value, so writing to one target first would stop the rest matching.
        targets = self._lib_bindings(name, previous)
        if name in self._MIRRORED:
            # The OWNER is mirrored unconditionally -- including when there is no
            # previous value at all, which `_lib_bindings` deliberately forwards
            # nowhere. `load_cli` applies this class only AFTER exec precisely so
            # the module body's own assignment of the real home does not clobber
            # a redirect an outer test already installed on the lib, and that
            # ordering is only safe while the owner write is unconditional.
            try:
                import agentware_lib.core.config as _libconfig
                if _libconfig not in targets:
                    targets.append(_libconfig)
            except Exception:
                pass
        # The CONSUMERS are forwarded too, and this is not a refinement (task 94).
        # `_MIRRORED` used to `return` after writing the owner, so a lib module
        # that did `from agentware_lib.core.config import HOME_CONFIG` -- binding
        # the VALUE at import, not the module -- kept the operator's REAL home for
        # the whole process. That is the exact failure this class exists to
        # prevent, inverted: the test rebinds, believes it is hermetic, and the
        # implementation reads `~/.agentware`. It stayed invisible while the
        # monolith stood because `CLI.<name>` then resolved to the monolith's own
        # copy, whose `__globals__` IS this module dict, so the rebind was read
        # back directly and no consumer was consulted. `features.skilllock` is the
        # live instance -- `_skill_lock_path()` resolved the real lockfile path
        # under a redirected HOME.
        for m in targets:
            try:
                setattr(m, name, value)
            except Exception:
                pass


def load_cli():
    """Import scripts/agentware as a module (cached). Stdlib importlib only.

    Cached in `sys.modules`, not only in this module's global, and that is not
    belt-and-braces (task 95). Under `discover -s tests -t .` BOTH `tests/` and
    the `tests` package are importable, and the suite uses both spellings --
    ~144 files say `import _fixtures`, ~164 say `from tests._fixtures import`.
    Python treats those as two DIFFERENT modules, so this file was executed
    twice, each copy holding its own `_CLI_MODULE` and its own
    `_ConfigMirrorModule` class object.

    Two CLI modules is not a compatibility seam, it is a coin flip. Writes
    through one still reach the lib OWNER (so the product sees a patch), but
    the other copy's dict keeps the pre-patch value -- so a test reading
    `CLI.<name>` to capture "the current value", and restoring it afterwards,
    can write a stale value back over a live redirect. That is the same silent
    shape the mirror exists to prevent, one level up.

    A single `sys.modules` entry makes the seam single-instance whichever
    spelling a test imports. The key is not importable as a package path, so
    nothing acquires it by accident, and it does not begin with
    `agentware_lib`, so the write-through scan never mistakes it for an owner.
    """
    global _CLI_MODULE
    if _CLI_MODULE is None:
        shared = sys.modules.get(_CLI_SYS_KEY)
        # Duck-typed, NOT isinstance: the whole point is that the other copy of
        # this file has its own `_ConfigMirrorModule` class object, so an
        # isinstance check would reject exactly the module we are adopting.
        if shared is not None and type(shared).__name__ == "_ConfigMirrorModule":
            _CLI_MODULE = shared
        else:
            loader = SourceFileLoader(_CLI_SYS_KEY, CLI_PATH)
            spec = importlib.util.spec_from_loader(_CLI_SYS_KEY, loader)
            mod = importlib.util.module_from_spec(spec)
            loader.exec_module(mod)
            # Applied AFTER exec: the module body itself assigns HOME_CONFIG to
            # the real home, and mirroring that would clobber a redirect an
            # outer test had already installed on the lib.
            mod.__class__ = _ConfigMirrorModule
            _populate_lib_names(mod)
            sys.modules[_CLI_SYS_KEY] = mod
            _CLI_MODULE = mod
    _rebind_tick_registry(_CLI_MODULE)
    return _CLI_MODULE



def attach_lib_resolver(mod):
    """Give ANY separately-loaded CLI module the same lib name resolution.

    Four suites load `scripts/agentware` under their own module names
    (`aw_g1harden`, `aw_schemaguard`, `aw_gatebaseline`, `agentware_cli_smoke`)
    to get an isolated namespace. They build plain module objects, so they miss
    `_ConfigMirrorModule` entirely — and with the monolith deleted every legacy
    read through them raises. Applying the class here rather than copying the
    resolver into each keeps ONE definition of "where does this name live".

    Returns `mod` so it can wrap a loader call directly.
    """
    mod.__class__ = _ConfigMirrorModule
    return mod

def _populate_lib_names(mod):
    """Bind every already-imported lib name onto the CLI module, as the exec did.

    `mock.patch.object` reads `target.__dict__[name]` DIRECTLY to decide whether
    the attribute is local. A name reachable only through `__getattr__` is not in
    that dict, so mock takes its non-local path and, on exit, DELETES the
    attribute instead of restoring it -- and the write-through in `__setattr__`
    never sees a restore, so the owning lib module keeps the mock for the rest of
    the process. That is unbounded cross-test pollution: the 6 package suites went
    from 62 passing to 12 failing purely on that, all of them passing alone.

    Binding the names up front reproduces exactly what the monolith's `exec` gave
    this module, so mock takes the local path it has always taken. Only names
    ALREADY imported are bound -- importing the whole package eagerly is what
    caused an earlier round of order-dependent failures, and importing
    `agentware_lib.cli` (which the facade does) already pulls in nearly all of it.
    `__getattr__` remains the fallback for the remainder.

    Never OVERWRITES a name the module already defines: the facade's own bindings
    and, while it exists, the exec'd monolith's, both win.

    The package IS walked here, unlike the resolver's lazy path: binding has to
    happen BEFORE a test patches, and `agentware_lib.cli` alone does not reach
    every module (`features.pkgapply` among them). The walk is imports only --
    the thing that made an earlier eager version harmful was that it also ran at
    a point where module-level side effects had somewhere to leak; tick-job
    registration is explicit now, so this is binding, not behaviour.
    """
    _import_all_lib_modules()
    for modname in sorted(m for m in sys.modules
                          if isinstance(m, str) and m.startswith("agentware_lib")):
        m = sys.modules.get(modname)
        if m is None:
            continue
        for name, val in list(vars(m).items()):
            if name.startswith("__") or name in mod.__dict__:
                continue
            # NEVER bind an agentware_lib SUBMODULE reference.
            # `agentware_lib.retrieval` holds an attribute `acr` pointing at the
            # SUBMODULE, while the function the suite wants is `acr` INSIDE it --
            # binding the package attribute made `CLI.acr(...)` raise "module
            # object is not callable" in 16 tests. An EXTERNAL module (stdlib and
            # friends) is the opposite case: the monolith imports `subprocess`,
            # `time`, `os`, `json` at its head, so those names really were in the
            # namespace this reproduces. See `_is_lib_submodule_ref`.
            if _is_lib_submodule_ref(val):
                continue
            if isinstance(val, types.ModuleType):
                types.ModuleType.__setattr__(mod, name, val)
                continue
            if getattr(val, "__module__", None) == modname or not callable(val):
                types.ModuleType.__setattr__(mod, name, val)


def _rebind_tick_registry(mod):
    """Point the lib's tick-job registry at THIS module's callables.

    STRANGLER-WINDOW TEST BRIDGE (remove with the passthrough at task 56).
    `_TICK_JOB_REGISTRY` moved into the lib at task 43, but the jobs are still
    registered BY the monolith, which 17 test modules `exec` into namespaces of
    their own. The registry is imported once and cached, so its entries end up
    closing over whichever namespace registered LAST — which is usually not the
    cached module the tests patch. The symptom is brutal to diagnose: a patch on
    the live namespace appears to do nothing while a job bound to a DEAD namespace
    runs unpatched.

    Production is unaffected (one exec, one registration). This re-points each
    entry's callables at the cached module by NAME on every `load_cli()`, so the
    module a test is about to patch is always the one the dispatcher runs.
    """
    try:
        import agentware_lib.ops.tickcmd as tickcmd
    except Exception:
        return
    for job in tickcmd._TICK_JOB_REGISTRY:
        for slot in ("enabled", "due", "run"):
            fn = job.get(slot)
            name = getattr(fn, "__name__", None)
            if not name or name == "<lambda>":
                continue
            live = getattr(mod, name, None)
            if callable(live) and live is not fn:
                job[slot] = live


def load_lib(dotted):
    """Import an `agentware_lib` module BY ITS OWN NAME and return it.

    The oracle for a PORTED verb (feature 260813 tasks 37..56). `load_cli()`
    returns the facade, whose namespace holds the monolith plus re-exports of the
    ported names — good enough to READ a symbol, useless to PATCH one: a
    ported function reads ITS OWN module globals, so `mock.patch.object(facade,
    "_KB_MIGRATIONS", …)` rebinds a name nothing consults and the test passes
    while proving nothing. So a test that patches must address the owning module,
    and this is the one place that resolution lives.

    Goes through `load_cli()` first, deliberately: the facade is what puts
    `scripts/` on `sys.path`, so importing it is what makes `agentware_lib`
    importable at all under a bare `python3 -m unittest` from the repo root.
    """
    load_cli()
    return importlib.import_module(dotted)


def run_cli(argv, kdir, stdin_text=None):
    """Run the CLI with AGENTWARE_KNOWLEDGE_DIR=kdir; capture (code, out, err).

    Restores any pre-existing env vars afterward so test ordering is irrelevant.

    Hermeticity: this fixture isolates the KB dir, but the CLI also reads
    MACHINE-level operator settings from ~/.agentware/config.env (HOME_CONFIG),
    which is bound at module-import time and so CANNOT be redirected in-process.
    The `audit` command's dream_health check keys on `resolve_dream()`, whose
    precedence is env -> config.env -> default-OFF; on an operator whose real
    config has AGENTWARE_DREAM=1 that value leaks in and makes a synthetic-KB
    audit FAIL for reasons unrelated to the KB under test. Since the env var wins
    over config.env, we default AGENTWARE_DREAM=OFF for the run so machine-level
    dream state never leaks and synthetic-KB audits depend ONLY on KB state. A
    caller that sets AGENTWARE_DREAM explicitly (e.g. the dream tests) is
    respected and its value restored afterward.

    Recursion: several verbs (`eval --record`, `audit`) measure a test pass-rate
    by SHELLING `python3 -m unittest discover -s tests` in a child process. Their
    guard against re-entering the suite is the AGENTWARE_NESTED_UNITTEST env var,
    which only the product sets when IT spawns — so a suite run under any runner
    never sets it, and the first `--record` test spawns a whole nested suite that
    also collects `tests/fixtures/` and wedges (observed: >10 min at 0.0% CPU).
    A process running this fixture is BY DEFINITION already inside a suite run,
    so the guard is defaulted ON here for the duration of the call, exactly like
    AGENTWARE_DREAM above: one seam covering every current and future CLI test
    instead of a per-class setUp each new test must remember (two classes had
    already missed it). A caller that sets it explicitly is respected and its
    value restored afterward; the verbs then record `test_pass_rate: None`
    ("not measured"), which is their documented nested behaviour and what the
    classes that DID remember the guard already assert.
    """
    mod = load_cli()
    prev = os.environ.get("AGENTWARE_KNOWLEDGE_DIR")
    os.environ["AGENTWARE_KNOWLEDGE_DIR"] = kdir
    prev_dream = os.environ.get("AGENTWARE_DREAM")
    force_dream_off = prev_dream is None
    if force_dream_off:
        os.environ["AGENTWARE_DREAM"] = "0"
    force_nested = os.environ.get(NESTED_UNITTEST_ENV) is None
    if force_nested:
        os.environ[NESTED_UNITTEST_ENV] = "1"
    out, err = io.StringIO(), io.StringIO()
    prev_stdin = sys_stdin = None
    try:
        if stdin_text is not None:
            import sys as _sys
            sys_stdin = _sys
            prev_stdin = _sys.stdin
            _sys.stdin = io.StringIO(stdin_text)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            # Through the STRANGLER ROUTER, not straight into legacy `main`
            # (feature 260813 task 37). The real CLI dispatches every invocation
            # through `agentware_lib.strangler.dispatch`, so a fixture that calls
            # `mod.main` directly serves EVERY verb from the monolith regardless
            # of the allowlist — the suite would then be green on an
            # implementation production no longer runs, which is the one failure
            # mode this migration's harness gates exist to prevent. `_strangler`
            # is the entrypoint's own binding, so this adds no import and no
            # sys.path assumption; while the allowlist is empty it is
            # byte-identical to the previous call.
            code = mod._strangler.dispatch(argv, mod.main)
    finally:
        if sys_stdin is not None:
            sys_stdin.stdin = prev_stdin
        if prev is None:
            os.environ.pop("AGENTWARE_KNOWLEDGE_DIR", None)
        else:
            os.environ["AGENTWARE_KNOWLEDGE_DIR"] = prev
        if force_dream_off:
            os.environ.pop("AGENTWARE_DREAM", None)
        if force_nested:
            os.environ.pop(NESTED_UNITTEST_ENV, None)
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
        "author": "testuser",
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
        "author": "testuser",
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
        "author": "testuser",
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
        "author": "testuser",
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
            # Write YAML frontmatter so the provenance audit (which reads from
            # files, not index.json) finds the `author` field in team mode.
            fm_fields = ["id", "title", "category", "tags", "created",
                         "author", "summary"]
            fm_lines = ["---"]
            for k in fm_fields:
                v = e.get(k)
                if v is None:
                    continue
                if isinstance(v, list):
                    fm_lines.append("%s: [%s]" % (k, ", ".join(v)))
                else:
                    fm_lines.append("%s: %s" % (k, v))
            fm_lines.append("---")
            fm_lines.append("")
            f.write("\n".join(fm_lines))
            f.write(e["body"])
    data = _index_from(entries)
    with open(os.path.join(root, "index.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return data


# --- Snapshot-backfill harness (feature 260714-miner-operationalize, Task 5) --
# ONE definition of the corpus-snapshot contract, shared by the hermetic tests
# (synthetic source corpus) and the operator's real-corpus measurement run — so
# the measured harness and the tested harness can never drift apart.

MINER_STATE_REL = "logs/steering/.transcript-miner.state.json"


def seed_miner_state_at_zero(kdir, sids=None):
    """Pre-write the miner state so a snapshot mines the WHOLE corpus (P1 trap).

    THE TRAP THIS EXISTS TO DEFUSE: `_transcript_load_state` SEEDS a fresh state
    with `offsets.prompts = <current file size>` — the TAIL — and
    `_transcript_mine_read` reads prompts.log from that stored offset
    UNCONDITIONALLY. `--backfill` widens only the SESSION selection; it does NOT
    rewind the prompts offset (decide-p2c-recurrence-source-forward-offset). So a
    snapshot KB left to seed itself parks at EOF and mines ZERO prompts —
    silently, and a harness that asserts nothing then passes vacuously.

    The seed written here is the fresh-seed state with ONE deviation: the offsets
    are rewound to 0. `backfill_pending` is seeded with every on-disk sid (NOT
    empty) because `--backfill` selects `pending & on_disk` — an empty pending
    set scans zero sessions, which would silently disable the error-recovery
    detector on the snapshot the same way a tail offset disables recurrence.
    `processed_sessions` mirrors it, matching fresh-seed semantics.
    """
    if sids is None:
        sroot = os.path.join(kdir, "logs", "sessions")
        try:
            sids = [d for d in os.listdir(sroot)
                    if os.path.isdir(os.path.join(sroot, d))]
        except OSError:
            sids = []
    sids = sorted(sids)
    state = {"offsets": {"prompts": 0, "metrics": 0},   # <- the P1 fix
             "processed_sessions": list(sids),
             "backfill_pending": list(sids),
             "tally": {},
             "emitted_keys": []}
    path = os.path.join(kdir, MINER_STATE_REL)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    import json
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f)
        f.write("\n")
    return state


def snapshot_corpus_kb(src_kdir, dst_kdir, entries=None):
    """Copy the miner-visible corpus from `src_kdir` into a fresh snapshot KB at
    `dst_kdir`, then pre-seed the miner state at offset 0.

    Copies ONLY what the miner actually reads: `logs/prompts.log` (recurrence),
    `logs/metrics.jsonl` (triage context), and each session's `live.jsonl`
    (error-recovery). `main.jsonl` is reproduced as a ZERO-BYTE marker — it is a
    settled/not-settled `os.path.isfile` probe only (`_transcript_session_settled`)
    and its content is never read, so copying the real one would add hundreds of
    MB of transcript for no read-surface fidelity.

    The snapshot is WRITE-ISOLATED: mining it can never touch `src_kdir` (D4 /
    R-LOC-03). Returns a summary dict of what was seeded.
    """
    build_synthetic_kb(dst_kdir, entries)
    slogs, dlogs = os.path.join(src_kdir, "logs"), os.path.join(dst_kdir, "logs")
    os.makedirs(os.path.join(dlogs, "sessions"), exist_ok=True)
    copied = {}
    for name in ("prompts.log", "metrics.jsonl"):
        src = os.path.join(slogs, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dlogs, name))
            copied[name] = os.path.getsize(src)
        else:
            copied[name] = 0
    sids, settled = [], 0
    ssess = os.path.join(slogs, "sessions")
    try:
        cand = sorted(d for d in os.listdir(ssess)
                      if os.path.isdir(os.path.join(ssess, d)))
    except OSError:
        cand = []
    for sid in cand:
        s_live = os.path.join(ssess, sid, "live.jsonl")
        if not os.path.isfile(s_live):
            continue                     # nothing the miner can read
        ddir = os.path.join(dlogs, "sessions", sid)
        os.makedirs(ddir, exist_ok=True)
        shutil.copy2(s_live, os.path.join(ddir, "live.jsonl"))
        if os.path.isfile(os.path.join(ssess, sid, "main.jsonl")):
            open(os.path.join(ddir, "main.jsonl"), "w").close()   # settled marker
            settled += 1
        sids.append(sid)
    seed_miner_state_at_zero(dst_kdir, sids)
    return {"prompts_bytes": copied.get("prompts.log", 0),
            "metrics_bytes": copied.get("metrics.jsonl", 0),
            "sessions": len(sids), "settled_sessions": settled,
            "sids": sids}


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
