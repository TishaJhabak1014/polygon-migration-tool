# Polygon Migration Tool

This Django application lets staff users retrieve problem statements and tests from Polygon, review the fetched information, and save it to PostgreSQL or upload test files to configured object storage. The browser posts forms to a single staff-only view; that view coordinates Polygon requests, Redis caching, database writes, and storage operations.

## Architecture at a glance

| Layer | Implementation | Code |
|---|---|---|
| Browser | Django templates and a little inline JavaScript | `PolygonMigration/problems/templates/problems/index.html`, `PolygonMigration/users/templates/users/login.html` |
| Routing and backend | Django project with `users` and `problems` apps | `PolygonMigration/PolygonMigration/urls.py`, `PolygonMigration/users/urls.py`, `PolygonMigration/problems/urls.py`, `PolygonMigration/users/views.py`, `PolygonMigration/problems/views.py` |
| Database | PostgreSQL, through Django ORM | `PolygonMigration/problems/models.py`, `PolygonMigration/users/models.py`, `PolygonMigration/contents/models.py` |
| Polygon | Signed HTTP POST requests to Polygon API | `PolygonMigration/problems/polygon_api.py` (`PolygonAPI`) |
| Test cache | Redis; cache errors are caught and callers fall back to Polygon | `PolygonMigration/problems/polygon_api.py` |
| Object storage | S3-compatible or Azure Blob Storage behind `StorageBackend` | `PolygonMigration/problems/storage/` |

The project URL configuration mounts `/` to `problems.urls` and `/users/` to `users.urls`. The `problems.index` view is protected by `user_passes_test` and requires an authenticated staff user.

## User flows

### 1. User login

- **What the user submits:** On `/users/login/`, the user enters an email and password in the “Staff Login” form and clicks **Login**.
- **What the frontend sends:** An HTTP `POST` to `/users/login/` with `email`, `password`, and Django’s CSRF token. The form is in `users/templates/users/login.html`.
- **What the backend does, in order:** `users.views.login_view` first logs out an already-authenticated session. For a POST, it calls Django `authenticate(request, username=email, email=email, password=password)`. `users.backends.EmailBackend.authenticate` looks up the custom `users.User` by email, checks the password hash, and checks that the user can authenticate. If authentication succeeds, `login_view` checks `user.is_staff`; staff users are logged in and redirected to the `problems:index` route (`/`). Non-staff users remain on the form and get a message; invalid credentials also remain on the form.
- **External APIs called:** None.
- **Database or storage:** The user record is read from the configured Django database. Django writes the authenticated session using the configured session backend (the project does not define a separate session backend in its settings). Passwords are stored as Django password hashes, not plaintext.
- **What the user sees:** Successful staff login redirects to the problem migration page. Invalid credentials show “Invalid email or password.” A valid non-staff account shows “You do not have staff access.” Messages are rendered above the form. `/users/logout/` invokes `logout_view` and redirects to the named `login` route.
- **Code pointers:** `PolygonMigration/PolygonMigration/urls.py`; `PolygonMigration/users/urls.py`; `PolygonMigration/users/views.py:login_view`; `PolygonMigration/users/backends.py:EmailBackend.authenticate`; `PolygonMigration/users/models.py:User`.

### 2. Fetching a problem from Polygon

