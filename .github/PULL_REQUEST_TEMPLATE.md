# Pull Request

## Summary
Fixes #<ISSUE_NUMBER>

### Description of Changes
Provide a concise summary of changes.

### Change Checklist
- [ ] Issue referenced in title and description (if applicable)
- [ ] Branch is named descriptively
- [ ] Commit messages follow conventional style (`feat:`, `fix:`, `docs:`, etc.)
- [ ] SPEC.md updated if this changes an agent contract, schema, or routing rule

### Testing Notes
Describe how this change was tested. Prefer real verification over mocks:

- Which agent(s)/tool(s) were exercised
- Real model calls made (not just unit-level mocks) or a real Postgres instance used
- Steps to reproduce, if relevant

### Verification
To verify this change is complete:

- [ ] Behavior works as expected against real sample data (`listings.json` scenarios)
- [ ] No regressions in the other scenarios
- [ ] SPEC.md accurately reflects what was actually built

**Note**: Agent completes the change checklist, human completes the verification checklist.

### Related Issues
List one reference per line. Do not comma-pack multiple references on a single line.
