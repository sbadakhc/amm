---
name: "Task / Investigation"
about: "Capture a task or investigation work"
title: "[TASK] "
labels: ["task"]
---

<!-- IMPORTANT: When creating via `gh issue create`, use --body-file or a quoted HEREDOC to prevent backtick command substitution. Do not use inline --body with multiline strings containing backticks. -->

<!-- REFERENCES: List one issue/PR reference per line. Do not comma-pack multiple #N references on a single line. -->

## Task Summary
Describe what needs to be done.

### Background
Context, related issues, SPEC.md section references.

### Deliverables
- [ ] Task step 1
- [ ] Task step 2

### Verification
- [ ] Confirm task completion
- [ ] Note results/findings (especially any divergence from SPEC.md discovered along the way)

## Human in the Loop

The agent is responsible for completing all deliverables. The human is responsible for
completing the verification checklist.
