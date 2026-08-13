---
name: Feature request
about: Propose a capability, a rule change or a new verb
title: ''
labels: enhancement
assignees: ''
---

<!--
Read CONTRIBUTING.md first if you intend to implement this yourself — a
capability lands as a whole unit (module, verb declaration, manifest entry,
owning test, parity row), and knowing that shapes the proposal.
-->

## The problem

<!-- What are you unable to do today? Describe the situation, not the solution.
A proposal that starts from a real loop that failed is far easier to judge than
one that starts from a feature name. -->

## The proposal

<!-- What should exist? If it is a CLI change, write the command line you want
to type and the output you want back. -->

```bash
scripts/agentware ...
```

## How it would be verified

<!-- Every capability here is proven by something that can fail. What test
would redden if this feature broke or was reverted? -->

## Alternatives considered

<!-- Including "do nothing" — say why the workaround is not enough. -->

## Scope check

- [ ] It needs no third-party dependency (the toolkit is standard library only)
- [ ] It stores nothing personal in the repository
- [ ] It is one capability, not a bundle
- [ ] I have read [`docs/architecture.md`](../../docs/architecture.md) and can
      name the layer it belongs in <!-- optional, but it speeds up the answer -->
