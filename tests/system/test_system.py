"""System tests (Section 5).

Each test gets its own serve.py process with a fresh database seeded as in
SPEC.md (the provided `system` fixture). Tests observe the system ONLY
through HTTP and the outbox; nothing is imported from src/.
"""

import pytest

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