# SE 413/513 Assignment 3: Integration and System Testing

**Student:** Ali Belhrak (GitHub: `alibelhrak`)
**Branch:** `assignment-3`

This repository tests the **Course Registration System**: an HTTP API, a
service, and a SQLite database built around `can_register()` and
`calculate_registration_fee()` from Assignments 1–2. `SPEC.md` is the
specification (REQ-1 to REQ-8).

`src/`, `serve.py`, and `tests/system/conftest.py` are the provided starter
code and are **unmodified**. Every test lists the requirement IDs it checks in
its docstring.

| Level | File | Tests | What is real | What is replaced |
|---|---|---|---|---|
| Integration | `tests/integration/test_integration.py` | 8 (7 pass, 1 strict `xfail`) | Repository, service, SQLite file in `tmp_path` | Notifier only (`FakeNotifier`) |
| System | `tests/system/test_system.py` | 32 | The whole system: `serve.py` running as a separate process | Nothing; observed only through HTTP and the outbox |

---

## Project layout

```
assignment-3/
|-- src/                                provided, unmodified
|-- serve.py                            provided, unmodified
|-- SPEC.md, requirements.txt, pyproject.toml
|-- tests/
|   |-- integration/conftest.py         fixtures: test DB, repository, FakeNotifier, service
|   |-- integration/test_integration.py Section 4 tests
|   |-- system/conftest.py              provided `system` fixture, unmodified
|   `-- system/test_system.py           Section 5 tests
|-- .gitignore
`-- README.md
```

## Setup

```
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

## Running the system by hand (Section 3)

```
python serve.py
```

prints `READY url=http://127.0.0.1:8000 outbox=...`. In a second terminal:

```
python -c "import requests; print(requests.get('http://127.0.0.1:8000/health').json())"
python -c "import requests; r=requests.post('http://127.0.0.1:8000/enrollments', headers={'X-API-Key':'registrar-key'}, json={'student_id':'S01','course_code':'SE413'}); print(r.status_code, r.json())"
```

Result:

```
{'status': 'ok'}
201 {'course_code': 'SE413', 'credits': 3, 'fee': 300.0, 'student_id': 'S01', 'total_credits': 3}
```

---

## Running the tests (Section 6a)

All commands run from the project root. (`python -m pytest` is used because
the `pytest` script directory may not be on `PATH` on Windows.)

| Command | Runs |
|---|---|
| `python -m pytest -v -rx` | Full suite: 39 passed, 1 xfailed (`-rx` prints the xfail reason) |
| `python -m pytest -m integration -v` | Integration tests only: 7 passed, 1 xfailed |
| `python -m pytest -m system -v` | System tests only: 32 passed (each starts its own `serve.py`) |
| `python -m pytest -m system -k race -v` | The concurrency test only |
| `python -m pytest --runxfail -k withdraw` | Shows the real assertion failure behind the known defect |

Every test carries exactly one level marker, applied with a module-level
`pytestmark` (`pytest.mark.integration` in `tests/integration/test_integration.py`,
`pytest.mark.system` in `tests/system/test_system.py`). Both markers are
registered in `pyproject.toml`, which also sets `xfail_strict = true`.

---

## Section 4(a): Integration points

```
HTTP client --IP1--> api.py --IP2--> RegistrationService --IP3--> RegistrationRepository --IP4--> SQLite
                                            `--IP5--> notifier (send_confirmation / send_rejection)
