"""agentware_lib.core.routing — per-phase + provider setting RESOLVERS (layer: core).

Owns: the per-phase (CLI/MODEL/LOCAL) routing resolvers (resolve_phase_cli/model/
local + _phase_key) and their lenient/strict provider parsers (_parse_local(_strict),
_parse_model(_strict)); the dream on/off + schedule + max-runtime resolvers
(resolve_dream / resolve_dream_schedule / resolve_dream_max_runtime + their _parse_*
helpers); the per-user provenance handle normalizer/resolver (_normalize_user_handle,
resolve_user_handle); and the LOCAL embedder backend/model resolvers
(resolve_embedder_backend, resolve_embed_model).
Does NOT own: the setting KEY names/defaults/token-sets these resolvers READ, the
config.env writers, and the knowledge-dir/cli resolvers (agentware_lib.core.config);
the embedder LOADER + instance cache that CONSUMES resolve_embedder_backend/
resolve_embed_model (load_embedder — entrypoint today, agentware_lib.retrieval in
wave 3); and the init/config/profile HANDLERS (agentware_lib.features.configcmd,
wave 6).
Layer deps: core -> core only (imports agentware_lib.core.config; nothing above).
Fan-in: 20 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

These are the precedence RESOLVERS (env -> phase config.env -> global -> default)
that turn config.py's raw setting keys into effective runtime routing decisions.
They are kept OUT of config.py so that module stays a near-zero-outbound-edge
settings ROOT; routing imports config downward like any other reader, so the core
layer graph stays acyclic (config <- routing, never the reverse).
"""

import os
import re

import agentware_lib.core.config as config


# --- per-phase routing (260628-hybrid-local-model) ---------------------------
def _phase_key(phase, axis):
    """Env/config key for a (phase, axis) pair, e.g. ('main','CLI') ->
    'AGENTWARE_MAIN_CLI'. `phase` in PHASES; `axis` in {CLI,MODEL,LOCAL}."""
    return "AGENTWARE_%s_%s" % (phase.upper(), axis)


def _parse_local(raw):
    """Lenient parse for the LOCAL provider knob -> 'lmstudio'|'ollama'|None.

    An unrecognized non-empty value returns None so the caller falls through to
    the next precedence tier (ultimately unset => cloud route) — a typo never
    silently points Codex at a bogus local server.
    """
    if raw is None:
        return None
    v = raw.strip().strip('"').strip("'").lower()
    if not v:
        return None
    if v in config._LOCAL_TOKENS:
        return v
    return None


def _parse_local_strict(raw):
    """Strict parse for `config --set-*-local`: only lmstudio|ollama, else None."""
    v = (raw or "").strip().strip('"').strip("'").lower()
    if v in config._LOCAL_TOKENS:
        return v
    return None


def _parse_model(raw):
    """Lenient parse for a runtime model id: a non-empty single token, else None.

    Models are free-form vendor ids ('opus', 'openai/gpt-oss-20b'); we only reject
    empty/whitespace-containing values (mirrors _parse_embed_model_strict) so a
    stray shell glob never becomes a model id. Used in the precedence tiers.
    """
    if raw is None:
        return None
    v = raw.strip().strip('"').strip("'")
    if not v or any(c.isspace() for c in v):
        return None
    return v


def _parse_model_strict(raw):
    """Strict parse for `config --set-*-model`: a non-empty, whitespace-free id."""
    v = (raw or "").strip().strip('"').strip("'")
    if not v or any(c.isspace() for c in v):
        return None
    return v


def resolve_phase_cli(phase):
    """Resolve a phase's runtime CLI ('claude'|'codex').

    Precedence: AGENTWARE_<PHASE>_CLI env -> phase config.env -> global
    resolve_cli() (which itself is env AGENTWARE_CLI -> config -> default 'claude').
    Backward-compatible: no per-phase key => exactly the global CLI.
    """
    key = _phase_key(phase, "CLI")
    env = config._parse_cli(os.environ.get(key))
    if env is not None:
        return env
    cfg = config._parse_cli(config._read_config_key(key))
    if cfg is not None:
        return cfg
    return config.resolve_cli()