- **What the user submits:** On `/`, the staff user enters a Polygon problem ID and clicks **Fetch Problem**. The form is in `problems/templates/problems/index.html`.
- **What the frontend sends:** A CSRF-protected `POST` to `/` with `problem_id`. No migration-action field is included, so this fetches and displays data without requesting a database or storage migration.
- **What the backend does, in order:** `problems.views.index` creates a `PolygonAPI`, looks up any existing `Problem` by `polygon_id` for display, then calls `get_problem_info`. It downloads the latest `standard` package using `download_and_extract_package`, extracts `problem.html` into a temporary directory, and passes its HTML to `parse_problem_html` to derive title, statement/legend, input/output format, and notes. The package helper makes its own `problem.info` request before it looks up and downloads the package. The view calls `get_all_test_cases` to retrieve tests for display and stores them in Redis for 30 minutes when caching is available. It then prepares previews (first 50 characters) and full test text for the page, gets checker and metadata information, and places the fetched result into the template context. The normal fetch path also calls `get_test_cases` to calculate a test count.
- **External APIs called:** `problem.info` (`problemId`); `problem.packages` (`problemId`) to find the latest standard package; `problem.package` (`problemId`, `packageId`, `type`) to download the ZIP; `problem.tests` (`problemId`, `testset=tests`); for each test, `problem.testInput` and `problem.testAnswer` (`problemId`, `testset`, `testIndex`); and `problem.checker` (`problemId`). `get_custom_checker_info` may call `problem.checker` as well. Polygon API requests are POSTed by `_make_request` to `https://polygon.codeforces.com/api/`; request signing adds the API key and timestamp and computes the SHA-512 signature using the configured secret. The package is a binary ZIP response.
- **Database or storage:** Reads `Problem` by `polygon_id` and reads all `ProblemTag` records for the page. A successful cache write stores each test’s input, output, index, description, and sample flag in Redis under `polygon_migration_test_cases_{polygon_id}_count` and `..._test_{n}`, with a 30-minute expiry. No problem or test-case database rows are created by a fetch-only POST. Package files are extracted to a temporary directory, which `download_and_extract_package` removes in its cleanup path.
- **What the user sees:** The page displays problem details and test case previews. Full content can be opened from the preview controls. Existing database metadata and tags are also available to the page. Errors are rendered in the page’s error alert; Polygon helper methods may convert some individual test/checker lookup failures to empty results.
- **Code pointers:** `PolygonMigration/problems/urls.py`; `PolygonMigration/problems/views.py:index`, `parse_problem_html`; `PolygonMigration/problems/polygon_api.py:PolygonAPI.get_problem_info`, `download_and_extract_package`, `get_all_test_cases`, `get_test_cases`, `_make_request`, `_generate_api_sig`.

### 3. Migrating a problem to the database

- **What the user clicks or submits:** After fetching a problem, the user selects a difficulty, optionally selects tags or enters a new tag, and clicks **Create/Update problem in Database**. The page’s difficulty selector and tag controls populate hidden form fields.
- **What the frontend sends:** A CSRF-protected `POST` to `/` with `problem_id`, `migrate_to_db=1`, `difficulty`, and zero or more repeated `tags` fields. The template also supports `new_tag`, although the displayed tag picker serializes selected tag names through `tags` fields. Button forms live in `problems/templates/problems/index.html`.
- **What the backend does, in order:** `problems.views.index` performs the normal Polygon fetch/parse work to obtain current problem metadata and test cases. It rejects an empty difficulty. It finds an existing `Problem` by `polygon_id`; if found, it updates it, otherwise it creates one. It saves the title and slug, difficulty, statement/input/output HTML, time and memory limits, checker type, test-case count, and notes. It clears the existing many-to-many tags, then gets or creates each submitted `ProblemTag` and attaches it. Finally, it reads sample cases from the Redis cache (or fetches them again from Polygon if absent) and updates existing `SampleTestCase` rows by order or creates missing rows for nonempty input/output. This action does not remove old sample rows beyond the current fetched count. All database operations are inside `transaction.atomic()`.
- **External APIs called:** The same problem metadata, package, and test-fetch calls described in the fetch flow. The view may additionally call `problem.solutions` and `problem.viewSolution` to display a main solution; this solution is not stored by this flow. Cache misses can trigger another `get_all_test_cases` Polygon fetch.
- **Database or storage:** Reads and creates/updates `problems.Problem` keyed by its unique `polygon_id`, reads/creates `problems.ProblemTag`, updates the `Problem.extra_tags` relation, and reads/creates/updates `problems.SampleTestCase` rows. It does not write the complete test set into `ProblemTestCase`; that is a separate action. Storage is not written.
- **What the user sees:** A database status message says the problem was created or updated, with a custom-checker notice when applicable. The page also renders the database-backed problem and current tags. Missing difficulty or an exception displays an error. A database exception rolls back the transaction.
- **Code pointers:** `PolygonMigration/problems/templates/problems/index.html` (difficulty/tag controls and submit form); `PolygonMigration/problems/views.py:index`; `PolygonMigration/problems/models.py:Problem`, `ProblemTag`, `SampleTestCase`.

