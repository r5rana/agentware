"""agentware_lib.knowledge.curation — KB write/capture surfaces (layer: knowledge).

AGGREGATOR ONLY. This package holds NO logic: it re-imports the public names of
its three domain submodules so every pre-split call site — which reaches the
module under an alias (`import agentware_lib.knowledge.curation as curation`) and
reads `curation._slugify` / `curation._render_features` — keeps resolving
unchanged, and so every existing monkeypatch target (`<consumer>.curation.<name>`)
keeps working. It ALSO re-imports the two parser registrars, because
`_PARSER_REGISTRATION` keeps naming this PACKAGE (`("ingest",
"agentware_lib.knowledge.curation", "register_ingest")`) and the assembler
resolves them with importlib + getattr on the module object. The submodules are:

  * `curationrender`   — the entry RENDERERS (_render_learning with its
                         `back_label` seam, _render_decision_body) + the two pure
                         string helpers (_topic_to_title, _slugify).
  * `curationingest`   — the whole ingestion connector pipeline: INGEST_SOURCES,
                         the two LOCAL adapters + markdown scanners, the candidate
                         model/validator, the content-fingerprint dedup, the
                         single `source: imported` registration path, the `work/`
                         staging writer, the Rule-7 metric append, cmd_ingest and
                         register_ingest.
  * `curationfeatures` — the FEATURES.md renderer (_render_features, also called
                         by `index rebuild`) + cmd_features + register_features.

The union of the three submodules' `_EXPORTS` is exactly the pre-split module's
`_EXPORTS` — no name added, none dropped (20 names).

Owns: nothing of its own — only the re-export surface that keeps the pre-split
import path (`import agentware_lib.knowledge.curation as curation`) and every
monkeypatch target (`<consumer>.curation.<name>`) resolving unchanged.
Does NOT own: any behaviour — see the three submodules above; the index
CRUD/validate/rebuild/graph core (-> agentware_lib.knowledge.index); the
learn/decide HANDLERS cmd_learn/cmd_decide (-> agentware_lib.features.curationcmd,
which reaches guard_shared_write -> ops.fleet); the worklog-scan promotion cluster
(-> agentware_lib.knowledge.worklog).
Layer deps: knowledge -> core (frontmatter, config, dates) + knowledge (index),
via its own submodules only. Never imports ops/retrieval/features or the
entrypoint (no upward/forward edges).
"""

from agentware_lib.knowledge.curation.curationrender import (
    _render_decision_body,
    _render_learning,
    _slugify,
    _topic_to_title,
)
from agentware_lib.knowledge.curation.curationingest import (
    INGEST_SOURCES,
    _ingest_candidate,
    _ingest_docs_adapter,
    _ingest_emit_metric,
    _ingest_existing_dedup,
    _ingest_extract_body,
    _ingest_fingerprint,
    _ingest_first_heading,
    _ingest_first_paragraph,
    _ingest_github_issues_adapter,
    _ingest_register,
    _ingest_validate,
    _ingest_write_staging,
    cmd_ingest,
    register_ingest,
)
from agentware_lib.knowledge.curation.curationfeatures import (
    _render_features,
    cmd_features,
    register_features,
)
