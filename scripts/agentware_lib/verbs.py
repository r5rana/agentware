"""agentware_lib.verbs — DISCOVER the CLI verb surface from per-module declarations.

Owns: the verb-declaration contract (`__verbs__`) and its discovery — walking the package in sorted module order, validating every declaration, refusing a duplicate verb or a duplicate order key, and resolving the ordered `(verb, module, attr)` registration the assembler iterates.
Does NOT own: the registrars themselves or their argparse bodies (each lives beside its handler in the owning module, which declares it in its own `__verbs__`); the argparse tree (agentware_lib.cli.assemble_parser); the ROUTING allowlist (agentware_lib.strangler).
Layer deps: cli -> core, knowledge, retrieval, ops, features (a discovery leaf: it imports the package's modules dynamically and nothing imports it but the assembler).
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

WHY DISCOVERY REPLACED THE FROZEN TUPLE (plan task 70)
------------------------------------------------------
Until this module existed, every verb was listed in ONE file — the frozen
``_PARSER_REGISTRATION`` tuple in ``agentware_lib/_registration.py``. Adding a
verb therefore touched at least two files (the owning module and the registry),
and the registry was the one file every concurrent fanout lane edited, which is
why it had to be marked ``merge=union`` in ``.gitattributes`` to stop lanes
conflicting on it. A single-file registry is a monolith in miniature: it grows
without bound, it centralises a decision that belongs beside the code, and it
makes "where do I add this?" a question with two answers.

Now each owning module DECLARES its own verbs and nothing else knows the list::

    __verbs__ = (("init", 100, "register_init"),)

and this module finds them by walking the package. Adding a verb touches exactly
one file. Removing one deletes one declaration. Two lanes adding two verbs edit
two different files and merge cleanly with no merge driver at all.

WHY ``__verbs__`` AND NOT AN UPPER-SNAKE CONSTANT
-------------------------------------------------
A module-level ``VERBS = (...)`` would be an UPPER_SNAKE name, and the constant
differ (``tests/_constant_differ.py``) records every one of those as a tracked
module constant. Forty modules declaring a same-named constant with forty
different literals is precisely the ``multi_literal`` shape that gate exists to
report — the declaration would arrive as forty findings on the day it landed.
``__verbs__`` is a dunder, which that normalisation excludes by rule (not by
special case), and it is the language's own idiom for module metadata, next to
``__all__``. The name is data ABOUT the module, so it is spelt like it.

THE ORDER KEY IS SPARSE AND EXPLICIT
------------------------------------
The second field is the DISPLAY ORDER: it is the top-level ``--help`` order, and
that order is user-visible behaviour. Discovery sorts by it, so a module's
position in the package tree — which is an implementation detail — can never
move a verb on the help screen.

Keys are issued in steps of 100 (``init`` is 100, ``graph`` is 4400). The gaps
are the point: inserting a verb between two existing ones picks a number in the
gap and touches NO other file. A dense key (1, 2, 3...) would force a
renumbering of every later verb — reintroducing exactly the single-shared-edit
problem this module removes, only spread across forty files instead of one.

Two modules claiming the same order key is a genuine ambiguity — the resulting
``--help`` would depend on module names, which nobody intends — so it is
REFUSED, loudly, naming both modules. So is a duplicate verb.

WHY A PACKAGE WALK AND NOT ENTRY POINTS
---------------------------------------
The obvious Python answer is ``importlib.metadata.entry_points``. It is wrong
here: agentware is CLONE-AND-GO. It is run straight out of a git checkout with
no install step, no virtualenv and no ``pip install -e .``, so there is no
distribution metadata to read and the verb list would come back EMPTY — the CLI
would present zero commands with no error. The package walk works on a bare
clone, which is the only configuration this project supports.

WHY THE WALK IS AFFORDABLE
--------------------------
Discovery imports every module in the package. That sounds expensive and is not:
assembling the parser already imports the registrar of all 44 verbs, and those
registrars pull their handlers, which pull the rest — 213 of the package's 219
modules were already loaded before this module existed. The walk adds the
remaining handful, measured at ~1.6 ms.

The two cheaper-looking alternatives were measured and are both WORSE: parsing
every module's AST costs ~118 ms (more than the entire CLI start-up), and a
text pre-filter that greps for the declaration before importing costs ~11 ms
and buys a silent-miss failure mode — a declaration the pre-filter's pattern
does not match would simply vanish from ``--help`` with nothing red.

NO CACHE
--------
``discover()`` recomputes on every call. A module-level cache would be mutable
state whose lifetime is the process, and this package is exec'd into fresh
namespaces by the test harness, the dashboard sidecar and the fresh-clone probe
— the failure mode written up in
``learn-moving-mutable-module-state-changes-its-lifetime``. Recomputation is
milliseconds and cannot go stale.
"""

import importlib
import pkgutil
import re

#: The module attribute each owning module declares. See the docstring for why
#: it is a dunder rather than an UPPER_SNAKE constant.
DECLARATION = "__verbs__"