### 4. Migrating test cases to the database

- **What the user clicks or submits:** The user first needs a problem row in the database, then clicks **Migrate Test Description to Database** below the test-case list. The POST field is named `migrate_test_cases_to_db`.
- **What the frontend sends:** A CSRF-protected `POST` to `/` with `problem_id` and `migrate_test_cases_to_db=1`.
- **What the backend does, in order:** `problems.views.index` looks up `Problem` by Polygon ID and stops with an error if it is missing. It gets the test list from Redis when available, otherwise calls `get_all_test_cases` and attempts to cache the result. For each test it strips trailing whitespace from input and output, skips the test if either is empty, truncates both strings to 260 characters, and retains the sample flag and description. It updates existing `ProblemTestCase` rows ordered by `order` or creates rows for new positions; `order` is assigned sequentially from 1. Existing rows beyond the current fetched list are not deleted. The transaction protects database changes.
- **External APIs called:** On a cache miss, `problem.tests` and per-test `problem.testInput`/`problem.testAnswer`, through `PolygonAPI.get_all_test_cases`. A cache hit avoids these calls for test persistence. The overall view still performs the normal metadata/package fetch needed to render the page.
- **Database or storage:** Reads the related `ProblemTestCase` rows and writes their `input`, `output`, `is_sample`, `description`, and `order` fields. Rows reference `Problem` through a foreign key. The separate `SampleTestCase` table is populated by the problem migration flow, not this action. No object storage is written.
- **What the user sees:** A success alert reads “Test cases description migrated to database Successfully.” Missing parent problem or a failed operation is shown in the error alert; database changes in the atomic block roll back on an exception.
- **Code pointers:** `PolygonMigration/problems/templates/problems/index.html` (test-case migration form); `PolygonMigration/problems/views.py:index`; `PolygonMigration/problems/polygon_api.py:get_all_test_cases`, `get_test_cases_from_redis`, `store_test_cases_in_redis`; `PolygonMigration/problems/models.py:ProblemTestCase`.

### 5. Uploading test cases to cloud storage

- **What the user clicks or submits:** The user first migrates the problem to the database, then clicks **Upload Test Cases to Storage**. The submit button is disabled in the template when no `db_problem` is present, and the backend independently checks the database row.
- **What the frontend sends:** A CSRF-protected `POST` to `/` with `problem_id` and `migrate_to_azure=1`. Despite the field’s legacy name, the backend dispatches to the configured storage provider, which can be Azure or S3.
- **What the backend does, in order:** `problems.views.index` finds `Problem` by Polygon ID and reports an error if it does not exist. It obtains the configured bucket/container name from `get_container_name`, uses the database `Problem.id` as the storage naming ID, and calls `delete_problem_test_case_cache` (a best-effort cleanup targeting a legacy cache-key pattern). It checks for a custom checker, then calls `PolygonAPI.migrate_to_storage`. That method retrieves test data from the Polygon-ID Redis cache, or falls back to Polygon and caches it for 30 minutes. It chooses the configured backend with `get_storage`, deletes existing objects under the problem’s test-case prefix, uploads each test with both nonempty input and output, then attempts to fetch and compile a custom checker if one exists. A checker binary is uploaded when compilation succeeds; otherwise its source is uploaded. The `StorageBackend` interface defines shared key naming, and provider implementations overwrite existing keys. In the view, subsequent page-render work fetches and parses problem details as well. On caught failure, the view attempts cleanup of uploaded test objects when it has marked the upload complete; database transaction rollback does not itself undo cloud writes.
- **External APIs called:** Polygon `problem.tests`, `problem.testInput`, and `problem.testAnswer` on a test-cache miss. Checker handling may call `problem.checker` and `problem.viewFile` to retrieve source. Compilation invokes local `g++` when available. The configured object-storage service receives list/delete and upload calls: Azure Blob Storage via `AzureStorage`/`AzureBlobManager`, or S3 via `S3Storage` and boto3.
- **Database or storage:** Reads `Problem` to obtain its numeric database ID; no test-case database rows are written by this flow. Objects are written under `test_cases/{database_problem_id}/{NN}` for input and `test_cases/{database_problem_id}/{NN}.a` for output, with one-based, zero-padded test numbers. For example, database problem 42’s first pair is `test_cases/42/01` and `test_cases/42/01.a`. Existing objects under `test_cases/{database_problem_id}/` are deleted first. A custom checker is stored under the same prefix as `custom_checker.exe` on Windows, `custom_checker` on other platforms, or `custom_checker.cpp` when compilation fails.
- **What the user sees:** On success, a success alert reads “Test cases uploaded to storage successfully,” optionally noting the custom checker. A missing database problem or caught upload error is shown in the error alert. The action returns the same migration page with refreshed problem/test details.
- **Code pointers:** `PolygonMigration/problems/templates/problems/index.html` (storage upload form); `PolygonMigration/problems/views.py:index`; `PolygonMigration/problems/polygon_api.py:migrate_to_storage`, `upload_custom_checker`; `PolygonMigration/problems/storage/__init__.py:get_storage`, `get_container_name`; `PolygonMigration/problems/storage/base.py:StorageBackend`; `problems/storage/azure.py`; `problems/storage/s3.py`; `problems/AzureTestcase.py:AzureBlobManager`.

