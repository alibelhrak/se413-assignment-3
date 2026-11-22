import threading

import pytest
import requests

pytestmark = pytest.mark.system


def seats_taken(system, code):
    response = system.get(f"/courses/{code}")
    assert response.status_code == 200
    return response.json()["seats_taken"]


# ---------------------------------------------------------------------------
# Section 5(a): end-to-end scenarios
# ---------------------------------------------------------------------------

def test_main_success_scenario_enroll_then_everything_agrees(system):
    """REQ-1, REQ-2, REQ-4, REQ-5, REQ-7: a student enrolls and every view agrees.

    Seed data: S01 has completed CIS350, the prerequisite of SE413 (3 credits,
    capacity 30, 0 seats taken). Expected fee for 3 credits: 300.0.
    """
    assert seats_taken(system, "SE413") == 0
    assert system.outbox() == []

    response = system.enroll("S01", "SE413")

    # 1. the response
    assert response.status_code == 201
    assert response.headers["Content-Type"].startswith("application/json")
    assert response.json() == {"student_id": "S01", "course_code": "SE413",
                               "credits": 3, "total_credits": 3, "fee": 300.0}

    # 2. the schedule
    schedule = system.get("/students/S01/schedule")
    assert schedule.status_code == 200
    assert schedule.json() == {
        "student_id": "S01",
        "courses": [{"code": "SE413", "title": "Software Testing", "credits": 3}],
        "total_credits": 3,
        "fee": 300.0,
    }

    # 3. the course's seats_taken
    assert seats_taken(system, "SE413") == 1

    # 4. the outbox: exactly one confirmation, with the same fee
    assert system.outbox() == [{"type": "confirmation", "student_id": "S01",
                                "course_code": "SE413", "fee": 300.0}]


def test_alternative_flow_missing_prerequisite_is_rejected(system):
    """REQ-1, REQ-5, REQ-7: a student without the prerequisite is refused and told why.

    Seed data: S13 has completed nothing; SE413 requires CIS350.
    """
    response = system.enroll("S13", "SE413")

    # 1. the response: 422 with the reason from SPEC.md
    assert response.status_code == 422
    assert response.headers["Content-Type"].startswith("application/json")
    assert response.json() == {"error": "prerequisite CIS350 not completed"}

    # 2. the schedule is unchanged (empty)
    schedule = system.get("/students/S13/schedule")
    assert schedule.status_code == 200
    assert schedule.json() == {"student_id": "S13", "courses": [],
                               "total_credits": 0, "fee": 0.0}

    # 3. no seat was taken
    assert seats_taken(system, "SE413") == 0

    # 4. the outbox: exactly one rejection, with the same reason
    assert system.outbox() == [{"type": "rejection", "student_id": "S13",
                                "course_code": "SE413",
                                "reason": "prerequisite CIS350 not completed"}]


# ---------------------------------------------------------------------------
# Section 5(b): robustness and security (REQ-8)
# ---------------------------------------------------------------------------

# Each case is the keyword arguments for requests.post(): `json=` sends a
# JSON body; `data=` sends raw bytes exactly as given.
INVALID_BODIES = {
    "missing_course_code":  {"json": {"student_id": "S01"}},
    "missing_both_fields":  {"json": {}},
    "student_id_integer":   {"json": {"student_id": 1, "course_code": "SE413"}},
    "course_code_null":     {"json": {"student_id": "S01", "course_code": None}},
    "nested_object_value":  {"json": {"student_id": {"$ne": None}, "course_code": "SE413"}},
    "body_is_json_list":    {"json": ["S01", "SE413"]},
    "body_is_json_string":  {"json": "S01 SE413"},
    "malformed_json":       {"data": '{"student_id": "S01", "course_code": ',
                             "headers": {"Content-Type": "application/json"}},
    "form_encoded_not_json": {"data": {"student_id": "S01", "course_code": "SE413"}},
    "empty_string_id":      {"json": {"student_id": "", "course_code": "SE413"}},
    "id_21_chars":          {"json": {"student_id": "A" * 21, "course_code": "SE413"}},
    "oversized_id":         {"json": {"student_id": "S" * 100_000, "course_code": "SE413"}},
    "sql_injection":        {"json": {"student_id": "S01'; DROP TABLE enrollments; --",
                                      "course_code": "SE413"}},
    "script_injection":     {"json": {"student_id": "<script>alert(1)</script>",
                                      "course_code": "SE413"}},
    "path_traversal":       {"json": {"student_id": "../../etc/passwd", "course_code": "SE413"}},
    "trailing_newline":     {"json": {"student_id": "S01\n", "course_code": "SE413"}},
}


