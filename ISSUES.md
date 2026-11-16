# Issues Analysis

## Summary

| Type | Critical | High | Medium | Low | Total |
|------|----------|------|--------|-----|-------|
| Product Issues | 0 | 2 | 1 | 0 | 3 |
| Code Issues | 0 | 2 | 1 | 0 | 3 |

I ranked the issues that can leave data wrong or unavailable first. The statement-rendering problem comes after those because it makes fetched content hard to review but does not block a migration.

---

## Product Issues

> Product issues are user-facing problems: broken functionality, missing validation, poor UX, data integrity risks visible to users.

### [P1 / 1] Re-migrating fewer tests leaves deleted tests in the database

**Severity**: High

**Location**: `PolygonMigration/problems/views.py` (`index`, `migrate_test_cases_to_db` branch)

**Description**:
When a user clicks **Migrate Test Description to Database** (the button that saves test cases), the view updates rows for the tests it receives or creates rows for new positions. It never deletes database rows that no longer appear on Polygon. For example, after a problem is reduced from 20 tests to 12, a fresh migration updates the first 12 rows but leaves rows 13–20 behind. The migration also reads test cases from Redis when present, so a cached 20-test list can be reused for up to 30 minutes before Polygon is queried again.

**Impact**:
- The database can advertise and serve test cases that the problem setter has removed.
- A user may believe the migration refreshed the test set when old cases remain mixed in.
- The result can be especially confusing because the page reports success.

**Suggested Fix**:
Treat migration as reconciliation: identify tests with stable Polygon test indexes, update or create those rows, and delete rows that are no longer in the source set within the same database transaction. Provide an explicit “refresh from Polygon” action or invalidate the relevant Redis cache when the user requests a fresh migration.

**Priority rationale**:
This is first because the action claims to synchronize data but can leave incorrect test cases in the database. Stale test data can directly affect downstream evaluation. It is more urgent than the title collision, which blocks one class of problem, and the statement formatting issue, which affects readability rather than stored test correctness.

---

### [P2 / 2] Different problems with the same title cannot both be migrated

**Severity**: High

**Location**: `PolygonMigration/problems/models.py` (`Problem.slug`); `PolygonMigration/problems/views.py` (`index`, problem create branch)

**Description**:
The view correctly looks up an existing problem by `polygon_id`, but it builds `slug` from the title alone with `slugify(title)`. `Problem.slug` is unique. Two Polygon IDs with the title “Two Sum” therefore try to create the same slug; the second insert raises an integrity error instead of creating a second problem. In my test, the second migration failed and the first problem’s saved details remained unchanged.

**Impact**:
- A valid Polygon problem cannot be migrated just because another problem has the same title.
- The user sees a generic migration failure rather than an explanation of the title/slug collision.
- Different titles can also collide after slugification, for example titles that differ only in punctuation.

**Suggested Fix**:
Make the slug unique independently of the display title, for example by appending a stable Polygon identifier. If a slug collision is still possible, handle it explicitly and show a useful message. Preserve the existing slug when updating the same Polygon problem unless the product requires changing it.

**Priority rationale**:
This is second because it prevents migration of legitimate problems and is deterministic for duplicate titles. It ranks below stale test data, which can silently leave incorrect records after a successful migration, but above a presentation defect because the affected user cannot complete the task at all.

---

### [P3 / 3] Fetched statements show markup instead of readable problem content

**Severity**: Medium

**Location**: `PolygonMigration/problems/views.py` (`parse_problem_html`); `PolygonMigration/problems/templates/problems/index.html` (problem statement, input/output, and notes sections)

**Description**:
`parse_problem_html` extracts HTML fragments from Polygon’s `problem.html`, but the template renders those strings with `linebreaksbr` and Django’s normal auto-escaping. The tags are therefore shown as text instead of being rendered as statement markup. The page also has no math renderer for the LaTeX used in many statements. When I tried other Polygon problems, I saw the raw HTML tags and unrendered math in the preview.

