# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**A released version is a signed tag.** Every heading below other than
`[Unreleased]` corresponds to an annotated, SSH-signed `vX.Y.Z` tag verified
against a pinned publisher key — the shape the updater will accept and nothing
else (see [`docs/release-signing.md`](docs/release-signing.md)). There is no
tagged release yet, so everything shipped so far sits under `[Unreleased]`.

## [Unreleased]

### Added

- **A layered, stdlib-only package.** All domain code moved out of a single
  entrypoint into `scripts/agentware_lib/`, a package whose layers import
  strictly downward (`core → knowledge → retrieval → ops → features → cli`).
  The entrypoint is now a `sys.path` bootstrap and a dispatch tail.
- **Per-module verb declaration.** Each CLI verb is declared by the module that
  owns it and discovered by walking the package, so adding a verb touches
  exactly one file instead of a central registry.
- **`agentware scaffold new|check`** — emits a compliant unit (module, mirrored
  owning test, router row, parity row) and runs the generators, plus an audit
  mode that answers the same wiring questions from the outside.
- **Org / team mode.** An optional multi-team knowledge root with per-team
  shards, provenance stamping, cross-team write isolation, curator-gated shared
  writes, and deterministic fleet reporting. With the org keys unset, resolution
  is byte-identical to a flat single-operator install.
- **A generated module manifest** (`catalog/modules.json`) enumerating every
  Python and shell file with its layer, ceiling and owning test, and the ratchets
  that keep the tree modular: per-file and per-function ceilings, the layer
  direction rule, test-ownership, and a negative test proving each violation
  shape reddens the suite.
- **A web dashboard** (`webui/`) served by the stdlib sidecar, which binds
  loopback only and confines every served path through a dedicated kernel.
- **Contributor documentation**: [`docs/architecture.md`](docs/architecture.md)
  (rendered from the manifest and gated against it) and
  [`docs/add-a-feature.md`](docs/add-a-feature.md), which is executed in a
  scratch checkout by its own test rather than proofread.
- **Community-health documents**: this changelog, `CONTRIBUTING.md`,
  `SECURITY.md`, `CODE_OF_CONDUCT.md`, and issue and pull-request templates.

### Changed

- **Knowledge retrieval is deterministic end to end.** No model writes or ranks
  memory; the BM25 retriever and its cache are the sole ranking path, gated
  against a recorded benchmark so a change that loses recall cannot merge.
- **The anti-monolith rules are always-on steering** (`R-MOD-01..12` in
  `AGENTS.md`), while the procedure for applying them is an on-demand skill.

### Removed

- **The single-file monolith.** The strangler migration completed: every verb
  moved to its owning module, the compatibility shim was deleted, and the
  routing tests now assert the legacy arm is unreachable.

### Security

- **A tree-wide personal-data guard.** `scripts/agentware audit` resolves the
  live operator identity at runtime and fails if any of it reaches a tracked
  file, so the published package ships zero operator data.
- **Verify-or-decline updates.** The updater is opt-in, off by default, and
  fails closed when no publisher key is pinned.
