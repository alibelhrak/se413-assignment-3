import pytest

import db
from repository import RegistrationRepository
from service import RegistrationService


@pytest.fixture
def db_path(tmp_path):
    """Path of a new database file, unique to each test."""
    return tmp_path / "registration.db"


@pytest.fixture
def connection(db_path):
    """A connection to a new database with the schema and the test data."""
    conn = db.connect(str(db_path))
    db.create_schema(conn)
    repo = RegistrationRepository(conn)
    with conn:  # the repository never commits; commit the set-up here
        repo.add_course("CIS350", "Intro to Software Engineering", 4, 10)
        repo.add_course("SE499", "Capstone Seminar", 3, 2, prerequisite="CIS350")
        repo.add_course("ART101", "Drawing Studio", 1, 1)
        for student_id in ("ST1", "ST2", "ST3"):
            repo.add_student(student_id, f"Student {student_id}")
        repo.add_completed("ST1", "CIS350")
        repo.add_completed("ST2", "CIS350")
    yield conn
    conn.close()


@pytest.fixture
def repository(connection):
    """The real RegistrationRepository on the test database."""
    return RegistrationRepository(connection)


class FakeNotifier:
    """Test double for the notifier (IP5): records every call in a list."""

    def __init__(self):
        self.sent = []

    def send_confirmation(self, student_id, course_code, fee):
        self.sent.append(("confirmation", student_id, course_code, fee))

    def send_rejection(self, student_id, course_code, reason):
        self.sent.append(("rejection", student_id, course_code, reason))


@pytest.fixture
def notifier():
    """A fresh FakeNotifier for each test."""
    return FakeNotifier()


@pytest.fixture
def service(connection, repository, notifier):
    """The real RegistrationService wired to the real repository and the fake notifier."""
    return RegistrationService(connection, repository, notifier)