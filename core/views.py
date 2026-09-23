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
        from attendance.models import AttendanceSession
        from assignments.models import Assignment
        from notifications.models import Notification

        ctx = super().get_context_data(**kwargs)
        faculty = self.request.user

        # Sections this faculty teaches
        sections = faculty.teaching_sections.select_related("course").all()
        ctx["sections"] = sections

        # Pending substitute confirmations for this faculty (broadcast or direct)
        from django.db.models import Q
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
        from django.utils import timezone
        from absence.models import SubstitutionRecord, AbsenceReport
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
        from assignments.models import Assignment, Submission
        from notifications.models import Notification
        from core.models import TimetableSlot
        from django.utils import timezone

        ctx = super().get_context_data(**kwargs)
        student = self.request.user

        ctx["sections"] = student.enrolled_sections.select_related("course", "faculty").all()
        ctx["attendance_summary"] = get_student_attendance_summary(student)

        # Today's classes for student
        today = timezone.localdate()
        today_weekday = today.weekday()
        now_time = timezone.localtime(timezone.now()).time()
        ctx["today"] = today
        ctx["now_time"] = now_time
        ctx["today_classes"] = TimetableSlot.objects.filter(
            section__students=student,
            day=today_weekday,
        ).select_related("section__course", "section__faculty").order_by("period_number")

        # Upcoming assignment deadlines
        now = timezone.now()
        ctx["upcoming_assignments"] = (
            Assignment.objects.filter(
                section__in=student.enrolled_sections.all(),
                due_date__gte=now,
            )
            .exclude(submissions__student=student)
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
