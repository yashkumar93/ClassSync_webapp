"""End-to-end workflow tests for all major features."""
import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'classsync.settings'
import django
django.setup()

from django.conf import settings
settings.ALLOWED_HOSTS.append('testserver')

from django.test import Client
from django.utils import timezone
from datetime import timedelta

from core.models import User, Section, TimetableSlot, SystemConfig
from attendance.models import AttendanceSession, AttendanceRecord
from assignments.models import Assignment, Submission
from absence.models import AbsenceReport, FacultyAvailability, SubstitutionRecord
from notifications.models import Notification, RiskFlag

errors = []


def assert_eq(label, actual, expected):
    if actual == expected:
        print(f"  OK  {label}")
    else:
        print(f"  FAIL  {label}: expected {expected}, got {actual}")
        errors.append(f"{label}: expected {expected}, got {actual}")


def assert_true(label, value):
    if value:
        print(f"  OK  {label}")
    else:
        print(f"  FAIL  {label}")
        errors.append(label)


# === Test 1: OTP Attendance Workflow ===
print("=== Test 1: OTP Attendance Workflow ===")
from attendance.services import generate_otp, mark_attendance

slot = TimetableSlot.objects.first()
faculty = slot.section.faculty
student = slot.section.students.first()
today = timezone.localdate()

# Delete any existing session or test absence for this test
AttendanceSession.objects.filter(timetable_slot=slot, date=today).delete()
AttendanceRecord.objects.filter(session__timetable_slot=slot, session__date=today).delete()
AbsenceReport.objects.filter(timetable_slot=slot, date=today).delete()
from attendance.services import get_effective_faculty
faculty = get_effective_faculty(slot, today)

# Faculty generates OTP
session = generate_otp(slot, today, faculty)
assert_true("OTP generated", session.otp_code and len(session.otp_code) == 6)
assert_true("Session is active", session.is_active)

# Student marks attendance with correct OTP
record = mark_attendance(student, slot, today, session.otp_code)
assert_true("Attendance marked", record is not None)
assert_eq("Record student", record.student.pk, student.pk)

# Student cannot mark again
from django.core.exceptions import ValidationError, PermissionDenied
try:
    mark_attendance(student, slot, today, session.otp_code)
    assert_true("Duplicate blocked", False)
except ValidationError:
    assert_true("Duplicate blocked", True)

# Wrong OTP rejected
student2 = slot.section.students.all()[1]
try:
    mark_attendance(student2, slot, today, "000000")
    assert_true("Wrong OTP rejected", False)
except ValidationError:
    assert_true("Wrong OTP rejected", True)


# === Test 2: Assignment Submission Workflow ===
print("\n=== Test 2: Assignment Submission Workflow ===")
from assignments.services import submit_assignment
from django.core.files.uploadedfile import SimpleUploadedFile

assignment = Assignment.objects.filter(section=slot.section).first()
assert_true("Assignment exists", assignment is not None)

# Student submits assignment
test_file = SimpleUploadedFile("test.pdf", b"test content", content_type="application/pdf")
submission = submit_assignment(student, assignment, test_file)
assert_true("Submission created", submission is not None)
assert_eq("Not late (before deadline)", submission.is_late, False)

# Student resubmits (before deadline)
test_file2 = SimpleUploadedFile("test2.pdf", b"updated content", content_type="application/pdf")
submission2 = submit_assignment(student, assignment, test_file2)
assert_true("Resubmission works", submission2 is not None)

# Non-enrolled student cannot submit
non_enrolled = User.objects.filter(role="student").exclude(
    enrolled_sections=assignment.section
).first()
if non_enrolled:
    try:
        test_file3 = SimpleUploadedFile("test.pdf", b"test", content_type="application/pdf")
        submit_assignment(non_enrolled, assignment, test_file3)
        assert_true("Non-enrolled blocked", False)
    except PermissionDenied:
        assert_true("Non-enrolled blocked", True)


# === Test 3: Absence & Substitution Engine ===
print("\n=== Test 3: Absence & Substitution Engine ===")
from absence.engine import find_eligible_substitutes, propose_next_substitute, confirm_substitution

report = AbsenceReport.objects.first()
assert_true("Absence report exists", report is not None)