```

| IP | Components | How they communicate | One thing the two sides could disagree about |
|---|---|---|---|
| **IP1** | HTTP client ↔ `api.py` | HTTP requests and JSON responses; `X-API-Key` header | The contract in SPEC.md: field names (`student_id`/`course_code`), status codes (e.g. 409 vs 422), or the error format. A client expecting `{"error": ...}` breaks if any error came back as Flask's default HTML page. |
| **IP2** | `api.py` ↔ `RegistrationService` | Python method calls; results as dicts, failures as exceptions from `errors.py` | Which exception means what: the API maps `CourseFull`/`AlreadyEnrolled` → 409 and `NotEligible` → 422. A service that raised a different or new exception would become a 500, and a renamed key in the returned dict would change the JSON body. |
| **IP3** | `RegistrationService` ↔ `RegistrationRepository` | Python method calls; return values (`bool`, `dict`, `None`) and `sqlite3.IntegrityError` | Who owns the transaction and what a return value means: the repository never commits (the service's `with connection:` does), and the service trusts that `take_seat()` returning `False` means "full, nothing changed" and that `remove_enrollment(student_id, course_code)` removes **only that student's** row. That last assumption is exactly where the known defect lies (4d). |
| **IP4** | `RegistrationRepository` ↔ SQLite | SQL statements over a `sqlite3` connection | Constraints and schema: the repository relies on the `enrollments` primary key to reject duplicates, on `seats_taken <= capacity` / the `WHERE seats_taken < capacity` guard to prevent over-filling, and on `PRAGMA foreign_keys = ON`. A different schema, or SQL that filters on the wrong column, silently changes behaviour. |
| **IP5** | `RegistrationService` ↔ notifier | Python method calls: `send_confirmation(student_id, course_code, fee)` / `send_rejection(student_id, course_code, reason)` | The method signatures and the rules for *when* to send: argument order, the fee as a float, the reason text, and that duplicates and invalid requests send **nothing** (REQ-5). |

---

## Section 4: Integration tests

Fixtures in `tests/integration/conftest.py` create, for every test, a new
SQLite **file** in `tmp_path` (`db.connect()` + `db.create_schema()`) with a
small data set of our own, the real `RegistrationRepository`, a
`FakeNotifier` that records every call in a list, and the real
`RegistrationService` built from the three.

| Courses | Credits | Capacity | Prerequisite | | Students | Completed |
|---|---|---|---|---|---|---|
| CIS350 | 4 | 10 | none | | ST1, ST2 | CIS350 |
| SE499 | 3 | **2** | CIS350 | | ST3 | nothing |
| ART101 | 1 | **1** | none | | | |

### 4(b) Repository + SQLite (IP4)

| Test | REQ | Checks |
|---|---|---|
| `test_take_seat_capacity_boundary` | REQ-2 | SE499 (capacity 2): the 2nd (last) seat succeeds; a 3rd returns `False` and `seats_taken` stays 2 |
| `test_duplicate_enrollment_rejected_by_database` | REQ-3 | A second identical `add_enrollment()` raises `sqlite3.IntegrityError`; one row remains |
| `test_current_credits_counts_only_that_students_courses` | REQ-1 | ST1 = 5 (CIS350 + ART101, not ST2's SE499); ST3 with no courses = 0 |
| `test_failed_transaction_leaves_neither_seat_nor_enrollment` | REQ-2, REQ-3 | A seat is taken, then the enrollment insert fails: the whole transaction rolls back, verified through a **second connection** to the same file |

### 4(c) Service + repository + notifier (IP3, IP5)

Each test checks **both sides**: what the database holds and what was sent.

| Test | REQ | Database | Notifications sent |
|---|---|---|---|
| `test_successful_enrollment_saves_seat_and_sends_confirmation` | 1, 2, 4, 5 | ST1 enrolled in SE499; `seats_taken` = 1 | Exactly one confirmation, fee `300.0` |
| `test_full_course_raises_course_full_and_sends_rejection` | 2, 5 | `CourseFull`; ST2 not enrolled; `seats_taken` stays 1 | ST1's confirmation, then a rejection with reason `"course is full"` |
| `test_duplicate_enrollment_raises_already_enrolled_and_sends_nothing` | 3, 5 | `AlreadyEnrolled`; still one row; `seats_taken` = 1 (no extra seat) | Only the first confirmation; the duplicate sent nothing |

### 4(d) The known defect (REQ-6)

**Defect.** `RegistrationRepository.remove_enrollment()` runs
`DELETE FROM enrollments WHERE course_code = ?`. It never filters on
`student_id`, despite its docstring ("Remove one student's enrollment"). When
one student withdraws, **every** student's enrollment in that course is
deleted, while `release_seat()` frees only **one** seat.

**Test.** `test_withdraw_removes_only_that_students_enrollment` enrolls ST1
and ST2 in CIS350 (2 seats), withdraws ST1, and asserts that ST2 is still
enrolled. It fails with `assert 0 == 1`: ST2's enrollment is gone, yet
`seats_taken` is 1, so the seat count no longer matches the enrollments table.
It is marked `@pytest.mark.xfail(strict=True, reason="Known defect: ...")`, and
the code is left unfixed. Because the xfail is strict, the test will report a
failure if the defect is ever fixed without removing the marker.

**Why a unit test of `RegistrationService.withdraw()` with a mocked repository would pass.**
`withdraw()` itself is correct: it calls `remove_enrollment(student_id, course_code)`,
raises `NotEnrolled` if that returns `False`, and then calls `release_seat(course_code)`
once. A mock can only verify those calls and their arguments, and its
`remove_enrollment` returns whatever the test tells it to. The defect lives in the
SQL inside the real repository method, which the mock replaces entirely, so it
only shows up against a real database containing two students in the same course.

---

## Section 5: System tests

The provided `system` fixture starts `serve.py` as a **separate process** with
a fresh database seeded as in SPEC.md for **each test**. The tests use only
HTTP (`system.enroll/get/delete`, or plain `requests` for unauthenticated
calls) and `system.outbox()`. `tests/system/` imports nothing from `src/`; its
only imports are `threading`, `pytest`, and `requests`.

### 5(a) End-to-end scenarios

| Test | REQ | Response | Schedule | `seats_taken` | Outbox |
|---|---|---|---|---|---|
| `test_main_success_scenario_enroll_then_everything_agrees`: S01 enrolls in SE413 | 1, 2, 4, 5, 7 | `201` with the full body, fee `300.0` | SE413 listed, 3 credits, fee `300.0` | 0 → 1 | Exactly one confirmation, fee `300.0` |
| `test_alternative_flow_missing_prerequisite_is_rejected`: S13 (no CIS350) enrolls in SE413 | 1, 5, 7 | `422`, `{"error": "prerequisite CIS350 not completed"}` | Empty, 0 credits, fee `0.0` | Stays 0 | Exactly one rejection with the same reason |

### 5(b) Robustness and security (REQ-8)

`test_invalid_or_hostile_enrollment_body_gets_json_400_and_changes_nothing`
is parametrized over **16** bodies:

| Category | Cases |
|---|---|
| Missing fields | `missing_course_code`, `missing_both_fields` |
| Wrong types | `student_id_integer`, `course_code_null`, `nested_object_value` |
| Not a JSON object | `body_is_json_list`, `body_is_json_string`, `malformed_json`, `form_encoded_not_json` |
| Length boundaries | `empty_string_id` (0 chars), `id_21_chars` (limit is 20), `oversized_id` (100,000 chars) |
| Injection-like | `sql_injection` (`S01'; DROP TABLE enrollments; --`), `script_injection`, `path_traversal`, `trailing_newline` |