def resolve_phase_model(phase):
    """Resolve a phase's model id ('' if none — spawned agent picks its own).

    Precedence: AGENTWARE_<PHASE>_MODEL env -> phase config.env -> global
    AGENTWARE_MODEL env -> global config.env -> '' (unset). Backward-compatible:
    no per-phase key => exactly the global AGENTWARE_MODEL (default unset).
    """
    key = _phase_key(phase, "MODEL")
    for raw in (os.environ.get(key), config._read_config_key(key),
                os.environ.get(config.MODEL_KEY), config._read_config_key(config.MODEL_KEY)):
        m = _parse_model(raw)
        if m is not None:
            return m
    return ""


def resolve_phase_local(phase):
    """Resolve a phase's LOCAL serving provider ('lmstudio'|'ollama'|'').

    Precedence: AGENTWARE_<PHASE>_LOCAL env -> phase config.env -> '' (unset =>
    cloud route). The LOCAL axis is inherently per-phase and has NO global
    fallback. Default unset keeps every phase cloud-routed (today's behavior).
    """
    key = _phase_key(phase, "LOCAL")
    env = _parse_local(os.environ.get(key))
    if env is not None:
        return env
    cfg = _parse_local(config._read_config_key(key))
    if cfg is not None:
        return cfg
    return ""


def _parse_dream(raw):
    """Map a raw dream setting to '1'/'0', or None if empty/unrecognized.

    Lenient (precedence-tier) parse: an unrecognized non-empty value returns None
    so the caller falls through to the next tier (ultimately the safe OFF
    default) — a typo never silently ENABLES the unattended cycle.
    """
    if raw is None:
        return None
    v = raw.strip().strip('"').strip("'").lower()
    if not v:
        return None
    if v in config._DREAM_OFF_TOKENS:
        return "0"
    if v in config._DREAM_ON_TOKENS:
        return "1"
    return None


def _parse_dream_strict(raw):
    """Strict parse for `config --set-dream`: only recognized on/off tokens."""
    v = (raw or "").strip().strip('"').strip("'").lower()
    if v in config._DREAM_OFF_TOKENS:
        return "0"
    if v in config._DREAM_ON_TOKENS:
        return "1"
    return None


def resolve_dream():
    """Resolve the effective dream setting as '1' (on) or '0' (off).

    Precedence: AGENTWARE_DREAM env -> config.env -> default OFF. Read per-run
    (no daemon); the scheduler/installer + the `dream` command consult this.
    """
    env = _parse_dream(os.environ.get(config.DREAM_KEY))
    if env is not None:
        return env
    cfg = _parse_dream(config._read_config_key(config.DREAM_KEY))
    if cfg is not None:
        return cfg
    return config.DREAM_DEFAULT


def _parse_dream_schedule_strict(raw):
    """Strict parse/normalize for `config --set-dream-schedule`.

    Accepts either a nightly 24h "HH:MM" token (normalized to zero-padded
    "HH:MM") or a 5-field cron expression (each field limited to digits and the
    cron metacharacters `* / , -`). Returns the normalized token, or None for an
    empty/invalid value (the caller errors out). Deterministic + stdlib-only.
    """
    v = (raw or "").strip().strip('"').strip("'")
    if not v:
        return None
    m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", v)
    if m:
        return "%02d:%02d" % (int(m.group(1)), int(m.group(2)))
    fields = v.split()
    if len(fields) == 5 and all(re.match(r"^[\d*/,\-]+$", f) for f in fields):
        return " ".join(fields)
    return None


def resolve_dream_schedule():
    """Resolve the configured dream schedule token, or None if unset.

    Precedence: AGENTWARE_DREAM_SCHEDULE env -> config.env -> None (unset). The
    value is re-validated through the strict parser so a hand-edited config typo
    degrades to 'unset' rather than propagating a malformed token to the scheduler.
    """
    env = os.environ.get(config.DREAM_SCHEDULE_KEY)
    if env is not None and env.strip():
        norm = _parse_dream_schedule_strict(env)
        if norm is not None:
            return norm
    cfg = config._read_config_key(config.DREAM_SCHEDULE_KEY)
    if cfg is not None and cfg.strip():
        norm = _parse_dream_schedule_strict(cfg)
        if norm is not None:
            return norm
    return None


