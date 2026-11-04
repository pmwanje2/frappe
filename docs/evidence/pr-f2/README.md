# PR-F2: Version payload permissions and fail-closed decoding

## Verified scope and correction to the design

The SiteOps design's F2 raw-history premise is stale at its named pin.
`infra/hosts/vm-erp/frappe-runtime-v16/carry-forward-revisions.v1.json`
and `release/components.lock.yaml` both declare Frappe
`0c73a8f7836663b0d5d3b796969fbae3fb05cf93`, on
`pmwanje2/frappe` `maximus/v16.54.0`. The release builder's
`_load_expected_revisions` / `_collect_app_records` enforce this source pin.
`apps.v1.json` includes Frappe; `release.env.example` is not an app revision
pin and its mutable example tag is not deployment proof.

This exact pin already contains `get_versions` field filtering from fork #3 /
upstream frappe/frappe#45824 (backport of #45401), including child fields,
table permlevels, shares, and name/docstatus. Do not duplicate or remove it.
The existing `readable_permlevels` calls native `Meta.get_permlevel_access`
with read permission and retains the native shared-reader level-0 allowance.

The remaining change is small: parse data before returning even to unrestricted
readers, and omit a Version if decoding or field filtering raises a data-shape
exception. Valid unrestricted histories retain their complete data. Existing
parent and child allowlists still filter restricted histories. No Version row
is modified, no schema/dependency changes, no accounting permission expansion.
Production delta: 15 added / 17 removed lines in `frappe/desk/form/load.py`.

Live deployed-image readback is **CANNOT-VERIFY**: read-only SSH to the declared
vm-erp address timed out. The pin above is the verified configured build-source
pin, not a claim about the currently serving image or ordinary-user outcomes.

Scratch clone: `/home/paulmwanje/wt/frappe-pr-f2-20261104`.
Branch: `fix/pr-f2-version-diff-field-permissions`, started at the exact pin,
not fork default `develop`. Target: `maximus/v16.54.0` (currently ahead of the
configured pin); read-only merge-tree validation found no conflict. No merge,
upstream PR, SiteOps file change, build admission, or production deployment.

## RED then GREEN

- RED `4cb82d097ba9aa899609343cda5b750d0e3b4d91`: tests and captured evidence only.
  Eight site-free methods ran: five passed, three failed across eight subcases.
  Failures demonstrate malformed diff sections breaking form loading and raw
  unparseable data bypassing decoding for Administrator/no-permission-row readers.
- GREEN `fb1435773561554777a4508888e1e457f67e03d2`: production change separately.
- Native fixture qualification `0ebb7a5242a492b17c64ce6ea97dec80403e3f58`:
  explicitly request User fixtures, use native All for level 0 instead of assuming
  the optional Blogger role exists, and assert the actors' actual read levels.

The requested Currency exclusion/retention and child-field site-free cases
already pass at the base. They are coverage of the existing carry, **not**
represented as newly failing privacy cases.

## Test evidence

Real imports, not copied function bodies. Only metadata/session/storage inputs
are doubled in the site-free tests. The separate native bench creates and saves
real custom DocTypes and documents, generates Versions as an L1 user, performs
an L0 save, calls `getdoc` as ordinary L0/L1 users, and inspects docinfo versions.

Environment: Python 3.14.10; image
`sha256:1f5740f645d5d96312bf0afe485e087f779c30a74ac3be2a0916944136d4d7b7`
(`frappe-siteops:rel-v16-0a427858`), fork checkout mounted read-only.

```sh
# Site-free selection; network disabled, root filesystem read-only, /tmp tmpfs.
docker run --rm --network none --read-only --tmpfs /tmp:rw,nosuid,nodev \
  --entrypoint /home/frappe/frappe-bench/env/bin/python \
  -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/workspace \
  -v /home/paulmwanje/wt/frappe-pr-f2-20261104:/workspace:ro -w /workspace \
  frappe-siteops:rel-v16-0a427858 -m unittest \
  frappe.tests.test_version_payload_permissions \
  frappe.tests.test_write_response_permissions.TestWriteResponsePermissions -v

# Separate disposable native bench; same checkout mounted at apps/frappe.
docker exec -w /home/frappe/frappe-bench/sites pr-f2-bench-20261104 \
  /home/frappe/frappe-bench/env/bin/python -m frappe.utils.bench_helper frappe \
  --site pr-f2.test run-tests --module frappe.tests.test_form_load \
  --case TestVersionCurrencyPermissions
docker exec -w /home/frappe/frappe-bench/sites pr-f2-bench-20261104 \
  /home/frappe/frappe-bench/env/bin/python -m frappe.utils.bench_helper frappe \
  --site pr-f2.test run-tests --module frappe.tests.test_form_load \
  --case TestStandardFieldsInVersions
```

