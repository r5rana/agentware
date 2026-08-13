"""agentware_lib.features.configcmd.cfgports -- config ACCESSORS for the settings this migration ported (layer: features).

Owns: the `config` flags that read and persist the settings whose RESOLVERS
landed during the modular-core adoption but whose accessors did not come with
them — the signed-channel updater (task 41), the periodic self-exam (task 44),
the auto skill-promotion family (task 46) and the git guards.
Does NOT own: the settings' resolution (that is `core.config`); the `config`
handler that acts on these flags (`configcmd.cfgmain.cmd_config`); the other
flag groups (pointer / org / kb-git / retrieval / kb-mode / cli / provenance /
dream — each has its own `_register_*` helper in its own module).

WHY THESE WERE MISSING
----------------------
Each port brought a setting's RESOLVER (`resolve_checkup`,
`resolve_skill_promote`, `resolve_updater`) and left its ACCESSOR behind, because
a resolver is what the feature needs to run and the accessor is only what a human
needs to change it. The result is a setting that is real, documented, read on
every run — and unreachable: `config --set-checkup off` answered
`unrecognized arguments`, so the only way to turn the feature off was to
hand-edit `config.env` or export an env var per run.

That is a worse failure than a missing feature. The feature works, so nobody
looks for a bug; the operator simply concludes the setting does not exist.

WHY A SEPARATE MODULE
---------------------
`configcmd` is already split one flag-group per file, and the group registration
ORDER in `cfgmain.register_config` IS the rendered `--help` order, which is
golden-pinned. A new group is therefore a new file plus one call in a fixed
place, rather than edits scattered through an existing group — and the diff that
adds a setting stays readable.

Layer deps: none of its own — pure argparse declarations over the parser it is
handed. The VALUES they persist are validated by the strict parsers in
`core.config.configvalidate`, not here: a registrar that validated would be a
second place for the vocabulary to drift.
"""


def register_ported_setting_flags(p_cfg):
    """Accessors for the updater, checkup, git-guard and skill-promotion settings.

    Declared verbatim from the preserved monolith so the flag names, dests,
    metavars and help text are byte-identical — this is a surface the union
    fixture pins, and a paraphrased help string is a surface change.
    """
    p_cfg.add_argument("--set-updater", dest="set_updater", metavar="on|off",
                       help="persist the automatic signed-channel updater opt-in "
                            "to ~/.agentware/config.env (default OFF; env "
                            "AGENTWARE_UPDATE overrides per-run)")
    p_cfg.add_argument("--updater-only", action="store_true", dest="updater_only",
                       help="print only the resolved updater setting (on/off)")
    p_cfg.add_argument("--set-checkup", dest="set_checkup", metavar="on|off",
                       help="persist the periodic self-exam (checkup) opt-in to "
                            "~/.agentware/config.env (default OFF; env "
                            "AGENTWARE_CHECKUP overrides per-run)")
    p_cfg.add_argument("--checkup-only", action="store_true", dest="checkup_only",
                       help="print only the resolved checkup setting (on/off)")
    p_cfg.add_argument("--install-git-guards", action="store_true",
                       dest="install_git_guards",
                       help="install the commit-msg co-author strip guard into "
                            "a git repo (default: the knowledge-dir KB repo). "
                            "Opt-in, non-clobber; strips Claude/AI co-author "
                            "trailers from operator commits without ever "
                            "blocking a commit")
    p_cfg.add_argument("--uninstall-git-guards", action="store_true",
                       dest="uninstall_git_guards",
                       help="remove the agentware commit-msg guard (restoring "
                            "a backed-up foreign hook if present)")
    p_cfg.add_argument("--repo", dest="git_guards_repo", metavar="PATH",
                       help="target repo for --install/--uninstall-git-guards "
                            "(default: the configured knowledge dir)")
    p_cfg.add_argument("--skill-promote-only", action="store_true",
                       dest="skill_promote_only",
                       help="print only the resolved skill-promotion mode "
                            "(off|propose) for shell consumers")
    p_cfg.add_argument("--set-skill-promote", dest="set_skill_promote",
                       metavar="off|propose",
                       help="persist the skill-promotion mode (detect + "
                            "PROPOSE only; never authors or installs) to "
                            "~/.agentware/config.env (default propose; env "
                            "still overrides per-run)")
    p_cfg.add_argument("--set-skill-promote-cap", dest="set_skill_promote_cap",
                       metavar="N",
                       help="persist the per-run skill-candidate emission cap "
                            "(non-negative int; 0 = detect/report only)")
    p_cfg.add_argument("--set-skill-promote-edge-min",
                       dest="set_skill_promote_edge_min", metavar="0..1",
                       help="persist the min edge-weight detection threshold "
                            "(float in [0.0, 1.0])")
    p_cfg.add_argument("--set-skill-promote-density-min",
                       dest="set_skill_promote_density_min", metavar="0..1",
                       help="persist the min subgraph-density detection "
                            "threshold (float in [0.0, 1.0])")
    p_cfg.add_argument("--set-skill-promote-separation-min",
                       dest="set_skill_promote_separation_min", metavar="0..1",
                       help="persist the min separation-margin detection "
                            "threshold (float in [0.0, 1.0])")


_EXPORTS = ["register_ported_setting_flags"]

__all__ = list(_EXPORTS)