Each case must get a `400` with a JSON content type, a body of exactly
`{"error": "<message>"}`, and no stack trace. Nothing may change: SE413
`seats_taken` stays 0, S01's schedule stays empty, the outbox stays empty, and
S14 still has its 15 seeded credits (so the injection did not reach the data).

`test_every_endpoint_except_health_requires_api_key` is parametrized over
**4 endpoints × 3 bad keys** (no key, wrong key, empty key) = 12 cases. Each
must get the JSON `401` `{"error": "missing or invalid API key"}` with no
effect; the unauthenticated `DELETE /enrollments/S14/MTH201` does not remove
the enrollment. `test_health_needs_no_api_key` is the control: `GET /health`
answers `200` without a key.

### 5(c) Concurrency (REQ-2)

`test_ten_students_race_for_three_seats` starts one thread per student
(S01–S10), all held at a `threading.Barrier(10)` and released together to POST
`SE499` (capacity 3). It asserts:

- exactly three `201`s and seven `409`s, each `409` being `{"error": "course is full"}`;
- `seats_taken == 3`;
- 3 confirmations and 7 rejections (`"course is full"`) in the outbox, sent to **exactly** the students who got `201` and `409` respectively;
- each admitted student's schedule lists SE499.

Exceptions inside the threads are collected and fail the test, so a crashed
thread cannot be miscounted. The test passed in 15 out of 15 repeated runs.

