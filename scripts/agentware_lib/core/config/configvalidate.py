"""agentware_lib.core.config.configvalidate — SETTINGS_AW token parsers (layer: core).

Owns: every raw-value VALIDATOR for a SETTINGS_AW setting, in both flavours —
the LENIENT precedence-tier parsers (_parse_autocommit/_parse_retrieval_mode/
_parse_kb_mode/_parse_cli: an unrecognized value returns None so the caller falls
through to the next tier and a typo never silently flips a mode) and the STRICT
writer-side parsers the `config --set-*` setters use to REJECT a bad value before
it reaches config.env (_parse_*_strict, _parse_embedder_backend_strict,
_parse_embed_model_strict, _parse_skill_propose_threshold).
Does NOT own: the key/default/token-set constants it validates against
(agentware_lib.core.config.configkeys); the env->config.env->default precedence
that CALLS these parsers (…config.configresolve); the config.env writers that act
on a strict parse (…config.configwrite); the `config --set-*` command handlers
(agentware_lib.features.configcmd).
Layer deps: core -> core only (imports its configkeys sibling; nothing above).
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

Pure functions: no env reads, no file reads, no writes. Every parser strips
surrounding whitespace and one layer of quotes so a value pasted into config.env
as KEY="value" parses identically to KEY=value.
"""

import re

from .configkeys import (
    _AUTOCOMMIT_OFF_TOKENS,
    _AUTOCOMMIT_ON_TOKENS,
    _CLI_CLAUDE_TOKENS,
    _CLI_CODEX_TOKENS,
    _KB_MODE_POWER_TOKENS,
    _KB_MODE_TEAM_TOKENS,
    _RETRIEVAL_MODE_DETERMINISTIC_TOKENS,
    _RETRIEVAL_MODE_SEMANTIC_TOKENS,
    _SKILL_PROMOTE_OFF_TOKENS,
    _SKILL_PROMOTE_ON_TOKENS,
    CLI_CLAUDE,
    CLI_CODEX,
    KB_MODE_POWER,
    KB_MODE_TEAM,
    RETRIEVAL_MODE_DETERMINISTIC,
    RETRIEVAL_MODE_SEMANTIC,
)


def _parse_autocommit(raw):
    """Map a raw setting to '1'/'0', or None if empty/unset.

    Honors the "anything that isn't an explicit off-token is ON" contract so an
    unrecognized non-empty value never silently disables autocommit.
    """
    if raw is None:
        return None
    v = raw.strip().strip('"').strip("'").lower()
    if not v:
        return None
    return "0" if v in _AUTOCOMMIT_OFF_TOKENS else "1"


def _parse_autocommit_strict(raw):
    """Strict parse for `config --set-autocommit`: only recognized tokens.

    Returns '1'/'0' for known on/off tokens, else None (caller errors out).
    """
    v = (raw or "").strip().strip('"').strip("'").lower()
    if v in _AUTOCOMMIT_OFF_TOKENS:
        return "0"
    if v in _AUTOCOMMIT_ON_TOKENS:
        return "1"
    return None


def _parse_retrieval_mode(raw):
    """Map a raw setting to 'deterministic'/'semantic', or None if empty/unset.

    Unrecognized non-empty values return None (the caller falls through to the
    next precedence tier, ultimately the safe 'deterministic' default) — a typo
    never silently activates the opt-in semantic mode.
    """
    if raw is None:
        return None
    v = raw.strip().strip('"').strip("'").lower()
    if not v:
        return None
    if v in _RETRIEVAL_MODE_SEMANTIC_TOKENS:
        return RETRIEVAL_MODE_SEMANTIC
    if v in _RETRIEVAL_MODE_DETERMINISTIC_TOKENS:
        return RETRIEVAL_MODE_DETERMINISTIC
    return None


def _parse_retrieval_mode_strict(raw):
    """Strict parse for `config --set-mode`/`--set-retrieval`: recognized tokens only.

    Maps the SETTINGS_AW retrieval alias too: `bm25` -> deterministic (Mode A),
    `semantic` -> semantic (Mode B), both already in the token sets above.
    """
    v = (raw or "").strip().strip('"').strip("'").lower()
    if v in _RETRIEVAL_MODE_SEMANTIC_TOKENS:
        return RETRIEVAL_MODE_SEMANTIC
    if v in _RETRIEVAL_MODE_DETERMINISTIC_TOKENS:
        return RETRIEVAL_MODE_DETERMINISTIC
    return None


def _parse_embedder_backend_strict(raw):
    """Strict parse for `config --set-embedder`: a non-empty backend spec.

    SETTINGS_AW (260627-semantic-embedding-settings) writer-side validator. The
    spec is either a dotted module name (e.g. `agentware_embedder_fastembed`,
    importable with scripts/ on sys.path) or a filesystem path to a .py backend.
    We only enforce non-emptiness + single-token-ness here (no whitespace); the
    actual contract is enforced lazily by `load_embedder` (any bad spec -> None ->
    graceful Mode-A fallback, never a crash). Returns the cleaned spec or None.
    """
    v = (raw or "").strip().strip('"').strip("'")
    if not v or any(c.isspace() for c in v):
        return None
    return v


def _parse_embed_model_strict(raw):
    """Strict parse for `config --set-embed-model`: a non-empty model id.

    SETTINGS_AW writer-side validator. The id is passed verbatim to the backend
    factory (e.g. the pinned HuggingFace id `BAAI/bge-small-en-v1.5`). We only
    enforce non-emptiness + no internal whitespace; the backend interprets it.
    Returns the cleaned id or None.
    """
    v = (raw or "").strip().strip('"').strip("'")
    if not v or any(c.isspace() for c in v):
        return None
    return v