**Impact**:
- Users have to interpret raw tags and cannot reliably read formatted statements, examples, or formulas.
- This makes the fetched preview less useful for review before saving the problem.

**Suggested Fix**:
Render the extracted fragment as HTML only after sanitizing it with an allowlist, and add a client-side math renderer for the notation Polygon statements use. Keep the escaping boundary explicit so rendering statements does not introduce an HTML injection risk.

**Priority rationale**:
This is third because it degrades a core review step across problems, but it does not prevent migration or corrupt stored test data. The fixes above protect correctness and successful completion first.

---

<!-- Keep this section to three product issues, ranked from highest to lowest priority. -->

---

## Code Issues

> Code issues are technical problems: bugs, security vulnerabilities, performance problems, code quality concerns, architectural issues.

### [C1 / 4] Storage upload can delete the previous set and leave a partial replacement

**Severity**: High

**Location**: `PolygonMigration/problems/polygon_api.py` (`migrate_to_storage`); `PolygonMigration/problems/views.py` (`index`, storage branch and exception handling)

**Description**:
`migrate_to_storage` deletes every object under the problem prefix before uploading the replacement files. If an upload fails partway through, the previous complete set is already gone and a partial new set may remain. The view sets `azure_blob_uploaded = True` only after `migrate_to_storage` returns, so its cleanup path does not run for an exception during the upload itself. Also, `transaction.atomic()` can roll back database writes, but it cannot roll back calls already made to Azure or S3. A later exception in the same request can therefore leave storage and the database out of sync despite the error message saying changes were rolled back.

**Impact**:
- A transient provider/network error can make the stored test set unusable or incomplete.
- The view’s rollback message can give engineers and users a false impression that storage writes were undone.
- The issue affects both supported providers because both implement deletion and upload as external operations.

**Suggested Fix**:
Upload to a temporary/versioned prefix first, verify the expected input/output pairs, then switch the active reference or replace the old prefix only after the new set is complete. Track partial uploads for cleanup, and report database and storage outcomes separately. Keep database transactions limited to database work.

**Priority rationale**:
This is first because it can destroy a previously usable copy of the tests and leave an incomplete replacement. It is more urgent than the test-row reconciliation issue because storage deletion happens before the new set is known to be complete.

---

### [C2 / 5] Test-case persistence is positional and silently lossy

**Severity**: High

**Location**: `PolygonMigration/problems/views.py` (`index`, test-case migration branch); `PolygonMigration/problems/models.py` (`ProblemTestCase`)

**Description**:
Existing `ProblemTestCase` rows are matched to fetched tests by their position in an ordered list, not by Polygon’s test index. During the save, input and output are stripped with `rstrip()` and sliced to the first 260 characters. Rows with empty input or output are skipped. The model does not store Polygon’s original test index or its `manual` flag, and the migration does not remove rows after the new list ends. Because the loop uses the original list index to select a row but increments `order` only for saved tests, skipping an item can also assign duplicate or unexpected order values.

**Impact**:
- Large test files are saved incompletely, and their exact trailing whitespace/newline is lost.
- A skipped test can make the saved `order` diverge from the list position and can produce duplicate order values.
- Engineers cannot reliably trace a saved row back to its original Polygon test, and removed cases remain as stale rows.

**Suggested Fix**:
Add a source test index (and any source metadata the product needs) to `ProblemTestCase`, use it as the update key, and define an explicit policy for tests that are too large or lack input/output. Preserve raw test content unless a documented database limit requires truncation; if truncation is required, make it visible instead of silent. Reconcile removals as described in P1.

**Priority rationale**:
This is second because the current save path can change the test data itself and loses identity needed for future updates. It is closely related to the user-visible stale-row issue, but this recommendation addresses the underlying persistence design and additional loss of content.

---

### [C3 / 6] One view and one API helper carry too many responsibilities

**Severity**: Medium