- `red.txt`: genuine baseline failures before any production change.
- `green.txt`: **17 passed**, zero failures/errors/skips; includes nine adjacent
  write-response privacy methods.
- `bench-green.txt`: **3 integration tests passed**, zero failures/errors/skips.
- `bench-standard-fields.txt`: **2 existing integration tests passed**.
- `bench-unavailable.txt`: initial pre-initialization attempt, **0 tests / 1
  setup error**; superseded by the real bench runs, never counted as RED.
- Ruff 0.16.4 check and format check passed; `git diff --check` passed.

The bench used only the new internal Docker network `pr-f2-test-20261104`, new
`pr-f2-db-20261104` (MariaDB 11.8), `pr-f2-redis-20261104` (Redis 6.2), and
`pr-f2-bench-20261104`; no exposed ports, borrowed bench, tenant database, or
existing customer identity. Scheduler disabled. Its test-only site/database
and containers are disposable, not release artifacts.

CodeRabbit CLI 0.7.6 binary matched the official versioned archive SHA256
`9a8f79f7aa04f9e9a8c5164ded478ccac43e5226d503f3cfd58f58e34c97c075`.
Review with explicit base and base commit returned **403 Invalid organization**.
That is unavailable review, not approval. Named independent review, hosted CI,
full Frappe suite, deployed-image readback and live role acceptance remain pending.

## Exact SiteOps follow-up (operator-owned, not performed here)

1. Review/qualify and merge this fork PR into `maximus/v16.54.0` only when
   authorized. Record the resulting target-branch SHA, preserving all intervening
   carries (including fork #6-#8). Do not blindly replace it with this pre-merge
   candidate SHA or force-push the carrier/default branch.
2. In a separate guarded SiteOps worktree/PR, update
   `infra/hosts/vm-erp/frappe-runtime-v16/carry-forward-revisions.v1.json`:
   `revisions.frappe` becomes that merged carrier SHA; append the F2 provenance,
   fork PR URL, reviewed head, upstream submission status and retirement condition
   to `_comment`. Retain the existing Frappe origin and unrelated revisions.
3. Update the matching `release/components.lock.yaml` Frappe commit, carry URLs
   and removal condition. Refresh affected pinned-native source hashes/fixtures
   and intentional exact-pin assertions, including
   `tools/deploy/tests/test_rcr_version_privacy_pin.py`'s `PRIVACY_REVISION`.
   Re-run pin/provenance/privacy/fixture tests. `apps.v1.json` is unchanged.
4. Use the attested Frappe image release lane: independent clean release clone,
   exact-source build with per-app revision manifest and immutable image ID,
   isolated restored-site qualification and rollback proof, preview and authorized
   apply, then `APPLY_OK` / `VERIFY_OK` / `RECORD_OK`. Do not deploy by editing
   `release.env.example`, copying source into running containers, or fixture-only
   release. Reconcile the live stamp/image first because this task could not read it.
5. Ring0 first: with approved TEST maintenance/finance personas, inspect real
   `getdoc` network payloads for Asset, Asset Repair, Asset Category and Vehicle,
   including old/new L1 and child values, and verify the permitted L1 reader
   retains them. Confirm Version storage remains intact. Only then proceed through
   authorized estate rollout. This PR alone proves no live financial isolation.
6. Operator may submit `upstream-pr.md` to frappe/frappe after comparing its
   current target. Retire this additional carry at the first qualified official
   v16 release with equivalent fail-closed handling; retire existing carries on
   their own conditions, not merely because this PR merges.