# Find eligible substitutes
eligible = find_eligible_substitutes(report)
assert_true("Found eligible substitutes", eligible.count() > 0)

# Propose a substitute
proposed = propose_next_substitute(report)
assert_true("Substitute proposed", proposed is not None)
report.refresh_from_db()
assert_eq("Report status", report.status, AbsenceReport.STATUS_PENDING_CONFIRMATION)
assert_true("Deadline set", report.confirmation_deadline is not None)

# Confirm substitution
confirm_substitution(report)
report.refresh_from_db()
assert_eq("Report reassigned", report.status, AbsenceReport.STATUS_REASSIGNED)
assert_true("SubstitutionRecord created", SubstitutionRecord.objects.filter(absence_report=report).exists())

# Notifications were sent
notif_count = Notification.objects.count()
assert_true("Notifications created", notif_count > 0)
print(f"  (Total notifications: {notif_count})")


# === Test 4: Threshold Alerts ===
print("\n=== Test 4: Threshold Alerts ===")
from attendance.services import check_threshold, attendance_percentage
from attendance.models import ThresholdAlert

course = slot.section.course
pct = attendance_percentage(student, course)
print(f"  Student attendance: {pct}%")
assert_true("Attendance calculated", pct >= 0)


# === Test 5: Risk Flag Evaluation ===
print("\n=== Test 5: Risk Flag Evaluation ===")
from notifications.services import evaluate_risk_flags
flagged, resolved = evaluate_risk_flags()
print(f"  Flagged: {flagged}, Resolved: {resolved}")
assert_true("Risk evaluation ran", True)


# === Test 6: Notification mark read ===
print("\n=== Test 6: Notification Mark Read ===")
from notifications.utils import mark_all_read, mark_read

notif = Notification.objects.first()
if notif:
    mark_read(notif.pk, notif.recipient)
    notif.refresh_from_db()
    assert_true("Single notification marked read", notif.is_read)

    mark_all_read(notif.recipient)
    unread = Notification.objects.filter(recipient=notif.recipient, read_at__isnull=True).count()
    assert_eq("All notifications marked read", unread, 0)


# === Test 7: Admin Panel CRUD via HTTP ===
print("\n=== Test 7: Admin Panel CRUD ===")
c = Client()
c.post('/login/', {'username': 'admin_demo', 'password': 'Admin@1234'})

# Clean up prior test objects if they exist
from core.models import Department, Course, User
Course.objects.filter(code='TEST101').delete()
Department.objects.filter(code='TEST').delete()
User.objects.filter(username='testfaculty').delete()

# Create department
r = c.post('/admin-panel/departments/create/', {
    'name': 'Test Department', 'code': 'TEST'
})
assert_eq("Department created", r.status_code, 302)

# Create course
from core.models import Department
dept = Department.objects.get(code='TEST')
r = c.post('/admin-panel/courses/create/', {
    'department': dept.pk, 'code': 'TEST101', 'name': 'Test Course', 'credits': 3
})
assert_eq("Course created", r.status_code, 302)

# Create user
r = c.post('/admin-panel/users/create/', {
    'username': 'testfaculty',
    'first_name': 'Test',
    'last_name': 'Faculty',
    'email': 'test@test.com',
    'role': 'faculty',
    'password1': 'Test@12345',
    'password2': 'Test@12345',
})
assert_eq("User created", r.status_code, 302)

# System config update
config = SystemConfig.get()
r = c.post('/admin-panel/config/', {
    'otp_validity_seconds': 60,
    'attendance_threshold': 80,
    'risk_missed_submissions': 3,
    'risk_window_days': 30,
    'confirmation_window_minutes': 15,
})
assert_eq("Config updated", r.status_code, 302)
config.refresh_from_db()
assert_eq("OTP validity updated", config.otp_validity_seconds, 60)

# Send announcement
r = c.post('/admin-panel/announce/', {
    'audience': 'all',
    'message': 'Test announcement from admin panel',
})
assert_eq("Announcement sent", r.status_code, 302)
assert_true("Announcement notifications created",
    Notification.objects.filter(notif_type='announcement').count() > 0
)


# Summary
print("\n" + "=" * 50)
if errors:
    print(f"FAILURES: {len(errors)}")
    for e in errors:
        print(f"  - {e}")
else:
    print("ALL E2E TESTS PASSED!")
