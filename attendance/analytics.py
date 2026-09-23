"""Attendance analytics: per-student attendance percentage calculation."""
from django.db.models import Count
from core.models import Section
from .models import AttendanceSession, AttendanceRecord


def attendance_percentage(student, section):
    """
    Returns the attendance percentage for `student` in `section`.
    Formula: (sessions attended / total sessions held) * 100
    Returns 0 if no sessions have been held yet.
    """
    total_sessions = AttendanceSession.objects.filter(
        timetable_slot__section=section
    ).count()

    if total_sessions == 0:
        return 0.0

    attended = AttendanceRecord.objects.filter(
        session__timetable_slot__section=section,
        student=student,
        status=AttendanceRecord.STATUS_PRESENT,
    ).count()

    return round((attended / total_sessions) * 100, 1)


def get_student_attendance_summary(student):
    """
    Returns a list of dicts with attendance info for each section the student is enrolled in.
    Each dict: {section, total_sessions, attended, percentage, below_threshold}
    """
    from core.models import SystemConfig
    config = SystemConfig.get()
    threshold = config.attendance_threshold
    summary = []

    for section in student.enrolled_sections.select_related("course", "faculty").all():
        total = AttendanceSession.objects.filter(
            timetable_slot__section=section
        ).count()
        attended = AttendanceRecord.objects.filter(
            session__timetable_slot__section=section,
            student=student,
            status=AttendanceRecord.STATUS_PRESENT,
        ).count()
        pct = round((attended / total) * 100, 1) if total > 0 else 0.0
        summary.append({
            "section": section,
            "total_sessions": total,
            "attended": attended,
            "percentage": pct,
            "below_threshold": pct < threshold and total > 0,
        })

    return summary


def get_section_attendance_dashboard(section):
    """
    Returns a list of student attendance records for a section, used in the faculty dashboard.
    """
    from core.models import SystemConfig
    config = SystemConfig.get()
    threshold = config.attendance_threshold

    students = section.students.filter(is_active=True).order_by("last_name", "first_name")
    total_sessions = AttendanceSession.objects.filter(
        timetable_slot__section=section
    ).count()

    rows = []
    for student in students:
        attended = AttendanceRecord.objects.filter(
            session__timetable_slot__section=section,
            student=student,
            status=AttendanceRecord.STATUS_PRESENT,
        ).count()
        pct = round((attended / total_sessions) * 100, 1) if total_sessions > 0 else 0.0
        rows.append({
            "student": student,
            "attended": attended,
            "total": total_sessions,
            "percentage": pct,
            "at_risk": pct < threshold and total_sessions > 0,
        })

    return rows
