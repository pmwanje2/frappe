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
