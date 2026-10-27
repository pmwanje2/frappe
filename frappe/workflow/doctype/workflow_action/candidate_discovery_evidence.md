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
