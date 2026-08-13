"""agentware_dashboard — the read-only observability dashboard sidecar.

Owns: the package namespace and the layout of the dashboard server; it
re-exports NOTHING and has no import side effects.
Does NOT own: any single concern — the entrypoint is `__main__`, the security
kernel is `confine`, the socket server is `httpserver`, the request handler is
`handler`, the URL table is `routes`, and every JSON payload is built by a
`read*` module named for its domain.

WHY A SIDECAR PACKAGE: the toolkit's moat is enforced by
`tests/gates/test_invariants.py`, whose import guards `ast.walk` the CLI corpus
and FORBID any network-capable module (`http`, `socket`, `ssl`, `urllib`, ...),
even nested or lazy. This server needs `http.server`, so it lives OUTSIDE the
guarded corpus in its own process (`scripts/agentware dashboard` launches it via
subprocess, the same pattern as the embedder backends) and carries its own
declared policy row (`dashboard-sidecar` in `tests/_security_policy.py`):
stdlib-only, network allowed, loopback only.

WHY A PACKAGE AND NOT A FILE: it was a 1,146-line single file — the tree's
second monolith after the CLI — measured over the 250-line cap for a served
script by the ceiling gate and carrying a declared debt row owned by plan task
69, this decomposition. The layer discipline mirrors `agentware_lib`: the
entrypoint parses argv and nothing else, the security kernel is one file with
one unit file, the handler owns HTTP mechanics and no derivation, and the read
model is split by DOMAIN so a panel's payload has one owning module.

READ-ONLY: this package adds NO writer. Every payload delegates to the
toolkit's existing read-only derivations under `agentware_lib`; the static layer
only reads `webui/dist/`.
"""

__all__ = []