**Location**: `PolygonMigration/problems/views.py` (`index`); `PolygonMigration/problems/polygon_api.py` (`PolygonAPI`)

**Description**:
`index` is a single large handler for fetching, parsing, database migration, test-case migration, and storage upload, with nested conditionals and shared mutable template context. `PolygonAPI` also reaches beyond Polygon HTTP calls: it downloads and unpacks packages, manages Redis test-case caching, compiles custom checkers, and coordinates storage. This means a change to one workflow can unexpectedly affect the others. The app test modules (`problems/tests.py` and `users/tests.py`) contain only the default placeholder imports, so these paths have no focused regression coverage.

**Impact**:
- It takes longer to understand which work a POST will perform and which error path will run.
- Changes to API, cache, database, or storage behavior are difficult to test in isolation.
- Existing edge cases such as stale rows, duplicate slugs, and partial storage failures can recur unnoticed.

**Suggested Fix**:
Keep `index` responsible for request validation and response rendering, and move each action into a small service function with explicit inputs and results. Split Polygon transport, package parsing, cache access, checker compilation, and storage coordination into focused components. Add tests around the service boundaries using mocked Polygon and storage clients, especially for retries, removals, and partial failures.

**Priority rationale**:
This is third because it increases the cost and risk of every future change, but it is less immediately damaging than the concrete data-loss and data-integrity issues above. Separating the responsibilities also makes the first two fixes easier to implement and verify.

---

<!-- Keep this section to three code issues, ranked from highest to lowest priority. -->

---

## Edge Case Analysis

I marked which results I tested and which ones I worked out from the code. I observed the duplicate-title conflict, but did not run problems with zero sample cases or a 20-to-12 test set; those answers are based on code reading.

### Q1

> A Polygon problem has 0 sample test cases but 15 regular test cases. What happens when you migrate this problem?

