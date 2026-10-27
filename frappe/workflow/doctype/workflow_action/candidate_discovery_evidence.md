# Workflow candidate discovery #5967 — source qualification

Base: `62dfbccf105c2e776f197d98b4c558809232c577`, MIT.
Branch: `fix/workflow-candidate-discovery-5967`.

## RED, before any production change

Interpreter: Python 3.12.3, stdlib source-function execution only.
Command: `python3 frappe/workflow/doctype/workflow_action/test_workflow_action_candidates.py -v`

Actual function bodies are compiled from this checkout. This is behavioral
execution, not AST assertions, but storage, permission and expression boundaries
are doubles. It is NOT imported-native or native DB integration proof.

Result: `Ran 2 tests`, `FAILED (failures=2)`; zero errors/skips.

```
test_creation_adds_candidate_edge_when_saver_condition_is_false ... FAIL
AssertionError: 0 != 1 : assigned candidate must receive native role indexing

test_recipients_recheck_condition_for_each_candidate ... FAIL
AssertionError: Items in the first set but not the second:
'other@example.test' : unassigned role holder must not receive an action
```

An initial harness setup error (missing Callable binding) was corrected before
these behavioral failures. It is not counted as RED evidence.

Native Python 3.14 units await a separately approved isolated runner. Native DB
integration remains CANNOT-VERIFY without the correct carrier and fresh lab.
SiteOps custom `valid_until` expiry is NOT a native 62df role API contract and
remains separately unqualified; no read-time sweep or new provider is introduced.
No deployment, image adoption, production execution, push or PR is authorized.

## Additional test-only commit, still before production changes

Same offline command/interpreter: 12 tests, 11 failing assertions (including
subtests), zero errors/skips. Five preservation methods pass on the base.
Failures now cover candidate conditions, request-cache isolation, fail-closed
permission/expression errors, reassignment and candidate-edge retries.

The existing native test module additionally contains five imported-module,
site-free unit tests and one fresh-lab integration test. Neither has run yet.
`run_candidate_units.sh` and `candidate_unit_runner.py` define the two-run-max
RED/GREEN container plan; execution requires root's final source-head/hash ACK.
Normal hooks were inspected: only sample Git hooks are installed and no
`core.hooksPath` is configured; pre-commit is unavailable. No hooks are bypassed.
Ruff 0.16.4 from the existing qualified SiteOps venv checks/formats changed
Python paths; `bash -n` checks the test-only container launcher.

## Approved imported-native source-unit run 1 of 2

Frozen head: `66ce81dcb302c50324770a15717d95f45c9e1e3f`.
Launcher SHA256: `2174d7dfc2b95a0683104b7563aa87a45ebd85b22e6c8b02ce3732a22466479e`.
Command: `bash frappe/workflow/doctype/workflow_action/run_candidate_units.sh 66ce81dcb302c50324770a15717d95f45c9e1e3f`.
Interpreter: Python 3.14.7. Both production modules imported from `/candidate`;
all eight supplied source hashes verified, including unchanged 62df workflow
and workflow_action hashes recorded in the RCA.

Selected: `TestWorkflowActionCandidateUnits`, 5 tests, 0 passed, 0 behavioral
failures, 5 errors, 0 skipped; exit 1. **CANNOT-VERIFY, not behavioral RED**.
Lazy safe_exec/PDF import attempted `/logs/cssutils.log` while working at `/tmp`;
no retries, dependency installation or guard relaxation performed.

Raw output: `/tmp/frappe-workflow5967-units.lX8E3j/units.log`, 308 lines,
SHA256 `29d0ae53f6c2582225d7bf43a3f5a16a9873519763e6e33476508968e074d70d`.
CID: `89d5bb73c649455809527130c1a0a629ba798681eb0f103ae4b45336943d3f31`.
Docker `--rm` removed it; separate bounded exact-CID inspection returned
`No such container`. No container removal beyond the approved run was needed.
The launcher trap stopped before its own readback because `read` on Docker's
newline-free CID file returns nonzero under `set -e`. The approved launcher
remains unchanged for root inspection. Run 2 needs a new frozen-head/script ACK.

Further pre-implementation tests exercise independently literal actor exclusions
and creator-email flag propagation through native discovery's field projection.

## Root-approved runner correction, test-only

Native logging configuration is `FRAPPE_STREAM_LOGGING=1` inside `env -i`;
CID reading explicitly accepts a nonempty EOF-terminated CID before validating
the same 64-hex format and enforcing unchanged name/label ownership checks.
No masks, bounds, startup guards or test assertions are loosened.