**Why this must be a system test rather than an integration test.** The race
only exists when `serve.py`'s threaded HTTP server handles the ten requests
at the same time, each on its own thread with its own SQLite connection, so
SQLite's locking and the atomic `UPDATE ... WHERE seats_taken < capacity` are
what keep the course at capacity. An integration test drives the service on a
single connection inside the test process, which serializes the calls and can
never exercise that concurrent path.

---

## Requirement traceability

| Requirement | Integration tests | System tests |
|---|---|---|
| REQ-1 eligibility | current credits; successful enrollment | main success; missing prerequisite |
| REQ-2 seats / capacity | capacity boundary; partial-failure rollback; success; full course | main success; **10-thread race** |
| REQ-3 duplicates | duplicate rejected by DB; partial-failure rollback; `AlreadyEnrolled` | n/a |
| REQ-4 credits and fee | successful enrollment | main success |
| REQ-5 notifications | success, full course, duplicate | main success; missing prerequisite; race; invalid bodies (nothing sent) |
| REQ-6 withdraw | **known defect (strict xfail)** | n/a |
| REQ-7 schedule | n/a | main success; missing prerequisite |
| REQ-8 API key, JSON-only errors | n/a | 16 hostile bodies; 12 missing-key cases; `/health` control |

---

## Section 6(b): Evidence the full suite passes

`python -m pytest -v -rx` from the project root. The strict xfail from 4(d)
is the only non-passing test.