#: The step between issued order keys. Not enforced (a verb inserted into a gap
#: is the whole point); published so the next author picks a key the same way.
ORDER_STEP = 100

VERB_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
ATTR_RE = re.compile(r"^register_[a-z][a-z0-9_]*$")


class VerbDeclarationError(RuntimeError):
    """A malformed, duplicate or unresolvable ``__verbs__`` declaration.

    Raised at parser-assembly time, which is CLI start-up: a bad declaration
    stops the whole CLI with a message naming the module rather than dropping
    one verb quietly off ``--help``.
    """


def _fail(message):
    raise VerbDeclarationError(message)


def module_names(package="agentware_lib"):
    """Every importable module in ``package``, in SORTED dotted-name order.

    Sorted explicitly: ``pkgutil.walk_packages`` yields in directory order,
    which is filesystem-dependent, and discovery must produce the same answer on
    every machine before the order key is even consulted.
    """
    pkg = importlib.import_module(package)

    def _raise(name):
        _fail("verb discovery could not import %r while walking %s"
              % (name, package))

    names = [info.name for info
             in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + ".",
                                      onerror=_raise)]
    return tuple(sorted(names))


def declarations(package="agentware_lib"):
    """``((module_name, declaration), ...)`` for every module that declares one.

    Walk order is ``module_names``'; a module with no ``__verbs__`` contributes
    nothing, which is the overwhelming majority of the package.
    """
    out = []
    for name in module_names(package):
        decl = getattr(importlib.import_module(name), DECLARATION, None)
        if decl is not None:
            out.append((name, decl))
    return tuple(out)


def _validate_row(module, row):
    """Normalise and check ONE declared row; return ``(verb, order, attr)``."""
    if not isinstance(row, (tuple, list)) or len(row) != 3:
        _fail("%s.%s: each row must be a (verb, order, attr) triple, got %r"
              % (module, DECLARATION, row))
    verb, order, attr = row
    if not isinstance(verb, str) or not VERB_RE.match(verb):
        _fail("%s.%s: %r is not a valid verb name (lowercase, dash-separated)"
              % (module, DECLARATION, verb))
    if not isinstance(order, int) or isinstance(order, bool) or order <= 0:
        _fail("%s.%s: verb %r has order %r; expected a positive int"
              % (module, DECLARATION, verb, order))
    if not isinstance(attr, str) or not ATTR_RE.match(attr):
        _fail("%s.%s: verb %r names registrar %r; expected register_<verb>"
              % (module, DECLARATION, verb, attr))
    return (verb, order, attr)


def _rows_of(module, decl):
    """Every validated row of one module's declaration."""
    if not isinstance(decl, (tuple, list)):
        _fail("%s.%s must be a tuple of (verb, order, attr) triples, got %r"
              % (module, DECLARATION, type(decl).__name__))
    return [_validate_row(module, row) for row in decl]


def _claim(seen, key, module, what):
    """Record ``key`` as claimed by ``module``, refusing a second claimant."""
    if key in seen:
        _fail("two modules declare the same %s %r: %s and %s. %s must be "
              "unique across the package -- pick another." % (
                  what, key, seen[key], module,
                  "A verb" if what == "verb" else "An order key"))
    seen[key] = module


def collect(declared):
    """Validate ``((module, declaration), ...)`` into ordered discovery rows.

    Pure: no imports, no filesystem. Returns ``((verb, order, module, attr),
    ...)`` sorted by order key. Refuses a duplicate verb and a duplicate order
    key, naming BOTH claimant modules in the message — the two failures a
    per-module registry can have that a single frozen list could not.
    """
    rows, by_verb, by_order = [], {}, {}
    for module, decl in declared:
        for verb, order, attr in _rows_of(module, decl):
            _claim(by_verb, verb, module, "verb")
            _claim(by_order, order, module, "order key")
            rows.append((verb, order, module, attr))
    return tuple(sorted(rows, key=lambda r: r[1]))


def discover(package="agentware_lib"):
    """``((verb, order, module, attr), ...)`` for the whole package, in order."""
    return collect(declarations(package))


def registration(package="agentware_lib"):
    """``((verb, module, attr), ...)`` in display order — the assembler's input.

    The shape the retired ``_PARSER_REGISTRATION`` tuple used to hold, now
    derived from the modules that own the verbs instead of maintained by hand.
    """
    return tuple((verb, module, attr) for verb, _order, module, attr in discover(package))


def resolve(verb, module, attr):
    """The callable registrar for one discovered row.

    A row naming a renamed or deleted registrar fails HERE, at assembly, with
    the verb and the dotted target in the message — not as a bare
    ``AttributeError`` from inside argparse.
    """
    registrar = getattr(importlib.import_module(module), attr, None)
    if not callable(registrar):
        _fail("verb %r declares registrar %s.%s, which is missing or not "
              "callable" % (verb, module, attr))
    return registrar


__all__ = ["DECLARATION", "ORDER_STEP", "VerbDeclarationError", "collect",
           "declarations", "discover", "module_names", "registration",
           "resolve"]