def _parse_kb_mode(raw):
    """Map a raw setting to 'power'/'team', or None if empty/unrecognized.

    An unrecognized non-empty value returns None so the caller falls through to
    the next precedence tier (ultimately the safe 'power' default) — a typo never
    silently flips a solo operator into team semantics.
    """
    if raw is None:
        return None
    v = raw.strip().strip('"').strip("'").lower()
    if not v:
        return None
    if v in _KB_MODE_TEAM_TOKENS:
        return KB_MODE_TEAM
    if v in _KB_MODE_POWER_TOKENS:
        return KB_MODE_POWER
    return None


def _parse_kb_mode_strict(raw):
    """Strict parse for `config --set-kb-mode`: only recognized tokens, else None."""
    v = (raw or "").strip().strip('"').strip("'").lower()
    if v in _KB_MODE_TEAM_TOKENS:
        return KB_MODE_TEAM
    if v in _KB_MODE_POWER_TOKENS:
        return KB_MODE_POWER
    return None


def _parse_cli(raw):
    """Map a raw runtime-CLI setting to 'claude'/'codex', or None if empty/unknown.

    Lenient (precedence-tier) parse: an unrecognized non-empty value returns None
    so the caller falls through to the next tier (ultimately the safe 'claude'
    default) — a typo never silently flips the operator onto an unsupported runtime.
    """
    if raw is None:
        return None
    v = raw.strip().strip('"').strip("'").lower()
    if not v:
        return None
    if v in _CLI_CODEX_TOKENS:
        return CLI_CODEX
    if v in _CLI_CLAUDE_TOKENS:
        return CLI_CLAUDE
    return None


def _parse_cli_strict(raw):
    """Strict parse for `config --set-cli`: only recognized tokens, else None."""
    v = (raw or "").strip().strip('"').strip("'").lower()
    if v in _CLI_CODEX_TOKENS:
        return CLI_CODEX
    if v in _CLI_CLAUDE_TOKENS:
        return CLI_CLAUDE
    return None


def _parse_skill_propose_threshold(raw):
    """Strict parse for `config --set-skill-propose-threshold` (positive int).

    Returns the int on success, or None on an empty/non-integer/non-positive
    value so the setter can reject it (no config corruption). A typo is never
    silently persisted.
    """
    if raw is None or str(raw).strip() == "":
        return None
    try:
        val = int(str(raw).strip())
    except (ValueError, TypeError):
        return None
    return val if val > 0 else None



# --- Skill-promotion settings (feature 260722-auto-skill-promotion) -----------
def _parse_skill_promote(raw):
    """Lenient parse of the skill-promotion mode -> 'propose'/'off', or None if
    empty/unrecognized (the caller falls through to the safe DEFAULT — a typo
    never silently disables promotion)."""
    if raw is None:
        return None
    v = str(raw).strip().strip('"').strip("'").lower()
    if not v:
        return None
    if v in _SKILL_PROMOTE_OFF_TOKENS:
        return "off"
    if v in _SKILL_PROMOTE_ON_TOKENS:
        return "propose"
    return None


def _parse_skill_promote_strict(raw):
    """Strict parse for `config --set-skill-promote`: only recognized tokens."""
    v = (raw or "").strip().strip('"').strip("'").lower()
    if v in _SKILL_PROMOTE_OFF_TOKENS:
        return "off"
    if v in _SKILL_PROMOTE_ON_TOKENS:
        return "propose"
    return None


def _parse_skill_promote_cap(raw):
    """Parse the per-run emission cap -> non-negative int, or None for an
    empty/invalid value. 0 disables emission (detect/report only)."""
    if raw is None:
        return None
    v = str(raw).strip().strip('"').strip("'").lower()
    if not v or not re.match(r"^\d+$", v):
        return None
    return int(v)


def _parse_unit_threshold(raw):
    """Parse a float threshold in [0.0, 1.0], or None (empty/invalid/out-of-range)."""
    if raw is None:
        return None
    v = str(raw).strip().strip('"').strip("'")
    if not v:
        return None
    try:
        f = float(v)
    except ValueError:
        return None
    # Reject out-of-range AND non-finite: `float("nan")` parses but every NaN
    # comparison is False, so `f < 0.0 or f > 1.0` would let NaN through as a
    # live threshold (adversarial-review). A chained `0.0 <= f <= 1.0` is False
    # for NaN and inf alike, so `not (...)` rejects both.
    if not (0.0 <= f <= 1.0):
        return None
    return f

# Explicit facade export list (public AND underscore-private) — the entrypoint
# re-export loop binds exactly these onto the CLI facade so SourceFileLoader-based
# tests still reach them as CLI.<name>. See the M3/_EXPORTS convention in the plan.
_EXPORTS = [
    "_parse_autocommit",
    "_parse_autocommit_strict",
    "_parse_retrieval_mode",
    "_parse_retrieval_mode_strict",
    "_parse_embedder_backend_strict",
    "_parse_embed_model_strict",
    "_parse_kb_mode",
    "_parse_kb_mode_strict",
    "_parse_cli",
    "_parse_cli_strict",
    "_parse_skill_propose_threshold",
    "_parse_skill_promote",
    "_parse_skill_promote_strict",
    "_parse_skill_promote_cap",
    "_parse_unit_threshold",
]
