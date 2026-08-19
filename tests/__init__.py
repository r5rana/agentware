# agentware test suite (stdlib unittest only — no third-party deps).
# Marks `tests/` as a package so `python3 -m unittest tests.unit.ops.test_*`
# works in addition to `python3 -m unittest discover -s tests`. Task 58 layered
# the corpus into unit/<layer>/, integration/, e2e/ and gates/ — see README.md
# for the tier contract and tests/_test_layout.py for the relocation ledger.
