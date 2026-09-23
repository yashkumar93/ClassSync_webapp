"""Test absence notifications for both student and faculty."""
import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'classsync.settings'
import django
django.setup()

from django.conf import settings
settings.ALLOWED_HOSTS.append('testserver')

from django.test import Client
from django.utils import timezone
from datetime import timedelta

from core.models import User, Section, TimetableSlot
from attendance.models import AttendanceSession, AttendanceRecord
from notifications.models import Notification
from attendance.services import (
    generate_otp, mark_attendance, mark_student_absent,
    mark_student_present, process_session_absences, get_effective_faculty
)

errors = []

def assert_true(label, val):
    if val:
        print(f"  OK  {label}")
    else:
        print(f"  FAIL  {label}")
        errors.append(label)

def assert_eq(label, actual, expected):
    if actual == expected:
        print(f"  OK  {label}")
    else:
        print(f"  FAIL  {label}: expected {expected}, got {actual}")
        errors.append(f"{label}: expected {expected}, got {actual}")

print("=== Test: Student Absence Notification to Student & Faculty ===")

today = timezone.localdate()
slot = TimetableSlot.objects.first()
section = slot.section
faculty = get_effective_faculty(slot, today)
assert_true("Faculty found", faculty is not None)

# Clean up any existing session for this test
AttendanceSession.objects.filter(timetable_slot=slot, date=today).delete()

# Create session
session = generate_otp(slot, today, faculty)
assert_true("Session created", session is not None)

enrolled_students = list(section.students.filter(is_active=True))
assert_true("Has enrolled students", len(enrolled_students) >= 2)

# Student 0 marks attendance with OTP
s0 = enrolled_students[0]
record0 = mark_attendance(s0, slot, today, session.otp_code)
assert_eq("Student 0 status is present", record0.status, AttendanceRecord.STATUS_PRESENT)

# Clear notifications before processing absences
Notification.objects.filter(related_object_id=session.pk).delete()

# Process absences for the session
absent_count = process_session_absences(session, triggered_by=faculty)
print(f"  Processed {absent_count} absences for session.")
assert_eq("Processed count matches unmarked", absent_count, len(enrolled_students) - 1)

# Check student notifications: every absent student must have an attendance_alert notification
for student in enrolled_students[1:]:
    notif = Notification.objects.filter(
        recipient=student,
        notif_type="attendance_alert",
        related_object_id=session.pk,
    ).first()
    assert_true(f"Absent student {student.username} received alert", notif is not None)
    if notif:
        assert_true(f"Message mentions absent for {student.username}", "absent" in notif.message.lower())

# Present student s0 must NOT have an absence notification for this session
s0_notif = Notification.objects.filter(
    recipient=s0,
    notif_type="attendance_alert",
    related_object_id=session.pk,
    message__icontains="marked absent"
).first()
assert_true("Present student did NOT receive absent alert", s0_notif is None)

# Faculty must receive attendance_alert notification(s) for the absent students
faculty_notifs = Notification.objects.filter(
    recipient=faculty,
    notif_type="attendance_alert",
    related_object_id=session.pk,
)
assert_true("Faculty received absence alert notifications", faculty_notifs.exists())
print(f"  Faculty received {faculty_notifs.count()} absence notification(s).")
first_fac_notif = faculty_notifs.first()
print(f"  Sample faculty notification: {first_fac_notif.message}")

# === Test Manual Toggle via Service ===
print("\n=== Test: Manual Toggle Marking Absent & Present ===")
# Clear session notifications
Notification.objects.filter(related_object_id=session.pk).delete()

# Faculty marks s0 absent
mark_student_absent(s0, session, marked_by=faculty, notify=True)
s0_rec = AttendanceRecord.objects.get(session=session, student=s0)
assert_eq("s0 status changed to absent", s0_rec.status, AttendanceRecord.STATUS_ABSENT)

# Verify s0 got alert
s0_alert = Notification.objects.filter(
    recipient=s0,
    notif_type="attendance_alert",
    related_object_id=session.pk,
    message__icontains="marked absent"
).first()
assert_true("s0 received alert when marked absent", s0_alert is not None)

# Verify faculty got alert
fac_alert = Notification.objects.filter(
    recipient=faculty,
    notif_type="attendance_alert",
    related_object_id=session.pk,
    message__icontains=s0.get_full_name()
).first()
assert_true("Faculty received alert when s0 marked absent", fac_alert is not None)

# Faculty marks s0 back to present
mark_student_present(s0, session, marked_by=faculty, notify=True)
s0_rec.refresh_from_db()
assert_eq("s0 status restored to present", s0_rec.status, AttendanceRecord.STATUS_PRESENT)

# === Test HTTP Endpoint for Toggle & Finalize ===
print("\n=== Test: HTTP Views (session_roster, toggle, finalize) ===")
c = Client()
c.post('/login/', {'username': faculty.username, 'password': 'Faculty@1234'})

# View session roster
r = c.get(f'/attendance/session/{session.pk}/roster/')
assert_eq("Session roster HTTP 200", r.status_code, 200)

# Toggle student 1 via HTTP
s1 = enrolled_students[1]
r = c.post(f'/attendance/session/{session.pk}/toggle/{s1.pk}/', {'target_status': 'present'})
assert_eq("Toggle HTTP redirect", r.status_code, 302)
s1_rec = AttendanceRecord.objects.get(session=session, student=s1)
assert_eq("s1 is now present via HTTP toggle", s1_rec.status, AttendanceRecord.STATUS_PRESENT)

# Toggle student 1 back to absent via HTTP
r = c.post(f'/attendance/session/{session.pk}/toggle/{s1.pk}/', {'target_status': 'absent'})
assert_eq("Toggle back HTTP redirect", r.status_code, 302)
s1_rec.refresh_from_db()
assert_eq("s1 is now absent via HTTP toggle", s1_rec.status, AttendanceRecord.STATUS_ABSENT)

# Finalize session via HTTP
r = c.post(f'/attendance/session/{session.pk}/finalize/')
assert_eq("Finalize HTTP redirect", r.status_code, 302)

print("\n" + "=" * 50)
if errors:
    print(f"FAILURES: {len(errors)}")
    for e in errors:
        print(f"  - {e}")
else:
    print("ALL ABSENCE NOTIFICATION TESTS PASSED SUCCESSFULLY!")
