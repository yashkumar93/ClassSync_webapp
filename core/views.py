"""
Core views: login, logout, role-based dashboard redirect.
"""
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.views.generic import TemplateView


class LoginView(auth_views.LoginView):
    template_name = "core/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        return reverse_lazy("core:dashboard")


class LogoutView(auth_views.LogoutView):
    next_page = "core:login"


@login_required
def dashboard_redirect(request):
    """
    After login, send users to the appropriate role dashboard.
    """
    role = request.user.role
    if role == "admin":
        return redirect("admin_panel:dashboard")
    elif role == "faculty":
        return redirect("core:faculty_dashboard")
    else:
        return redirect("core:student_dashboard")


class FacultyDashboardView(TemplateView):
    template_name = "core/faculty_dashboard.html"

    def dispatch(self, request, *args, **kwargs):
        from django.contrib.auth.decorators import login_required
        if not request.user.is_authenticated or request.user.role != "faculty":
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        from absence.models import AbsenceReport
        from attendance.models import AttendanceSession, AttendanceRecord
        from attendance.services import check_threshold
        from assignments.models import Assignment, Submission
        from assignments.services import send_assignment_reminders
        from notifications.models import Notification
        from core.models import SystemConfig
        from django.utils import timezone

        ctx = super().get_context_data(**kwargs)
        faculty = self.request.user
        config = SystemConfig.get()
        ctx["attendance_threshold"] = config.attendance_threshold

        # Sections this faculty teaches
        sections = faculty.teaching_sections.select_related("course").all()
        ctx["sections"] = sections

        # 1. Trigger threshold evaluation and assignment reminder checks
        for sec in sections.prefetch_related("students"):
            for st in sec.students.filter(is_active=True):
                check_threshold(st, sec.course)
        send_assignment_reminders(user=faculty)

        # 2. Critical Attendance Alerts (< 75%) for students in faculty's sections
        low_attendance_list = []
        for sec in sections:
            total_sessions = AttendanceSession.objects.filter(
                timetable_slot__section=sec
            ).count()
            if total_sessions == 0:
                continue
            for st in sec.students.filter(is_active=True):
                attended = AttendanceRecord.objects.filter(
                    session__timetable_slot__section=sec,
                    student=st,
                    status=AttendanceRecord.STATUS_PRESENT,
                ).count()
                pct = round((attended / total_sessions) * 100, 1)
                if pct < config.attendance_threshold:
                    low_attendance_list.append({
                        "student": st,
                        "section": sec,
                        "course": sec.course,
                        "percentage": pct,
                        "attended": attended,
                        "total_sessions": total_sessions,
                    })

        ctx["faculty_low_attendance_students"] = sorted(
            low_attendance_list, key=lambda x: x["percentage"]
        )

        # 3. Missing / Unsubmitted Assignment Alerts for Faculty
        from django.db.models import Q
        faculty_assignments = Assignment.objects.filter(
            Q(section__in=sections) | Q(created_by=faculty)
        ).distinct().select_related("section__course").order_by("due_date")

        unsubmitted_assignments_info = []
        for a in faculty_assignments:
            submitted_ids = a.submissions.values_list("student_id", flat=True)
            missing_students = a.section.students.filter(
                is_active=True
            ).exclude(id__in=submitted_ids).order_by("last_name", "first_name")
            missing_count = missing_students.count()
            total_count = a.section.students.filter(is_active=True).count()
            if missing_count > 0:
                unsubmitted_assignments_info.append({
                    "assignment": a,
                    "course": a.section.course,
                    "section": a.section,
                    "is_past_due": a.is_past_due,
                    "missing_count": missing_count,
                    "total_count": total_count,
                    "missing_students": list(missing_students[:5]),
                    "more_count": max(0, missing_count - 5),
                })

        ctx["faculty_unsubmitted_assignments"] = unsubmitted_assignments_info

        # Pending substitute confirmations for this faculty (broadcast or direct)
        ctx["pending_confirmations"] = (
            AbsenceReport.objects.filter(
                status=AbsenceReport.STATUS_PENDING_CONFIRMATION,
            )
            .filter(
                Q(notified_substitutes=faculty) | Q(proposed_substitute=faculty)
            )
            .exclude(
                declined_substitutes=faculty
            )
            .distinct()
            .select_related("faculty", "timetable_slot__section__course")
        )

        # Recent absences reported by this faculty
        ctx["recent_absences"] = AbsenceReport.objects.filter(
            faculty=faculty
        ).order_by("-created_at")[:5]

        # Upcoming sessions (today's timetable slots + any substitute classes)
        from absence.models import SubstitutionRecord
        today = timezone.localdate()
        today_weekday = today.weekday()
        ctx["today"] = today

        # Exclude regular slots where this faculty has reported absence and it got reassigned
        reassigned_absence_slot_ids = AbsenceReport.objects.filter(
            faculty=faculty,
            date=today,
            status=AbsenceReport.STATUS_REASSIGNED,
        ).values_list("timetable_slot_id", flat=True)

        regular_slots = list(
            sections.filter(
                timetable_slots__day=today_weekday
            ).exclude(
                timetable_slots__id__in=reassigned_absence_slot_ids
            ).values(
                "course__code",
                "course__name",
                "name",
                "room",
                "timetable_slots__start_time",
                "timetable_slots__end_time",
                "timetable_slots__period_number",
                "timetable_slots__id",
            )
        )
        for s in regular_slots:
            s["is_substitute"] = False

        # Substitute classes assigned to this faculty today
        sub_records = SubstitutionRecord.objects.filter(
            substitute_faculty=faculty,
            absence_report__date=today,
        ).select_related("absence_report__timetable_slot__section__course")

        substitute_slots = []
        for sub in sub_records:
            ts = sub.absence_report.timetable_slot
            substitute_slots.append({
                "course__code": ts.section.course.code,
                "course__name": ts.section.course.name,
                "name": ts.section.name,
                "room": ts.section.room,
                "timetable_slots__start_time": ts.start_time,
                "timetable_slots__end_time": ts.end_time,
                "timetable_slots__period_number": ts.period_number,
                "timetable_slots__id": ts.id,
                "is_substitute": True,
            })

        ctx["today_slots"] = sorted(
            regular_slots + substitute_slots,
            key=lambda x: x["timetable_slots__period_number"]
        )

        now = timezone.localtime()
        ctx["now_time"] = now.time()

        ctx["unread_notifications"] = Notification.objects.filter(
            recipient=faculty, read_at__isnull=True
        ).count()

        return ctx



