"""
Attendance services: OTP generation, marking, percentage calculation,
threshold alert logic, and the faculty-resolution seam that bridges
the absence/substitution system to attendance.

All business logic lives here; views.py stays thin.
"""
import random
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from core.models import SystemConfig
from absence.models import AbsenceReport, SubstitutionRecord
from .models import AttendanceSession, AttendanceRecord, ThresholdAlert


# ---------------------------------------------------------------------------
# Faculty-Resolution Seam (depends on absence app — Phase 1)
# ---------------------------------------------------------------------------

def get_effective_faculty(timetable_slot, date):
    """
    Who is actually teaching this slot on this date — original or substitute.

    If the original faculty reported absence AND it was reassigned, the
    substitute from SubstitutionRecord is the effective faculty.
    Otherwise it's the section's assigned faculty.
    """
    absence = AbsenceReport.objects.filter(
        timetable_slot=timetable_slot,
        date=date,
        status=AbsenceReport.STATUS_REASSIGNED,
    ).first()

    if absence:
        try:
            return SubstitutionRecord.objects.get(
                absence_report=absence
            ).substitute_faculty
        except SubstitutionRecord.DoesNotExist:
            pass  # Data inconsistency — fall through to original faculty

    return timetable_slot.section.faculty


def is_self_study(timetable_slot, date):
    """True if this slot on this date has been marked as self-study."""
    return AbsenceReport.objects.filter(
        timetable_slot=timetable_slot,
        date=date,
        status=AbsenceReport.STATUS_SELF_STUDY,
    ).exists()


# ---------------------------------------------------------------------------
# OTP Generation
# ---------------------------------------------------------------------------

def generate_otp(timetable_slot, date, requesting_user, enforce_timings=False, allow_timing_override=False):
    """
    Create (or regenerate) an OTP session for a slot on a specific date.

    Guards:
    - Self-study periods cannot have attendance sessions.
    - Only the effective faculty (original or substitute) can generate.
    - Timing validation: When enforce_timings is True and user is not admin and
      allow_timing_override is False:
        - Class must be scheduled for the given date (day of week matches slot).
        - Current time must be within scheduled class timings (start_time <= now <= end_time).

    Returns the AttendanceSession instance.
    """
    if is_self_study(timetable_slot, date):
        raise ValidationError(
            "This period is self-study; no attendance session can be created."
        )

    effective_faculty = get_effective_faculty(timetable_slot, date)
    if requesting_user != effective_faculty and requesting_user.role != "admin":
        raise PermissionDenied(
            "Only the faculty currently assigned to this class or an admin can generate a code."
        )

    # Class timings validation
    if enforce_timings and requesting_user.role != "admin" and not allow_timing_override:
        now = timezone.localtime()
        today = now.date()
        now_time = now.time()

        if date != today or timetable_slot.day != date.weekday():
            raise ValidationError(
                f"This class is scheduled for {timetable_slot.get_day_display()}s. "
                f"Attendance OTP can only be generated on scheduled class days."
            )
        if now_time < timetable_slot.start_time:
            raise ValidationError(
                f"Class has not started yet. OTP can only be generated during class timings "
                f"({timetable_slot.start_time.strftime('%H:%M')} – {timetable_slot.end_time.strftime('%H:%M')})."
            )
        if now_time > timetable_slot.end_time:
            raise ValidationError(
                f"Class time has ended ({timetable_slot.end_time.strftime('%H:%M')}). "
                f"OTP can only be generated during class timings "
                f"({timetable_slot.start_time.strftime('%H:%M')} – {timetable_slot.end_time.strftime('%H:%M')})."
            )

    config = SystemConfig.get()
    code = f"{random.randint(0, 999999):06d}"

    session, _ = AttendanceSession.objects.update_or_create(
        timetable_slot=timetable_slot,
        date=date,
        defaults={
            "otp_code": code,
            "generated_by": requesting_user,
            "expires_at": timezone.now() + timedelta(
                seconds=config.otp_validity_seconds
            ),
        },
    )
    return session


# ---------------------------------------------------------------------------
# Mark Attendance
# ---------------------------------------------------------------------------