def _parse_dream_max_runtime(raw):
    """Strict parse/normalize for the dream max-runtime cap -> int seconds.

    Accepts a bare integer (seconds) or an integer with a single s/m/h suffix
    (e.g. "1800", "30m", "1h"), or an explicit off token ("0"/"off"/"none"/
    "disable"/"disabled"/"unlimited") -> 0 (disabled, no cap). Returns the
    non-negative int of seconds, or None for an empty/invalid value (the
    resolver falls through to the default; the `config` setter errors out).
    Deterministic + stdlib-only.
    """
    if raw is None:
        return None
    v = str(raw).strip().strip('"').strip("'").lower()
    if not v:
        return None
    if v in config._DREAM_MAX_RUNTIME_OFF_TOKENS:
        return 0
    m = re.match(r"^(\d+)\s*([smh]?)$", v)
    if not m:
        return None
    return int(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[m.group(2)]


def resolve_dream_max_runtime():
    """Resolve the dream wall-clock cap in seconds (0 = disabled/no cap).

    Precedence: AGENTWARE_DREAM_MAX_RUNTIME env -> config.env -> generous
    default (DREAM_MAX_RUNTIME_DEFAULT). Read per-run (no daemon); the dream
    cycle consults this BETWEEN steps. A hand-edited config typo degrades to the
    default rather than silently disabling the guard.
    """
    env = _parse_dream_max_runtime(os.environ.get(config.DREAM_MAX_RUNTIME_KEY))
    if env is not None:
        return env
    cfg = _parse_dream_max_runtime(config._read_config_key(config.DREAM_MAX_RUNTIME_KEY))
    if cfg is not None:
        return cfg
    return config.DREAM_MAX_RUNTIME_DEFAULT


def _normalize_user_handle(raw):
    """Sanitize a per-user handle into a safe single config token, or None.

    Strips surrounding quotes/whitespace, collapses internal whitespace to '-'
    (so a multi-word handle can't corrupt the `KEY=VALUE` config line), and drops
    characters outside a conservative `[A-Za-z0-9._-]` allowlist. Returns None for
    an empty/all-stripped result so the caller can reject it.
    """
    if raw is None:
        return None
    v = raw.strip().strip('"').strip("'").strip()
    if not v:
        return None
    v = re.sub(r"\s+", "-", v)
    v = re.sub(r"[^A-Za-z0-9._-]", "", v)
    # Require at least one alphanumeric: a handle that collapses to only
    # separators (e.g. "!!! @@@" -> "-") is not a usable provenance handle.
    if not re.search(r"[A-Za-z0-9]", v):
        return None
    return v


def resolve_user_handle():
    """Resolve the per-user provenance handle, or None if unset.

    Precedence: AGENTWARE_USER_HANDLE env -> config.env -> None. None means the
    writer falls back to _operator_handle(kdir) — byte-identical to pre-team
    behavior.
    """
    env = _normalize_user_handle(os.environ.get(config.USER_HANDLE_KEY))
    if env:
        return env
    cfg = _normalize_user_handle(config._read_config_key(config.USER_HANDLE_KEY))
    if cfg:
        return cfg
    return None


def resolve_embedder_backend():
    """Resolve the configured LOCAL embedder backend spec, or None.

    Precedence: AGENTWARE_EMBEDDER_BACKEND env -> config.env -> None. An unset
    spec means NO semantic backend, so Mode A stays the zero-install default.
    The spec is either a dotted module name (importable on sys.path) or a
    filesystem path to a .py file satisfying the embedder contract (see
    load_embedder).
    """
    env = os.environ.get(config.EMBEDDER_BACKEND_KEY)
    if env and env.strip():
        return env.strip()
    cfg = config._read_config_key(config.EMBEDDER_BACKEND_KEY)
    if cfg and cfg.strip():
        return cfg.strip()
    return None


def resolve_embed_model():
    """Resolve the embedding model id (env -> config.env -> default).

    Passed to the backend factory. The default names the reference Ollama
    backend's `nomic-embed-text`; other backends interpret it as they see fit.
    """
    env = os.environ.get(config.EMBED_MODEL_KEY)
    if env and env.strip():
        return env.strip()
    cfg = config._read_config_key(config.EMBED_MODEL_KEY)
    if cfg and cfg.strip():
        return cfg.strip()
    return config.EMBED_MODEL_DEFAULT


_EXPORTS = [
    "_phase_key",
    "_parse_local",
    "_parse_local_strict",
    "_parse_model",
    "_parse_model_strict",
    "resolve_phase_cli",
    "resolve_phase_model",
    "resolve_phase_local",
    "_parse_dream",
    "_parse_dream_strict",
    "resolve_dream",
    "_parse_dream_schedule_strict",
    "resolve_dream_schedule",
    "_parse_dream_max_runtime",
    "resolve_dream_max_runtime",
    "_normalize_user_handle",
    "resolve_user_handle",
    "resolve_embedder_backend",
    "resolve_embed_model",
]
