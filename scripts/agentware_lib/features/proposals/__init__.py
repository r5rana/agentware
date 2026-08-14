"""agentware_lib.features.proposals — the skill proposal/promotion lifecycle (layer: features).

AGGREGATOR ONLY. This package holds NO logic: it re-imports the public names of
its three domain submodules so every pre-split call site — which reaches the
module under an alias (`from agentware_lib.features import proposals`) and reads
`proposals.cmd_skill_propose`, and the `_registration.py` row that resolves
`register_skill` off THIS package — keeps working byte-identically after the
split, and so does every existing monkeypatch target (`proposals.config.<name>`,
`proposals._fleet.<name>`, `proposals._skills.<name>`). The submodules:

  * `proposeauthor`    — the AUTHORING half: the slug grammar (`_SLUG_RE` /
                         `_valid_proposal_slug`), the dedup primitives
                         (`_slug_token_set` / `near_duplicate_proposal`), the
                         proposed-SKILL.md renderer and `cmd_skill_propose`.
  * `proposalreport`   — the READ-ONLY half: `_proposal_refs`, the single
                         deterministic row builder `_proposal_rows`, the
                         machine-local ripeness nudge report
                         (`RIPE_PROPOSALS_REPORT_REL` /
                         `write_ripe_proposals_report`) and `cmd_skill_proposals`.
  * `proposalpromote`  — the human-gated PROMOTION half (`_promote_frontmatter` /
                         `cmd_skill_promote`) PLUS the `skill` verb registrar
                         `register_skill`, which wires all nine sub-verbs.

Owns: nothing of its own — only the re-export surface that keeps the pre-split
import path and every monkeypatch target resolving unchanged. It deliberately
carries NO `_EXPORTS`: the submodules carry theirs and are the entries the
entrypoint facade iterates, so restating them here would give one symbol two
owners.
Does NOT own: any behaviour — see the submodules above; the read-only
proposal-ledger + integrity reads (proposal_dir / proposal_ledger_path /
read_proposal_ledger / proposal_count / existing_proposal_slugs) ->
agentware_lib.knowledge.proposals (reached via the `_kp` alias); the skill
CATALOG management + static vetter (read_skill_frontmatter / vet_skill_dir) ->
agentware_lib.features.skills (reached via the `_skills` sibling alias); the
ripeness THRESHOLD resolver -> agentware_lib.core.config.
Layer deps: features.proposals -> core (config / dates / frontmatter / routing) +
knowledge (index; knowledge.proposals via `_kp`) + ops (fleet via `_fleet`;
freshness `_CONFLICT_JACCARD`) + retrieval (corpus via `_corpus`) + features
SIBLING (skills via `_skills`; acyclic — skills does not import proposals) — all
of them owned by the submodules; the aliases below are bound here only so the
pre-split namespace (and its monkeypatch targets) still resolves.
"""

# Namespace parity with the pre-split flat module: tests and callers reach these
# through THIS package (`mock.patch.object(proposals.config, ...)`,
# `proposals._fleet`, `proposals._skills`, `proposals._CONFLICT_JACCARD`), so the
# aliases must keep resolving here even though every handler body now lives in a
# submodule. Cross-module FUNCTIONS are always reached through these module
# aliases, never by-name, so monkeypatching the OWNER module still propagates.
import agentware_lib.core.config as config                  # noqa: F401
import agentware_lib.core.dates as dates                    # noqa: F401
import agentware_lib.core.frontmatter as frontmatter        # noqa: F401
import agentware_lib.core.routing as routing                # noqa: F401
import agentware_lib.knowledge.index as index               # noqa: F401
import agentware_lib.knowledge.proposals as _kp             # noqa: F401
import agentware_lib.features.skills as _skills             # noqa: F401
import agentware_lib.retrieval.corpus as _corpus            # noqa: F401
import agentware_lib.ops.fleet as _fleet                    # noqa: F401
from agentware_lib.ops.freshness import _CONFLICT_JACCARD   # noqa: F401

from .proposeauthor import (  # noqa: F401
    _SLUG_RE,
    _valid_proposal_slug,
    _slug_token_set,
    near_duplicate_proposal,
    _render_proposal_skill_md,
    cmd_skill_propose,
)
from .proposalreport import (  # noqa: F401
    _proposal_refs,
    _proposal_rows,
    RIPE_PROPOSALS_REPORT_REL,
    write_ripe_proposals_report,
    cmd_skill_proposals,
)
from .proposalpromote import (  # noqa: F401
    _promote_frontmatter,
    cmd_skill_promote,
    register_skill,
)
