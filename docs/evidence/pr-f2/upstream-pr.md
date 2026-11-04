# Draft upstream PR description — do not submit without operator authorization

Title: fix(form): fail closed when Version payload decoding or filtering fails

## Summary

Follow-up to #45401 / v16 backport #45824. Their existing `get_versions` filter
already removes unreadable parent and child fields from Desk docinfo history.
This patch preserves those native permission rules rather than adding another
filter or changing audit storage.

Malformed historical data can still raise during filtering (for example, a
non-iterable `row_changed` changes collection). Administrator and DocTypes
without permission rows bypass decoding altogether, returning invalid raw data.

Decode object-shaped data before returning it for any reader. Keep the
permission-filter operation within the same narrow data-error boundary, omitting
the affected Version when decoding/filtering fails. Valid unrestricted histories
retain all values. No schema change, dependency, write to Version storage, or
change to role/share policy.

## Tests

- Add tracked DocType with level-1 Currency field and a child table. An L1 actor
  changes financial fields and child rows; an L0 actor then changes a public field.
  Loading the form as L0 must not include restricted fieldnames or either value
  string in docinfo versions. L1 keeps the financial changes.
- Verify child row changes, additions and removals preserve readable descriptions
  but omit unreadable Currency fields.
- Real-import site-free regressions cover malformed JSON/non-object data,
  malformed diff sections, Administrator, no permission rows, untracked documents,
  storage immutability, permitted fields and standard docstatus history.
- RED tests committed before production: eight failing subcases across three
  methods; baseline Currency/child protection already passes.
- GREEN: 17 site-free tests (including adjacent write-response regressions),
  three real form-load integration tests, and two existing standard-field
  integration tests passed. Python 3.14.10, Frappe v16.54.0 fork source, MariaDB
  11.8 / Redis 6.2 in a new disposable isolated bench. Ruff and diff checks pass.

## Submission notes for the operator

This is a prepared description only; no frappe/frappe PR has been opened.
Compare with current upstream first. The production delta was authored against
`0c73a8f7836663b0d5d3b796969fbae3fb05cf93` on a v16 fork already carrying
#45824. Port only the relevant production/tests to the appropriate upstream
target; exclude SiteOps evidence/release notes and unrelated fork carries.
Hosted CI, independent review, full-suite and deployed/live acceptance are not
claimed by the local focused test results.