**From code reading.** Clicking **Create/Update problem in Database** creates or updates the `Problem` record. The view sets `test_case_count` from the Polygon test list (15), filters the tests marked `useInStatements` into `sample_tests`, and writes only those to `SampleTestCase`. With zero samples, that loop creates no sample rows. This problem-migration button does not write the 15 regular tests to `ProblemTestCase`. To save all 15 tests in the database, the user must separately click **Migrate Test Description to Database** (the assignment refers to this as migrating test cases to the DB); that branch iterates the full test list and creates `ProblemTestCase` rows for tests with nonempty input and output. If instead the user uploads to storage, `migrate_to_storage` uploads each nonempty input/output pair. **Reference:** [R1](#r1-current-test-list).

---

### Q2

> A problem is migrated with 20 test cases. Later, the problem setter removes 8 test cases on Polygon (now 12 remain). The problem is re-migrated. What happens?

**From code reading; this exact 20-to-12 scenario was not recorded as tested.** Under **Migrate Test Description to Database**, if the migration sees the current 12 tests, it updates the first 12 existing rows but never deletes rows 13–20. The database therefore still has 20 rows, including eight stale cases. There is an additional cache wrinkle: test cases are cached in Redis for 30 minutes. If the old 20-test list is still cached, the button may reuse it until the cache expires or is refreshed, so the migration may continue updating all 20. The regular **Create/Update problem in Database** action does not reconcile `ProblemTestCase` rows at all; it only updates the problem and its sample-case rows, and it also does not delete sample rows that disappeared. **Reference:** [R1](#r1-current-test-list) shows the recorded problem had 13 tests, so the 20-to-12 answer is from code reading, not that screenshot.

---

### Q3

> Two different Polygon problems have the exact same title: "Two Sum". You migrate the first one successfully. Then you try to migrate the second one. What happens?

In my test, the second migration failed and the first problem’s saved details stayed unchanged. The code explains why: the view searches by `polygon_id`, so it does not treat the second Polygon problem as the first one. It then slugifies the shared title to the same value (for example, `two-sum`) and tries to create another `Problem`. Because `Problem.slug` is unique, the database rejects the insert. The transaction rolls back and the page reports a migration failure. This is a slug collision, not an overwrite by title. The screenshot set does not include a dedicated capture of that error; [R2](#r2-polygon-problem-list) shows the Polygon problem list from the test setup.

---

### Q4

> When test cases are saved to the database via "Migrate Test Cases to DB," some data is intentionally discarded. What data is lost? Why might this cause problems?

**From code reading.** The assignment calls this action **Migrate Test Cases to DB**; the button in this checkout says **Migrate Test Description to Database**. For each test that has both input and output, the migration calls `.rstrip()` on both strings, removing trailing whitespace including the final newline, then saves only the first 260 characters of each. Tests with empty input or output are skipped. `ProblemTestCase` stores the sample flag and description, but it does not store Polygon’s original test index or the `manual` flag. The view assigns a new sequential `order` instead.

This can break exact test reproduction when whitespace matters, silently corrupt long test files, omit tests with one missing side, and make a stored row hard to match back to the original Polygon test. The code comment calls the limit “260 bytes,” but Python string slicing limits this to 260 characters, which can also differ from 260 bytes for non-ASCII text. **Reference:** [R3](#r3-test-case-preview) shows the short test inputs in the recorded run; the truncation behavior itself comes from reading the save code, not from that screenshot.

---

## Severity Guidelines

Use these definitions when assigning severity:

| Severity | Definition | Examples |
|----------|------------|----------|
| **Critical** | System broken, security vulnerability, data loss | SQL injection, authentication bypass, data corruption |
| **High** | Major functionality broken, significant data integrity risk | Feature doesn't work, orphaned records, race conditions |
| **Medium** | Feature partially broken, poor UX, code maintainability | Missing validation, confusing errors, code duplication |
| **Low** | Minor issues, cosmetic, best practice violations | Unused imports, inconsistent formatting, missing logs |

---

## Notes

- I observed the duplicate-title failure during testing. The other edge cases are labeled as code-reading conclusions because I did not run those scenarios.
- The notes also report slow fetches (about 65–70 seconds on the first request and about 36–40 seconds on a repeat) and a shorter repeat after Redis had cached the test list. This supports a future performance investigation, but the core data-integrity and migration-blocking problems above take priority.
- The fetch form relies on browser-side `required` validation for an empty ID. Other malformed IDs reach the backend and are rejected by Polygon with an error shown in the page; server-side ID validation would make that path clearer and avoid unnecessary external requests.
- In my login test, more than ten wrong password attempts did not trigger a lockout. The staff login has no rate limit in this codebase; add throttling before exposing it to untrusted networks. I left this outside the top three because the main priorities here are migration correctness, and the test was run in a local assessment setup.

---

## References

These screenshots are kept in `refs/flow2/`. R1 and R3 provide context for the recorded test set; neither depicts the hypothetical zero-sample or 20-to-12 scenario. R2 is setup context rather than a screenshot of the duplicate-slug error. R4 supports the statement-formatting observation. The edge-case outcomes that were not directly exercised are identified as code-reading conclusions above.

### R1. Current test list

The captured run has 13 tests, with the first three marked as samples. It helps distinguish the actual test data from the zero-sample and 20-to-12 hypotheticals.

![R1: The migration page showing 13 test cases and sample flags](refs/flow2/image-5.png)

### R2. Polygon problem-list context

This is the Polygon problem list from the test setup.

![R2: Polygon problem list from the test setup](refs/flow2/image-34.png)

### R3. Test-case preview

This preview shows ordinary short cases. It does not demonstrate truncation; the 260-character limit and whitespace loss are established by the code path described in Q4.

![R3: Test-case preview in the migration page](refs/flow2/image-6.png)

### R4. Fetched problem statement preview

The screenshot records the fetched problem content as displayed in the page and supports the readability concern in P3.

![R4: Fetched problem statement and formats](refs/flow2/image-4.png)
