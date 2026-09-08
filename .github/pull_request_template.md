## Summary
<!-- Provide a clear, high-level summary of what this pull request changes and why. -->

## Changes
- 

## Verification & Testing Evidence
<!-- List the automated commands and tests run to verify the changes -->
- [ ] `python -m pytest -q` (All unit & integration tests pass)
- [ ] `python -m compileall -q local_coding_agent tests` (Zero syntax/bytecode errors)
- [ ] `git diff --check` (Diff is clean)
- [ ] `local-agent doctor`
- [ ] `local-agent test-run --mock`

## Invariants Compliance
- [ ] Proposal-only mode preserved as default
- [ ] Bounded task envelope allowlist respected
- [ ] External test evidence strictly required
- [ ] Duplicate tool call guard preserved