```
collected 40 items

tests/integration/test_integration.py::test_take_seat_capacity_boundary PASSED [  2%]
tests/integration/test_integration.py::test_duplicate_enrollment_rejected_by_database PASSED [  5%]
tests/integration/test_integration.py::test_current_credits_counts_only_that_students_courses PASSED [  7%]
tests/integration/test_integration.py::test_failed_transaction_leaves_neither_seat_nor_enrollment PASSED [ 10%]
tests/integration/test_integration.py::test_successful_enrollment_saves_seat_and_sends_confirmation PASSED [ 12%]
tests/integration/test_integration.py::test_full_course_raises_course_full_and_sends_rejection PASSED [ 15%]
tests/integration/test_integration.py::test_duplicate_enrollment_raises_already_enrolled_and_sends_nothing PASSED [ 17%]
tests/integration/test_integration.py::test_withdraw_removes_only_that_students_enrollment XFAIL [ 20%]
tests/system/test_system.py::test_main_success_scenario_enroll_then_everything_agrees PASSED [ 22%]
tests/system/test_system.py::test_alternative_flow_missing_prerequisite_is_rejected PASSED [ 25%]
tests/system/test_system.py::test_invalid_or_hostile_enrollment_body_gets_json_400_and_changes_nothing[missing_course_code] PASSED [ 27%]
tests/system/test_system.py::test_invalid_or_hostile_enrollment_body_gets_json_400_and_changes_nothing[missing_both_fields] PASSED [ 30%]
tests/system/test_system.py::test_invalid_or_hostile_enrollment_body_gets_json_400_and_changes_nothing[student_id_integer] PASSED [ 32%]
tests/system/test_system.py::test_invalid_or_hostile_enrollment_body_gets_json_400_and_changes_nothing[course_code_null] PASSED [ 35%]
tests/system/test_system.py::test_invalid_or_hostile_enrollment_body_gets_json_400_and_changes_nothing[nested_object_value] PASSED [ 37%]
tests/system/test_system.py::test_invalid_or_hostile_enrollment_body_gets_json_400_and_changes_nothing[body_is_json_list] PASSED [ 40%]
tests/system/test_system.py::test_invalid_or_hostile_enrollment_body_gets_json_400_and_changes_nothing[body_is_json_string] PASSED [ 42%]
tests/system/test_system.py::test_invalid_or_hostile_enrollment_body_gets_json_400_and_changes_nothing[malformed_json] PASSED [ 45%]
tests/system/test_system.py::test_invalid_or_hostile_enrollment_body_gets_json_400_and_changes_nothing[form_encoded_not_json] PASSED [ 47%]
tests/system/test_system.py::test_invalid_or_hostile_enrollment_body_gets_json_400_and_changes_nothing[empty_string_id] PASSED [ 50%]
tests/system/test_system.py::test_invalid_or_hostile_enrollment_body_gets_json_400_and_changes_nothing[id_21_chars] PASSED [ 52%]
tests/system/test_system.py::test_invalid_or_hostile_enrollment_body_gets_json_400_and_changes_nothing[oversized_id] PASSED [ 55%]
tests/system/test_system.py::test_invalid_or_hostile_enrollment_body_gets_json_400_and_changes_nothing[sql_injection] PASSED [ 57%]
tests/system/test_system.py::test_invalid_or_hostile_enrollment_body_gets_json_400_and_changes_nothing[script_injection] PASSED [ 60%]
tests/system/test_system.py::test_invalid_or_hostile_enrollment_body_gets_json_400_and_changes_nothing[path_traversal] PASSED [ 62%]
tests/system/test_system.py::test_invalid_or_hostile_enrollment_body_gets_json_400_and_changes_nothing[trailing_newline] PASSED [ 65%]
tests/system/test_system.py::test_every_endpoint_except_health_requires_api_key[POST /enrollments-no_key] PASSED [ 67%]
tests/system/test_system.py::test_every_endpoint_except_health_requires_api_key[POST /enrollments-wrong_key] PASSED [ 70%]
tests/system/test_system.py::test_every_endpoint_except_health_requires_api_key[POST /enrollments-empty_key] PASSED [ 72%]
tests/system/test_system.py::test_every_endpoint_except_health_requires_api_key[DELETE /enrollments/S14/MTH201-no_key] PASSED [ 75%]
tests/system/test_system.py::test_every_endpoint_except_health_requires_api_key[DELETE /enrollments/S14/MTH201-wrong_key] PASSED [ 77%]
tests/system/test_system.py::test_every_endpoint_except_health_requires_api_key[DELETE /enrollments/S14/MTH201-empty_key] PASSED [ 80%]
tests/system/test_system.py::test_every_endpoint_except_health_requires_api_key[GET /students/S01/schedule-no_key] PASSED [ 82%]
tests/system/test_system.py::test_every_endpoint_except_health_requires_api_key[GET /students/S01/schedule-wrong_key] PASSED [ 85%]
tests/system/test_system.py::test_every_endpoint_except_health_requires_api_key[GET /students/S01/schedule-empty_key] PASSED [ 87%]
tests/system/test_system.py::test_every_endpoint_except_health_requires_api_key[GET /courses/SE413-no_key] PASSED [ 90%]
tests/system/test_system.py::test_every_endpoint_except_health_requires_api_key[GET /courses/SE413-wrong_key] PASSED [ 92%]
tests/system/test_system.py::test_every_endpoint_except_health_requires_api_key[GET /courses/SE413-empty_key] PASSED [ 95%]
tests/system/test_system.py::test_health_needs_no_api_key PASSED         [ 97%]
tests/system/test_system.py::test_ten_students_race_for_three_seats PASSED [100%]

=========================== short test summary info ============================
XFAIL tests/integration/test_integration.py::test_withdraw_removes_only_that_students_enrollment - Known defect: RegistrationRepository.remove_enrollment() deletes WHERE course_code = ? without filtering on student_id, so one student's withdrawal removes EVERY student's enrollment in that course while release_seat() frees only one seat (violates REQ-6).
======================== 39 passed, 1 xfailed ========================
```

Per level: `python -m pytest -m integration` gives **7 passed, 1 xfailed**;
`python -m pytest -m system` gives **32 passed**.