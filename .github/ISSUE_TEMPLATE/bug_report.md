---
name: Bug report
about: Something in the toolkit, the loop or the dashboard does not behave as documented
title: ''
labels: bug
assignees: ''
---

<!--
SECURITY: do not report a vulnerability here. Use the Security tab ->
"Report a vulnerability" instead (see SECURITY.md).

PERSONAL DATA: the outputs below can contain your home path, your handle or
knowledge-base content. Redact before pasting.
-->

## What happened

<!-- One or two sentences. What did you expect instead? -->

## Reproduction

The exact command, and the full output:

```text
$ scripts/agentware ...

```

Does it reproduce on a clean checkout, or only in your working tree?

## Environment

- agentware commit: <!-- git rev-parse --short HEAD -->
- Python: <!-- python3 --version -->
- OS:
- Agent runtime (if the loop is involved): <!-- Claude Code / Codex / none -->
- Knowledge base: <!-- flat single-operator / org-team mode / none -->

## Diagnostics

```text
$ scripts/agentware audit

```

If a test is failing, the targeted run rather than the whole suite:

```text
$ python3 -m unittest tests.unit.<layer>.test_<module>

```

## Anything else

<!-- A theory about the cause, a link to the module you suspect, a workaround
you found. All optional — the sections above are what makes a report
actionable. -->