@pytest.mark.parametrize("request_kwargs", INVALID_BODIES.values(), ids=INVALID_BODIES.keys())
def test_invalid_or_hostile_enrollment_body_gets_json_400_and_changes_nothing(system, request_kwargs):
    """REQ-8, REQ-5: every invalid or hostile POST /enrollments body gets a JSON 400,
    never HTML or a stack trace, and changes nothing (no seat, no notification).
    """
    response = system.http.post(f"{system.url}/enrollments", timeout=10, **request_kwargs)

    # a JSON 400 with an error message
    assert response.status_code == 400
    assert response.headers["Content-Type"].startswith("application/json")
    body = response.json()
    assert set(body) == {"error"} and isinstance(body["error"], str)
    assert "Traceback" not in response.text

    # nothing changed
    assert seats_taken(system, "SE413") == 0
    assert system.get("/students/S01/schedule").json()["courses"] == []
    assert system.outbox() == []
    # the injection did not touch existing data: S14 still has its 4 seeded courses
    assert system.get("/students/S14/schedule").json()["total_credits"] == 15


PROTECTED_ENDPOINTS = [
    ("POST",   "/enrollments", {"student_id": "S01", "course_code": "SE413"}),
    ("DELETE", "/enrollments/S14/MTH201", None),
    ("GET",    "/students/S01/schedule", None),
    ("GET",    "/courses/SE413", None),
]


@pytest.mark.parametrize("api_key", [None, "wrong-key", ""], ids=["no_key", "wrong_key", "empty_key"])
@pytest.mark.parametrize("method, path, body", PROTECTED_ENDPOINTS,
                         ids=[f"{m} {p}" for m, p, _ in PROTECTED_ENDPOINTS])
def test_every_endpoint_except_health_requires_api_key(system, method, path, body, api_key):
    """REQ-8: every endpoint except GET /health answers a JSON 401 without the right key,
    and the request has no effect.
    """
    headers = {} if api_key is None else {"X-API-Key": api_key}
    # a plain request, NOT system.http (that session always sends the right key)
    response = requests.request(method, system.url + path, json=body,
                                headers=headers, timeout=10)

    assert response.status_code == 401
    assert response.headers["Content-Type"].startswith("application/json")
    assert response.json() == {"error": "missing or invalid API key"}

    # no effect: nothing enrolled, nothing withdrawn, nothing sent
    assert seats_taken(system, "SE413") == 0
    assert system.get("/students/S14/schedule").json()["total_credits"] == 15
    assert system.outbox() == []


def test_health_needs_no_api_key(system):
    """REQ-8: GET /health is the one endpoint that answers without a key."""
    response = requests.get(system.url + "/health", timeout=10)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    
    


# ---------------------------------------------------------------------------
# Section 5(c): concurrency (REQ-2)
# ---------------------------------------------------------------------------

def test_ten_students_race_for_three_seats(system):
    """REQ-2, REQ-5: SE499 has 3 seats; 10 eligible students (S01-S10) request it
    at the same moment. Exactly 3 get in, 7 are told the course is full, and
    the course is never over capacity.
    """
    students = [f"S{n:02d}" for n in range(1, 11)]
    barrier = threading.Barrier(len(students))
    results = {}          # student_id -> (status code, JSON body)
    errors = []

    def enroll(student_id):
        try:
            barrier.wait(timeout=10)          # all 10 threads are released together
            # one plain request per thread (a requests.Session is not thread-safe)
            response = requests.post(f"{system.url}/enrollments", timeout=30,
                                     headers={"X-API-Key": "registrar-key"},
                                     json={"student_id": student_id, "course_code": "SE499"})
            results[student_id] = (response.status_code, response.json())
        except Exception as exc:              # surface thread failures in the test
            errors.append(f"{student_id}: {exc!r}")

    threads = [threading.Thread(target=enroll, args=(s,)) for s in students]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == []
    assert len(results) == 10

    # exactly 3 x 201 and 7 x 409 "course is full"
    statuses = sorted(code for code, _ in results.values())
    assert statuses == [201] * 3 + [409] * 7
    admitted = {s for s, (code, _) in results.items() if code == 201}
    refused = {s for s, (code, _) in results.items() if code == 409}
    for student_id in refused:
        assert results[student_id][1] == {"error": "course is full"}

    # the course is exactly full, never over capacity
    assert seats_taken(system, "SE499") == 3

    # outbox: 3 confirmations and 7 rejections, for exactly the right students
    outbox = system.outbox()
    confirmations = [m for m in outbox if m["type"] == "confirmation"]
    rejections = [m for m in outbox if m["type"] == "rejection"]
    assert len(outbox) == 10
    assert len(confirmations) == 3 and len(rejections) == 7
    assert {m["student_id"] for m in confirmations} == admitted
    assert {m["student_id"] for m in rejections} == refused
    assert all(m["course_code"] == "SE499" and m["fee"] == 300.0 for m in confirmations)
    assert all(m["reason"] == "course is full" for m in rejections)

    # each admitted student's schedule shows SE499
    for student_id in admitted:
        courses = system.get(f"/students/{student_id}/schedule").json()["courses"]
        assert [c["code"] for c in courses] == ["SE499"]