class StudentDashboardView(TemplateView):
    template_name = "core/student_dashboard.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated or request.user.role != "student":
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        from attendance.analytics import get_student_attendance_summary
        from attendance.services import check_threshold
        from assignments.models import Assignment, Submission
        from assignments.services import send_assignment_reminders
        from notifications.models import Notification
        from core.models import TimetableSlot, SystemConfig
        from django.utils import timezone

        ctx = super().get_context_data(**kwargs)
        student = self.request.user
        config = SystemConfig.get()
        ctx["attendance_threshold"] = config.attendance_threshold

        # 1. Trigger threshold evaluation and assignment reminder checks
        for sec in student.enrolled_sections.select_related("course").all():
            check_threshold(student, sec.course)
        send_assignment_reminders(user=student)

        # 2. Sections and Attendance Summary
        ctx["sections"] = student.enrolled_sections.select_related("course", "faculty").all()
        attendance_summary = get_student_attendance_summary(student)
        ctx["attendance_summary"] = attendance_summary

        # Identify courses below threshold (< 75%)
        ctx["low_attendance_alerts"] = [r for r in attendance_summary if r.get("below_threshold")]

        # 3. Today's classes for student
        today = timezone.localdate()
        today_weekday = today.weekday()
        now_time = timezone.localtime(timezone.now()).time()
        ctx["today"] = today
        ctx["now_time"] = now_time
        ctx["today_classes"] = TimetableSlot.objects.filter(
            section__students=student,
            day=today_weekday,
        ).select_related("section__course", "section__faculty").order_by("period_number")

        # 4. Unsubmitted / Missing Assignment Alerts
        now = timezone.now()
        submitted_assignment_ids = Submission.objects.filter(
            student=student
        ).values_list("assignment_id", flat=True)

        unsubmitted_assignments = Assignment.objects.filter(
            section__students=student
        ).exclude(
            id__in=submitted_assignment_ids
        ).select_related("section__course", "section__faculty").order_by("due_date")

        ctx["missing_assignment_alerts"] = list(unsubmitted_assignments)
        ctx["overdue_assignments"] = [a for a in unsubmitted_assignments if a.is_past_due]

        # Upcoming assignment deadlines (unsubmitted + future due date)
        ctx["upcoming_assignments"] = (
            Assignment.objects.filter(
                section__in=student.enrolled_sections.all(),
                due_date__gte=now,
            )
            .exclude(id__in=submitted_assignment_ids)
            .order_by("due_date")[:5]
        )

        ctx["unread_notifications"] = Notification.objects.filter(
            recipient=student, read_at__isnull=True
        ).count()

        return ctx


@login_required
def faculty_timetable(request):
    """Weekly teaching schedule view for faculty."""
    if request.user.role != "faculty" and request.user.role != "admin":
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

    from core.models import TimetableSlot
    from core.timetable_helper import build_timetable_grid
    from django.shortcuts import render

    faculty = request.user
    slots = TimetableSlot.objects.filter(
        section__faculty=faculty
    ).select_related("section__course", "section__faculty").order_by("day", "period_number")

    grid_data = build_timetable_grid(slots)

    return render(request, "core/faculty_timetable.html", {
        "grid": grid_data,
        "slots": slots,
        "faculty": faculty,
        "total_classes": slots.count(),
    })


@login_required
def student_timetable(request):
    """Weekly academic schedule view for students."""
    if request.user.role != "student" and request.user.role != "admin":
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

    from core.models import TimetableSlot
    from core.timetable_helper import build_timetable_grid
    from django.shortcuts import render

    student = request.user
    slots = TimetableSlot.objects.filter(
        section__students=student
    ).select_related("section__course", "section__faculty").order_by("day", "period_number")

    grid_data = build_timetable_grid(slots)

    return render(request, "core/student_timetable.html", {
        "grid": grid_data,
        "slots": slots,
        "student": student,
        "total_classes": slots.count(),
    })