Root explicitly amended the source-unit budget: failed setup run 1 is preserved;
at most two additional runs may qualify corrected RED and GREEN (three total).
Each requires root's exact frozen-head/script ACK; another environment failure
must stop without retry. No additional run has occurred at this commit.

A minimum uncommitted implementation briefly passed the 14 SOURCE methods
before this revised ordering arrived; it was removed completely. Both production
files are again byte-identical to 62df before this test-only runner commit.

Before corrected RED was run, inspection identified native metadata boundaries
missing from the imported-unit fixture: `client_cache` supplies the child-table
list, local System Settings supplies the strict-permission flag, and an empty
list-query/translation store completes the isolated metadata world. These are
memory-only storage fixtures; no native role/permission logic or assertions are
replaced or weakened. The launcher/startup guard remain unchanged.

## Source review preconditions, before corrected native RED

The fresh-lab integration test no longer grants System Manager. Both candidates
are ordinary System Users with Test Approver; ToDo `allocated_to` distinguishes
candidate A's read/list access from candidate B's refusal. Administrator is only
the bootstrap/saving principal. Real DB integration has NOT run.

New SOURCE negatives require the candidate context to exclude inherited
permission-bypass flags and isolate nested flags plus `message_log` and
`permission_debug_log`, restoring references, values and original absence on
success and query errors. The imported-native unit class now has six methods;
its new diagnostic case preserves the same checks with native permission code
and memory-only storage.

The unchanged offline command runs 16 methods: 20 failing assertions including
subtests, zero errors/skips on unchanged 62df production. This is SOURCE RED,
not real permission-query or imported-native proof. No container run followed
the initial failed setup run. A subsequent ACK for the older b696dca527 head
cannot authorize executing these changed tests; a new exact-head ACK is needed.

## Corrected imported-native RED, approved run 2 of 3

Frozen RED: `2ac9e4cd0c6883b8bd23e8a092e0cb245f0f6743`.
Launcher: `4804011c699d61c95dadf30cce8ce9a9e6f356f0cf97340cf8678671867f8906`.
Command: `bash frappe/workflow/doctype/workflow_action/run_candidate_units.sh 2ac9e4cd0c6883b8bd23e8a092e0cb245f0f6743`.
Python 3.14.7; all source hashes verified and both framework modules imported
from `/candidate`. Selected exactly `TestWorkflowActionCandidateUnits`, six
methods: **1 passed, 5 behavioral failures, 0 errors, 0 skips**, exit 1.

The failures demonstrate unassigned recipients, permission-denied target with
another condition-ineligible role holder, warm-cache/candidate-context errors,
bypass/diagnostic isolation and escaping role-read failure. Disabled/revoked
membership through the native role API already passes.

Full raw output: `/tmp/frappe-workflow5967-units.35P4CA/units.log`, 102 lines,
SHA256 `6f05a0847f859d8987c63ed33cb2b07e7f989d296bc80da320cf1b8dc2b45fd0`.
CID `cec611ca007762ce7f6830dc3d797179b132dfc5ecf57e8ce778a7c823782e76`;
launcher verified absence and printed `CONTAINER_ABSENT UNIT_EXIT=1`.
No integration class, site initialization, actual DB, ERPNext, network or
production target was used. This is imported-native source-unit evidence only.

## Minimum SOURCE GREEN, separate production commit

The same offline command on Python 3.12.3 now passes all 16 methods (including
their preservation/security subtests), zero failures/errors/skips. Assertions
are unchanged from the committed RED tests. Ruff 0.16.4 checks/formats all five
affected Python paths; `git diff --check` passes.

Only `model/workflow.py` and native `workflow_action.py` change production
behavior: a bounded private context, one shared candidate predicate, additive
opt-in discovery for creation, and candidate rechecking for native email.
Discovery also carries the existing `send_email_to_creator` field so native
creator email policy is not lost in its projection. Existing qualifying edges
remain indexed without a current holder; creator email filtering is separate.

AST comparison against 62df confirms unchanged native `get_transitions`,
`apply_workflow`, `has_approval_access`, condition evaluation and safe globals.
This comparison is authority-preservation evidence, NOT integration execution.
Imported-native GREEN still requires root's separate frozen-head ACK for run 3.
Custom SiteOps expiry and fresh-lab DB/all-tenant integration remain unqualified.