## External integrations

### Polygon API

`PolygonAPI` in `problems/polygon_api.py` sends POST requests to `https://polygon.codeforces.com/api/{method}`. It uses JSON-returning `_make_request` for structured responses and `_make_plain_request` for text or file contents. The methods below are the Polygon endpoints used by the flows in this application; some are only called when the related data is needed.

| Polygon method | What the application uses it for | Important parameters |
|---|---|---|
| `problem.info` | Problem metadata such as name and limits | `problemId` |
| `problem.packages` | Find the latest standard problem package | `problemId` |
| `problem.package` | Download the selected package as a ZIP, then extract `problem.html` | `problemId`, `packageId`, `type` (normally `standard`) |
| `problem.tests` | List tests in the selected testset | `problemId`, `testset` (normally `tests`) |
| `problem.testInput` | Fetch each test’s input text | `problemId`, `testset`, `testIndex` |
| `problem.testAnswer` | Fetch each test’s expected output | `problemId`, `testset`, `testIndex` |
| `problem.checker` | Determine the checker type and detect a custom checker | `problemId` |
| `problem.viewFile` | Fetch custom-checker source when uploading a checker | `problemId`, `type`, `name` |
| `problem.solutions` | List solutions when the page displays a main solution | `problemId` |
| `problem.viewSolution` | Fetch the selected solution text | `problemId`, `name` |
| `problem.updateWorkingCopy` | Update the working copy before looking up solutions for an existing problem | `problemId` |

`get_test_cases` also calls `problem.tests` to count tests. `problem.statements`, `problem.script`, and `problem.files` helpers exist in `PolygonAPI`, but the documented migration page flows do not call them.

Requests are authenticated with `POLYGON_API_KEY` and `POLYGON_API_SECRET` from settings. `_generate_api_sig` adds the API key and current Unix timestamp to the parameters, sorts and URL-encodes them, combines them with the method name, a random six-character prefix, and the secret, then calculates a SHA-512 hash. The prefix plus hash is sent as `apiSig`; the key and timestamp are also sent with the POST. The package download uses the same signature process for `problem.package`. Credentials belong in environment configuration and should never be committed.

`_make_request` raises on an HTTP error or a Polygon response with `status=FAILED`, but it does not set an explicit request timeout. The view waits for these synchronous requests to finish. Some helpers soften failures: `get_test_cases` returns an empty list on error, and `get_all_test_cases` leaves an individual test with empty input/output if fetching its content fails. That means the page can show partial test data, and the database/storage migrations skip tests missing either side. In the recorded local run, the first fetch took about 65–70 seconds and a repeat took about 36–40 seconds; treat those as observations, not a performance guarantee.

