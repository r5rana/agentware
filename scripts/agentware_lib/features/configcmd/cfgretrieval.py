"""agentware_lib.features.configcmd.cfgretrieval — retrieval + embedding settings.

Owns: the `config` handlers for the RETRIEVAL/EMBEDDING family — --set-mode and
      its SETTINGS_AW alias --set-retrieval (both write the SAME
      RETRIEVAL_MODE_KEY), --retrieval-mode-only, --set-embedder /
      --embedder-only, and --set-embed-model / --embed-model-only.
Does NOT own: the WORKSPACE --set-kb-mode / --kb-mode-only (a DIFFERENT `mode`)
      -> the sibling agentware_lib.features.configcmd.cfgorg; the
      effective-mode resolution, the embedder-availability probe and the embedder
      cache -> agentware_lib.retrieval.vectorcache; the strict token PARSERS +
      the *_KEY constants -> agentware_lib.core.config; the embedder/model
      RESOLVERS -> agentware_lib.core.routing; the resolved-config VIEW ->
      agentware_lib.features.configcmd.cfgshow; the flag->handler ORDER ->
      agentware_lib.features.configcmd.cfgroutes.
Layer deps: features.configcmd.cfgretrieval -> core (config, routing), retrieval
      (vectorcache) -- all DOWNWARD for the features layer.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

Every handler here takes ``(args, as_json)`` and returns the process exit code,
byte-identical to the corresponding branch of the pre-split 622-line cmd_config.
This is the SETTINGS_AW (260627-semantic-embedding-settings) core: the single,
config-backed, EXTENSIBLE settings layer recall/eval consult to pick the active
retrieval strategy. Each setter mirrors the EXACT `_set_config_value` +
strict-token-parse + echo pattern, so a FUTURE feature flag slots in via the
identical get/set pattern (the extensibility contract). All values are persisted
to ~/.agentware/config.env; env still overrides per-run; the change takes effect
on the NEXT recall/eval (config is read per-run — no daemon, no restart). Schema
documented in docs/GUIDE.md. The BRANCH ORDER is not encoded here — it is the
cfgroutes table's single job.
"""

import json
import sys

import agentware_lib.core.config as config
import agentware_lib.core.routing as routing
import agentware_lib.retrieval.vectorcache as _vectorcache


def _set_mode(args, as_json):
    """--set-mode deterministic|semantic: persist to ~/.agentware/config.env.

    Reports any graceful fallback when semantic has no local model yet.
    """
    mode_val = getattr(args, "set_mode", None)
    norm = config._parse_retrieval_mode_strict(mode_val)
    if norm is None:
        sys.stderr.write(
            "agentware: invalid --set-mode value %r (use deterministic|semantic)\n"
            % mode_val)
        return 2
    config._set_config_value(config.RETRIEVAL_MODE_KEY, norm)
    eff, fell_back, notice = _vectorcache.resolve_effective_retrieval_mode()
    if as_json:
        json.dump({"ok": True, "retrieval_mode": norm, "effective_mode": eff,
                   "fell_back": fell_back, "notice": notice,
                   "config": config.HOME_CONFIG},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("Set %s=%s in %s (retrieval mode %s)"
              % (config.RETRIEVAL_MODE_KEY, norm, config.HOME_CONFIG, norm))
        if fell_back:
            print("Notice: %s" % notice)
    return 0


def _set_retrieval_mode(args, as_json):
    """--set-retrieval bm25|semantic: friendly ALIAS for `--set-mode`.

    Writes the SAME RETRIEVAL_MODE_KEY as --set-mode deterministic|semantic.
    """
    retrieval_val = getattr(args, "set_retrieval", None)
    norm = config._parse_retrieval_mode_strict(retrieval_val)
    if norm is None:
        sys.stderr.write(
            "agentware: invalid --set-retrieval value %r (use bm25|semantic)\n"
            % retrieval_val)
        return 2
    config._set_config_value(config.RETRIEVAL_MODE_KEY, norm)
    eff, fell_back, notice = _vectorcache.resolve_effective_retrieval_mode()
    alias = "bm25" if norm == config.RETRIEVAL_MODE_DETERMINISTIC else "semantic"
    if as_json:
        json.dump({"ok": True, "retrieval": alias, "retrieval_mode": norm,
                   "effective_mode": eff, "fell_back": fell_back,
                   "notice": notice, "config": config.HOME_CONFIG},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("Set %s=%s in %s (retrieval %s)"
              % (config.RETRIEVAL_MODE_KEY, norm, config.HOME_CONFIG, alias))
        if fell_back:
            print("Notice: %s" % notice)
    return 0


def _set_embedder(args, as_json):
    """--set-embedder <dotted-name|path>: persist the LOCAL embedder backend spec."""
    embedder_val = getattr(args, "set_embedder", None)
    norm = config._parse_embedder_backend_strict(embedder_val)
    if norm is None:
        sys.stderr.write(
            "agentware: invalid --set-embedder value %r (use a dotted module "
            "name or a path to a .py backend, no whitespace)\n" % embedder_val)
        return 2
    config._set_config_value(config.EMBEDDER_BACKEND_KEY, norm)
    _vectorcache._reset_embedder_cache()
    if as_json:
        json.dump({"ok": True, "embedder_backend": norm,
                   "semantic_embedder_available": _vectorcache.semantic_embedder_available(),
                   "config": config.HOME_CONFIG},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("Set %s=%s in %s (local embedder backend)"
              % (config.EMBEDDER_BACKEND_KEY, norm, config.HOME_CONFIG))
    return 0


def _set_embed_model(args, as_json):
    """--set-embed-model <hf-id>: persist the embedding model id."""
    embed_model_val = getattr(args, "set_embed_model", None)
    norm = config._parse_embed_model_strict(embed_model_val)
    if norm is None:
        sys.stderr.write(
            "agentware: invalid --set-embed-model value %r (use a non-empty "
            "model id, no whitespace)\n" % embed_model_val)
        return 2
    config._set_config_value(config.EMBED_MODEL_KEY, norm)
    _vectorcache._reset_embedder_cache()
    if as_json:
        json.dump({"ok": True, "embed_model": norm, "config": config.HOME_CONFIG},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("Set %s=%s in %s (embedding model id)"
              % (config.EMBED_MODEL_KEY, norm, config.HOME_CONFIG))
    return 0


def _embedder_only(args, as_json):
    """--embedder-only: bare resolved backend spec for shell consumers."""
    spec = routing.resolve_embedder_backend()
    if spec:
        print(spec)
    return 0


def _embed_model_only(args, as_json):
    """--embed-model-only: bare resolved embedding model id for shell consumers."""
    print(routing.resolve_embed_model())
    return 0


def _retrieval_mode_only(args, as_json):
    """--retrieval-mode-only: bare EFFECTIVE mode for shell consumers.

    For the loop / Phase 7 wiring. Any fallback notice goes to stderr so stdout
    stays a clean single token for capture.
    """
    eff, fell_back, notice = _vectorcache.resolve_effective_retrieval_mode()
    print(eff)
    if fell_back:
        sys.stderr.write("agentware: %s\n" % notice)
    return 0


_EXPORTS = []

__all__ = list(_EXPORTS)
