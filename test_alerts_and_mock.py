"""
Verification suite for attendance (<75%) and assignment submission deadline alerts.
"""
import os
os.environ['DJANGO_SETTINGS_MODULE'] = 'classsync.settings'
import django
django.setup()

from datetime import timedelta
from django.utils import timezone
from core.models import User, Course, Section, TimetableSlot, SystemConfig
from attendance.models import AttendanceSession, AttendanceRecord, ThresholdAlert
from attendance.services import attendance_percentage, evaluate_all_attendance_thresholds, check_threshold
from assignments.models import Assignment, Submission, ReminderLog
from assignments.services import send_assignment_reminders
from notifications.models import Notification

errors = []

def assert_true(label, condition):
    if condition:
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

print("=== Testing Attendance Mock Data & Alerts (<75%) ===")

config = SystemConfig.get()
assert_eq("SystemConfig threshold is 75%", config.attendance_threshold, 75)

s_low = User.objects.filter(username="23BCS1004").first()
assert_true("Student 23BCS1004 exists", s_low is not None)

cs401 = Course.objects.filter(code="CS401").first()
assert_true("Course CS401 exists", cs401 is not None)

pct_cs401 = attendance_percentage(s_low, cs401)
print(f"  23BCS1004 CS401 Attendance: {pct_cs401}%")
assert_true("23BCS1004 attendance in CS401 is < 75%", pct_cs401 < 75.0)

alert_cs401 = ThresholdAlert.objects.filter(student=s_low, course=cs401, resolved=False).first()
assert_true("ThresholdAlert exists for 23BCS1004 in CS401", alert_cs401 is not None)

notif_att = Notification.objects.filter(
    recipient=s_low,
    notif_type=Notification.TYPE_ATTENDANCE_ALERT,
    message__icontains="CS401"
).first()
assert_true("Attendance alert notification received by 23BCS1004 for CS401", notif_att is not None)
if notif_att:
    print(f"  Sample notification: {notif_att.message}")

s_normal = User.objects.filter(username="23BCS1001").first()
pct_normal = attendance_percentage(s_normal, cs401)
print(f"  23BCS1001 CS401 Attendance: {pct_normal}%")
assert_true("23BCS1001 attendance is >= 75%", pct_normal >= 75.0)
alert_normal = ThresholdAlert.objects.filter(student=s_normal, course=cs401, resolved=False).first()
assert_true("No active ThresholdAlert for 23BCS1001 in CS401", alert_normal is None)


print("\n=== Testing Assignment Due Tomorrow Mock Data & Alerts ===")

local_now = timezone.localtime(timezone.now())
tomorrow_date = local_now.date() + timedelta(days=1)

a1 = Assignment.objects.filter(section__course=cs401, title__icontains="Cloud Architecture").first()
assert_true("Assignment 1 (CS401) exists", a1 is not None)
assign_local = timezone.localtime(a1.due_date)
assert_eq("Assignment 1 is due tomorrow", assign_local.date(), tomorrow_date)

# Student 23BCS1001 submitted
assert_true(
    "23BCS1001 submitted Assignment 1",
    Submission.objects.filter(assignment=a1, student=s_normal).exists()
)

# Student 23BCS1003 did NOT submit
s_pending = User.objects.filter(username="23BCS1003").first()
assert_true(
    "23BCS1003 has NOT submitted Assignment 1",
    not Submission.objects.filter(assignment=a1, student=s_pending).exists()
)

# 23BCS1003 received reminder alert
notif_student_remind = Notification.objects.filter(
    recipient=s_pending,
    notif_type=Notification.TYPE_ASSIGNMENT_REMINDER,
    related_object_id=a1.pk
).first()
assert_true("23BCS1003 received assignment deadline reminder", notif_student_remind is not None)
if notif_student_remind:
    print(f"  Student alert: {notif_student_remind.message}")

# Concerned faculty received reminder alert for 23BCS1003
faculty_naseer = a1.section.faculty
notif_faculty_alert = Notification.objects.filter(
    recipient=faculty_naseer,
    notif_type=Notification.TYPE_ASSIGNMENT_REMINDER,
    related_object_id=a1.pk,
    message__icontains="23BCS1003"
).first()
assert_true("Faculty received pending submission alert for 23BCS1003", notif_faculty_alert is not None)
if notif_faculty_alert:
    print(f"  Faculty alert: {notif_faculty_alert.message}")

# Deduplication check
remind_count_before = ReminderLog.objects.count()
notifications_count_before = Notification.objects.count()
sent_again = send_assignment_reminders()
assert_eq("Re-running send_assignment_reminders sends 0 duplicate reminders", sent_again, 0)
assert_eq("ReminderLog count unchanged", ReminderLog.objects.count(), remind_count_before)
assert_eq("Notification count unchanged", Notification.objects.count(), notifications_count_before)


print("\n=== Testing Attendance Auto-Resolution ===")
# If student attendance improves to >= 75%, alert resolves
from attendance.models import AttendanceSession, AttendanceRecord
# Add present records for s_low until >= 75%
sessions_cs401 = AttendanceSession.objects.filter(timetable_slot__section__course=cs401)
absent_records = AttendanceRecord.objects.filter(session__in=sessions_cs401, student=s_low, status="absent")
original_absent_pks = list(absent_records.values_list("pk", flat=True))

# Turn them to present temporarily
AttendanceRecord.objects.filter(pk__in=original_absent_pks).update(status="present")
check_threshold(s_low, cs401)
alert_resolved = ThresholdAlert.objects.filter(student=s_low, course=cs401).first()
assert_true("ThresholdAlert auto-resolved when attendance reaches >= 75%", alert_resolved.resolved)

# Restore absent status to maintain mock data state
AttendanceRecord.objects.filter(pk__in=original_absent_pks).update(status="absent")
check_threshold(s_low, cs401)
alert_reopened = ThresholdAlert.objects.filter(student=s_low, course=cs401, resolved=False).first()
assert_true("ThresholdAlert reopened when attendance dropped back < 75%", alert_reopened is not None)


print("\n" + "=" * 50)
if errors:
    print(f"FAILURES: {len(errors)}")
    for e in errors:
        print(f"  - {e}")
    exit(1)
else:
    print("ALL TESTS PASSED SUCCESSFULLY!")