### Cloud storage

The migration workflow talks to the `StorageBackend` interface rather than Azure or S3 APIs directly. `get_storage()` in `problems/storage/__init__.py` selects `AzureStorage` or `S3Storage` from `STORAGE_PROVIDER` (`azure` is the default). Both adapters implement `upload_bytes` and `empty_blob`; the shared `upload_test_case` method in `problems/storage/base.py` applies the object naming convention. To add a provider, implement that small interface and add it to the factory; the migration flow in `PolygonAPI.migrate_to_storage` does not need provider-specific changes.

Each test case is stored as two objects, with a one-based, two-digit test number:

```text
test_cases/{problem_id}/{test_number}      # input
test_cases/{problem_id}/{test_number}.a    # expected output
```

For example, test 1 for database problem 42 is `test_cases/42/01` and `test_cases/42/01.a`. Although the layout uses the generic name `{problem_id}`, the upload flow currently uses the **database Problem primary key**, not the Polygon ID. Before upload it removes existing objects under `test_cases/{database_problem_id}/`, then uploads each test whose input and output are both nonempty. Upload calls overwrite objects with matching keys. A custom checker, when present, is uploaded under the same problem prefix as a compiled binary or, if compilation fails, as `custom_checker.cpp`.

Set `STORAGE_PROVIDER=s3` for AWS S3 or an S3-compatible service. Configure `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, and `AWS_S3_BUCKET_NAME`; set `AWS_S3_ENDPOINT_URL` for a compatible endpoint such as a local SeaweedFS instance, and leave it empty for AWS. For Azure, set `STORAGE_PROVIDER=azure` and configure `AZURE_STORAGE_ACCOUNT_URL`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_USERNAME`, `AZURE_PASSWORD`, and `AZURE_CONTAINER_NAME`. Keep credentials in environment variables or an untracked local `.env` file.

The S3-compatible path was exercised locally with SeaweedFS: after fetching a Polygon problem and migrating it to the database, uploading a problem with 14 tests produced 28 test objects (14 input files and 14 `.a` output files). The objects can be checked in the provider’s bucket/container browser or CLI. The upload also clears the existing problem prefix first, so re-uploading replaces the current set instead of leaving stale higher-numbered tests behind.

### Redis cache

`store_test_cases_in_redis` uses the key prefix `polygon_migration_test_cases_{polygon_id}`: a `_count` key and numbered `_test_{n}` JSON values, each with a default 30-minute TTL. Cache read/write/clear methods catch errors and log them, returning `None` on read failure. Callers interpret `None` as a cache miss and fetch from Polygon. Redis connection settings are `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, and `REDIS_SSL`.

### Database models

`Problem` stores Polygon ID, title/slug, difficulty, statement sections, limits, checker type, test count, notes, and tags. `SampleTestCase` stores statement samples. `ProblemTestCase` stores the migrated test descriptions and truncated input/output. The Django database configuration in `PolygonMigration/PolygonMigration/settings.py` uses PostgreSQL; the custom authentication model is `users.User`.

## Code index

| Concern | File |
|---|---|
| Project routing and settings | `PolygonMigration/PolygonMigration/urls.py`, `PolygonMigration/PolygonMigration/settings.py` |
| Login and email authentication | `PolygonMigration/users/views.py`, `PolygonMigration/users/backends.py`, `PolygonMigration/users/models.py` |
| Migration UI and request handler | `PolygonMigration/problems/templates/problems/index.html`, `PolygonMigration/problems/views.py` |
| Problem/test database models | `PolygonMigration/problems/models.py` |
| Polygon API, signatures, test extraction/cache | `PolygonMigration/problems/polygon_api.py` |
| Storage provider selection and contract | `PolygonMigration/problems/storage/__init__.py`, `PolygonMigration/problems/storage/base.py` |
| Azure and S3 adapters | `PolygonMigration/problems/storage/azure.py`, `PolygonMigration/problems/storage/s3.py`, `PolygonMigration/problems/AzureTestcase.py` |