def mark_attendance(student, timetable_slot, date, entered_code):
    """
    Validate and record a student's attendance for a session.

    Checks (in order):
    1. An active session exists for this slot/date.
    2. The OTP has not expired.
    3. The entered code matches.
    4. The student is enrolled in the section.
    5. The student hasn't already marked attendance.

    After recording, runs threshold check.
    Returns the AttendanceRecord instance.
    """
    session = AttendanceSession.objects.filter(
        timetable_slot=timetable_slot, date=date
    ).first()

    if not session:
        raise ValidationError("No active attendance session for this class.")

    if timezone.now() > session.expires_at:
        raise ValidationError("This code has expired.")

    if entered_code != session.otp_code:
        raise ValidationError("Incorrect code.")

    # Enrollment check — Section.students is a ManyToMany
    if not timetable_slot.section.students.filter(pk=student.pk).exists():
        raise PermissionDenied("Not enrolled in this section.")

    record, created = AttendanceRecord.objects.get_or_create(
        session=session,
        student=student,
        defaults={"status": AttendanceRecord.STATUS_PRESENT},
    )
    if not created:
        if record.status == AttendanceRecord.STATUS_PRESENT:
            raise ValidationError("Attendance already marked for this session.")
        record.status = AttendanceRecord.STATUS_PRESENT
        record.save(update_fields=["status"])

    # Threshold check against the course (not just the section)
    check_threshold(student, timetable_slot.section.course)
    return record


# ---------------------------------------------------------------------------
# Student Absence & Presence Management
# ---------------------------------------------------------------------------

def mark_student_absent(student, session, marked_by=None, notify=True):
    """
    Mark a student absent for a given session.
    Sends alert notification to BOTH the student and the faculty.
    Runs threshold check for the student.
    """
    from notifications.utils import create_notification
    from notifications.models import Notification

    record, created = AttendanceRecord.objects.get_or_create(
        session=session,
        student=student,
        defaults={"status": AttendanceRecord.STATUS_ABSENT},
    )
    if not created and record.status != AttendanceRecord.STATUS_ABSENT:
        record.status = AttendanceRecord.STATUS_ABSENT
        record.save(update_fields=["status"])

    slot = session.timetable_slot
    course = slot.section.course
    section = slot.section
    effective_faculty = get_effective_faculty(slot, session.date) or session.generated_by

    if notify:
        # Alert notification to student
        create_notification(
            recipient=student,
            notif_type=Notification.TYPE_ATTENDANCE_ALERT,
            message=(
                f"Attendance Alert: You have been marked absent for {course.name} "
                f"(Section {section.name}) on {session.date} (Period {slot.period_number})."
            ),
            related_object_id=session.pk,
        )

        # Alert notification to faculty
        if effective_faculty:
            roll_str = f" ({student.roll_number})" if student.roll_number else ""
            create_notification(
                recipient=effective_faculty,
                notif_type=Notification.TYPE_ATTENDANCE_ALERT,
                message=(
                    f"Attendance Alert: Student {student.get_full_name()}{roll_str} "
                    f"was marked absent for {course.name} (Section {section.name}) on "
                    f"{session.date} (Period {slot.period_number})."
                ),
                related_object_id=session.pk,
            )

    check_threshold(student, course)
    return record


def mark_student_present(student, session, marked_by=None, notify=True):
    """
    Mark a student present for a given session (manual update or correction).
    Optionally notifies the student of the status update.
    Runs threshold check.
    """
    from notifications.utils import create_notification
    from notifications.models import Notification

    record, created = AttendanceRecord.objects.get_or_create(
        session=session,
        student=student,
        defaults={"status": AttendanceRecord.STATUS_PRESENT},
    )
    if not created and record.status != AttendanceRecord.STATUS_PRESENT:
        record.status = AttendanceRecord.STATUS_PRESENT
        record.save(update_fields=["status"])

    slot = session.timetable_slot
    course = slot.section.course
    section = slot.section

    if notify:
        create_notification(
            recipient=student,
            notif_type=Notification.TYPE_ATTENDANCE_ALERT,
            message=(
                f"Attendance Update: Your attendance for {course.name} "
                f"(Section {section.name}) on {session.date} (Period {slot.period_number}) "
                f"has been marked as Present."
            ),
            related_object_id=session.pk,
        )

    check_threshold(student, course)
    return record


