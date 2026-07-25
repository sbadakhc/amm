---
name: "Feature Request"
about: "Suggest a new feature or enhancement"
title: "[FEATURE] "
labels: ["enhancement"]
---

<!-- IMPORTANT: When creating via `gh issue create`, use --body-file or a quoted HEREDOC to prevent backtick command substitution. Do not use inline --body with multiline strings containing backticks. -->

<!-- REFERENCES: List one issue/PR reference per line. Do not comma-pack multiple #N references on a single line. -->

## Feature Overview
Describe the requested feature.

### Problem Statement
What is the problem you are trying to solve?

### Current Behavior
Current system behavior, if any (point at the relevant SPEC.md section).

### Proposed Solution
- What should the system do?
- Which agent(s) or tool(s) does this touch (§3 agents, §6 CLI tools)?

### Design Considerations
- Rough approach
- Any real model/API behavior that needs verifying before committing to the design
  (see SPEC.md's implementation notes for prior examples of spec vs. real-API gaps)

### Implementation Plan
- [ ] Update SPEC.md first if this changes a contract
- [ ] Implement
- [ ] Verify against real data (live model call and/or a real Postgres instance, not just a unit mock)

### Verification
- [ ] Tested against real sample data
- [ ] SPEC.md updated to match what was actually built
