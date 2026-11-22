
import sqlite3

import pytest

import db
from errors import AlreadyEnrolled, CourseFull

pytestmark = pytest.mark.integration


# -- helpers: read the database directly, independently of the repository --

def seats_taken(conn, code):
    return conn.execute(
        "SELECT seats_taken FROM courses WHERE code = ?", (code,)).fetchone()[0]


def enrollment_count(conn, student_id=None, course_code=None):
    sql = "SELECT COUNT(*) FROM enrollments WHERE 1 = 1"
    params = []
    if student_id is not None:
        sql += " AND student_id = ?"
        params.append(student_id)
    if course_code is not None:
        sql += " AND course_code = ?"
        params.append(course_code)
    return conn.execute(sql, params).fetchone()[0]


# ---------------------------------------------------------------------------
# Section 4(b): Repository + SQLite (IP4)
# ---------------------------------------------------------------------------

def test_take_seat_capacity_boundary(connection, repository):
    """REQ-2: the last seat can be taken; one more is refused and changes nothing.

    SE499 has capacity 2: seat 1 and seat 2 (the last) succeed, seat 3 fails.
    """
    with connection:
        assert repository.take_seat("SE499") is True
        assert repository.take_seat("SE499") is True   # last seat (2 of 2)
    assert seats_taken(connection, "SE499") == 2

    with connection:
        assert repository.take_seat("SE499") is False  # one past capacity
    assert seats_taken(connection, "SE499") == 2       # unchanged


def test_duplicate_enrollment_rejected_by_database(connection, repository):
    """REQ-3: the enrollments primary key rejects a second identical row."""
    with connection:
        repository.add_enrollment("ST1", "CIS350")

    with pytest.raises(sqlite3.IntegrityError):
        with connection:
            repository.add_enrollment("ST1", "CIS350")

    assert enrollment_count(connection, "ST1", "CIS350") == 1


def test_current_credits_counts_only_that_students_courses(connection, repository):
    """REQ-1: current_credits() sums only this student's courses; 0 with none.

    ST1 takes CIS350 (4) + ART101 (1) = 5; ST2 takes SE499 (3); ST3 takes nothing.
    """
    with connection:
        repository.add_enrollment("ST1", "CIS350")
        repository.add_enrollment("ST1", "ART101")
        repository.add_enrollment("ST2", "SE499")

    assert repository.current_credits("ST1") == 5   # not 8: ST2's course excluded
    assert repository.current_credits("ST2") == 3
    assert repository.current_credits("ST3") == 0   # no enrollments -> 0, not None


def test_failed_transaction_leaves_neither_seat_nor_enrollment(connection, repository, db_path):
    """REQ-2, REQ-3: a transaction that fails part-way is rolled back completely.

    The seat is taken first, then add_enrollment() fails (ST1 is already
    enrolled). The same with-block the service uses must undo the seat too.
    """
    with connection:
        repository.take_seat("CIS350")
        repository.add_enrollment("ST1", "CIS350")
    assert seats_taken(connection, "CIS350") == 1

    with pytest.raises(sqlite3.IntegrityError):
        with connection:
            assert repository.take_seat("CIS350") is True   # step 1 succeeds
            repository.add_enrollment("ST1", "CIS350")     # step 2 fails

    # Check through a SECOND connection: this is what is actually on disk.
    other = db.connect(str(db_path))
    try:
        assert seats_taken(other, "CIS350") == 1            # seat rolled back
        assert enrollment_count(other, "ST1", "CIS350") == 1
    finally:
        other.close()
        


# ---------------------------------------------------------------------------
# Section 4(c): Service + repository + notifier (IP3, IP5)
# Each test checks BOTH sides: what the database holds and what was sent.
# ---------------------------------------------------------------------------

def test_successful_enrollment_saves_seat_and_sends_confirmation(service, connection, notifier):
    """REQ-1, REQ-2, REQ-4, REQ-5: enrolled, one seat taken, confirmation with the fee.

    ST1 has completed CIS350, so SE499 (3 credits) is allowed:
    total 3 credits, fee = calculate_registration_fee(3) = 300.0.
    """
    result = service.enroll("ST1", "SE499")

    assert result == {"student_id": "ST1", "course_code": "SE499",
                      "credits": 3, "total_credits": 3, "fee": 300.0}
    # database side
    assert enrollment_count(connection, "ST1", "SE499") == 1
    assert seats_taken(connection, "SE499") == 1
    # notifier side
    assert notifier.sent == [("confirmation", "ST1", "SE499", 300.0)]


def test_full_course_raises_course_full_and_sends_rejection(service, connection, notifier):
    """REQ-2, REQ-5: a full course refuses the student, changes nothing, and says why.

    ART101 has capacity 1: ST1 takes the only seat, then ST2 is refused.
    """
    service.enroll("ST1", "ART101")

    with pytest.raises(CourseFull):
        service.enroll("ST2", "ART101")

    # database side
    assert enrollment_count(connection, "ST2", "ART101") == 0
    assert seats_taken(connection, "ART101") == 1
    # notifier side
    assert notifier.sent == [
        ("confirmation", "ST1", "ART101", 100.0),
        ("rejection", "ST2", "ART101", "course is full"),
    ]


def test_duplicate_enrollment_raises_already_enrolled_and_sends_nothing(service, connection, notifier):
    """REQ-3, REQ-5: a duplicate is refused, takes no extra seat, and sends nothing.

    The second enroll() takes a seat, then the INSERT fails; the transaction
    must roll the seat back, and no second notification may be sent.
    """
    service.enroll("ST1", "CIS350")

    with pytest.raises(AlreadyEnrolled):
        service.enroll("ST1", "CIS350")

    # database side
    assert enrollment_count(connection, "ST1", "CIS350") == 1
    assert seats_taken(connection, "CIS350") == 1        # no extra seat
    # notifier side: only the first enrollment's confirmation
    assert notifier.sent == [("confirmation", "ST1", "CIS350", 400.0)]