def process_session_absences(session, triggered_by=None):
    """
    Identify all enrolled students who do NOT have a 'present' record for this session,
    mark them absent, and dispatch notifications to both the students and the faculty.
    Marks session.absences_processed = True to prevent duplicate notifications.
    """
    if session.absences_processed:
        return 0

    enrolled_students = session.timetable_slot.section.students.filter(is_active=True)
    present_student_ids = set(
        session.records.filter(status=AttendanceRecord.STATUS_PRESENT).values_list(
            "student_id", flat=True
        )
    )

    absent_students = enrolled_students.exclude(id__in=present_student_ids)
    count = 0
    for student in absent_students:
        mark_student_absent(student, session, marked_by=triggered_by, notify=True)
        count += 1

    session.absences_processed = True
    session.save(update_fields=["absences_processed"])
    return count


# ---------------------------------------------------------------------------
# Attendance Percentage (course-scoped)
# ---------------------------------------------------------------------------

def attendance_percentage(student, course):
    """
    Calculate attendance % for a student across all sections of a course
    they are enrolled in.

    Returns 100.0 if no sessions have been held yet (benefit of the doubt).
    """
    total = AttendanceSession.objects.filter(
        timetable_slot__section__course=course,
        timetable_slot__section__students=student,
    ).distinct().count()

    attended = AttendanceRecord.objects.filter(
        student=student,
        session__timetable_slot__section__course=course,
        status=AttendanceRecord.STATUS_PRESENT,
    ).count()

    return round((attended / total) * 100, 1) if total else 100.0


# ---------------------------------------------------------------------------
# Threshold Alert — resolve / reopen pattern
# ---------------------------------------------------------------------------

def check_threshold(student, course):
    """
    Check if the student's attendance in a course has crossed the threshold.

    - Below threshold + no open alert → create alert + notify.
    - Below threshold + open alert exists → do nothing (no spam).
    - At/above threshold + open alert → resolve the alert.
    """
    config = SystemConfig.get()
    pct = attendance_percentage(student, course)
    open_alert = ThresholdAlert.objects.filter(
        student=student, course=course, resolved=False
    ).first()

    if pct < config.attendance_threshold:
        if not open_alert:
            ThresholdAlert.objects.create(student=student, course=course)
            notify_threshold_alert(student, course, pct)
    else:
        if open_alert:
            open_alert.resolved = True
            open_alert.resolved_at = timezone.now()
            open_alert.save(update_fields=["resolved", "resolved_at"])


def notify_threshold_alert(student, course, pct):
    """Drop a Notification row for the student (TYPE_ATTENDANCE_ALERT)."""
    from notifications.utils import create_notification
    from core.models import SystemConfig

    config = SystemConfig.get()
    create_notification(
        recipient=student,
        notif_type="attendance_alert",
        message=(
            f"Attendance Alert: Your attendance in {course.name} ({course.code}) "
            f"has dropped to {pct:.1f}%, which is below the required {config.attendance_threshold}%. "
            f"Please attend classes regularly to avoid academic penalties."
        ),
        related_object_id=course.pk,
    )


def evaluate_all_attendance_thresholds():
    """
    Evaluate attendance thresholds across all active students and courses.
    Triggers in-app alerts whenever a student's attendance falls below 75%
    (SystemConfig.attendance_threshold) in any course where classes have been held.

    Returns a tuple of (triggered_count, resolved_count).
    """
    from core.models import User, Course
    students = User.objects.filter(role="student", is_active=True)
    triggered_count = 0
    resolved_count = 0

    for student in students:
        courses = Course.objects.filter(sections__students=student).distinct()
        for course in courses:
            total = AttendanceSession.objects.filter(
                timetable_slot__section__course=course,
                timetable_slot__section__students=student,
            ).distinct().count()
            if total == 0:
                continue

            config = SystemConfig.get()
            pct = attendance_percentage(student, course)
            open_alert = ThresholdAlert.objects.filter(
                student=student, course=course, resolved=False
            ).first()

            if pct < config.attendance_threshold:
                if not open_alert:
                    ThresholdAlert.objects.create(student=student, course=course)
                    notify_threshold_alert(student, course, pct)
                    triggered_count += 1
            else:
                if open_alert:
                    open_alert.resolved = True
                    open_alert.resolved_at = timezone.now()
                    open_alert.save(update_fields=["resolved", "resolved_at"])
                    resolved_count += 1

    return triggered_count, resolved_count
