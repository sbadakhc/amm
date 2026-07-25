---
name: "Bug Report"
about: "Report an unexpected behavior or problem"
title: "[BUG] "
labels: ["bug"]
---

<!-- IMPORTANT: When creating via `gh issue create`, use --body-file or a quoted HEREDOC to prevent backtick command substitution. Do not use inline --body with multiline strings containing backticks. -->

<!-- REFERENCES: List one issue/PR reference per line. Do not comma-pack multiple #N references on a single line. -->

## Bug Summary
Provide a clear, concise description of the bug.

### Steps to Reproduce
1.
2.
3.

### Expected Behavior
What you expected to happen.

### Actual Behavior
What actually happened.

### Impact
- Affected component (Evidence/Consistency/Safety/Policy/Decision agent, CLI, pipeline, DB):
- Severity:

### Root Cause Analysis
Describe the likely cause if known (optional).

### Remediation
What needs to be done to fix this:

- [ ] Identify root cause
- [ ] Implement fix
- [ ] Update SPEC.md if behavior/contracts changed

### Verification
To verify this bug is resolved:

- [ ] Issue no longer reproducible against a real listing (not just a mock)
- [ ] No regression in the other scenarios in `listings.json`

### Additional Context
Add any other context (logs, model responses, environment).
