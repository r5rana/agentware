"""agentware_lib._registration — the FROZEN parser registration tuple.

Owns: _PARSER_REGISTRATION — the frozen (verb, module, attr) map in EXACT --help order.
Does NOT own: the registrar functions or their argparse bodies — each lives in its owning agentware_lib module; the assembler is agentware_lib.cli.assemble_parser.
Layer deps: cli -> (nothing; imports nothing at all — a pure data leaf).
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

``_PARSER_REGISTRATION`` lists every TOP-LEVEL agentware verb in EXACT current
display order. It is the single source of truth the assembler
(``agentware_lib.cli.assemble_parser``) iterates to build the argparse tree, and
the contract the parser-contract test asserts against.

Each entry is a 3-tuple ``(verb, module, attr)``:

  * ``verb``   — the command name as it appears on the top-level ``--help``.
                 Display ORDER is load-bearing: it IS the top-level ``--help``
                 order, and it is the one thing a clean union-merge of
                 concurrent lanes can still get wrong — which is exactly why
                 this file is marked ``merge=union`` in ``.gitattributes``.
  * ``module`` — the dotted agentware_lib module that OWNS the verb's registrar,
                 or ``None`` while the registrar is still entrypoint-owned
                 (pre-extraction). Each extraction wave repoints its verbs from
                 ``None`` to their new owning module — with NO ``--help`` change.
  * ``attr``   — the registrar function name, ``register_<verb>``, resolved
                 either against the entrypoint's globals (module is None) or the
                 owning module's namespace (module is a dotted name).

Registrar signature contract: ``register_<verb>(sub, fmt_parent)`` where ``sub``
is the top-level subparsers action and ``fmt_parent`` is the shared ``--format``
parent parser. Registrars that do not use ``--format`` still accept
``fmt_parent`` for a uniform signature.

⚠️ Do NOT reorder entries: the order IS the top-level ``--help`` order, so any
reorder is a behavior change. Append a new verb at the position it should appear
in ``--help``. Repoint (never reorder) an entry's ``module`` when its wave moves
the registrar into agentware_lib.
"""

_PARSER_REGISTRATION = (
    ("init", "agentware_lib.features.setupcmd", "register_init"),
    ("config", "agentware_lib.features.configcmd", "register_config"),
    ("profile", "agentware_lib.features.setupcmd", "register_profile"),
    ("attach", "agentware_lib.knowledge.attach", "register_attach"),
    ("index", "agentware_lib.features.indexcmd", "register_index"),
    ("skill", "agentware_lib.features.proposals", "register_skill"),
    ("update", "agentware_lib.features.updatecmd", "register_update"),
    ("query", "agentware_lib.knowledge.graph", "register_query"),
    ("learn", "agentware_lib.features.curationcmd", "register_learn"),
    ("decide", "agentware_lib.features.curationcmd", "register_decide"),
    ("ingest", "agentware_lib.knowledge.curation", "register_ingest"),
    ("features", "agentware_lib.knowledge.curation", "register_features"),
    ("worklog", "agentware_lib.knowledge.worklog", "register_worklog"),
    ("work", "agentware_lib.ops.planauthor", "register_work"),
    ("audit", "agentware_lib.ops.audit", "register_audit"),
    ("recall", "agentware_lib.retrieval.recallcmd", "register_recall"),
    ("mcp", "agentware_lib.features.mcp", "register_mcp"),
    ("eval", "agentware_lib.retrieval.evalcmd", "register_eval"),
    ("bench", "agentware_lib.retrieval.bench", "register_bench"),
    ("metrics", "agentware_lib.ops.metricscmd", "register_metrics"),
    ("fleet", "agentware_lib.ops.fleet", "register_fleet"),
    ("fleet-emit", "agentware_lib.ops.fleet", "register_fleet_emit"),
    ("team", "agentware_lib.ops.team", "register_team"),
    ("dashboard", "agentware_lib.ops.metricscmd", "register_dashboard"),
    ("dream", "agentware_lib.features.dream", "register_dream"),
    ("kb-git", "agentware_lib.ops.kbsync", "register_kb_git"),
    ("steering", "agentware_lib.ops.steering", "register_steering"),
    ("org", "agentware_lib.ops.orglint", "register_org"),
    ("templates", "agentware_lib.ops.fleet", "register_templates"),
    ("gate", "agentware_lib.ops.releasegate", "register_gate"),
    ("whereami", "agentware_lib.cli", "register_whereami"),
    ("plan", "agentware_lib.ops.planauthor", "register_plan"),
    ("fanout", "agentware_lib.features.fanoutmq", "register_fanout"),
)

__all__ = ["_PARSER_REGISTRATION"]
