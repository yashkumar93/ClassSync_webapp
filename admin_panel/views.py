"""
Admin Panel views — manages users, departments, courses, sections,
timetable slots, system config, and provides the institution dashboard.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Avg, Q
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import User, Department, Course, Section, TimetableSlot, SystemConfig
from core.decorators import role_required
from absence.models import AbsenceReport, SubstitutionRecord
from attendance.models import AttendanceSession, AttendanceRecord
from assignments.models import Assignment, Submission
from notifications.models import RiskFlag
from notifications.utils import create_notification

from .forms import (
    UserCreateForm, UserEditForm, DepartmentForm, CourseForm,
    SectionForm, TimetableSlotForm, SystemConfigForm, AnnouncementForm,
)


# ---------------------------------------------------------------------------
# Admin Dashboard
# ---------------------------------------------------------------------------

@role_required("admin")
def dashboard(request):
    now = timezone.now()
    thirty_days_ago = now - timezone.timedelta(days=30)

    context = {
        "total_faculty": User.objects.filter(role="faculty", is_active=True).count(),
        "total_students": User.objects.filter(role="student", is_active=True).count(),
        "total_sections": Section.objects.count(),
        "total_courses": Course.objects.count(),
        "recent_absences": AbsenceReport.objects.select_related(
            "faculty", "timetable_slot__section__course"
        ).order_by("-created_at")[:8],
        "self_study_count": AbsenceReport.objects.filter(
            status="self_study", date__gte=thirty_days_ago.date()
        ).count(),
        "makeup_candidates": AbsenceReport.objects.filter(
            is_makeup_candidate=True, date__gte=thirty_days_ago.date()
        ).count(),
        "active_risk_flags": RiskFlag.objects.filter(resolved=False).count(),
        "risk_flags": RiskFlag.objects.filter(resolved=False).select_related(
            "student__department"
        ).order_by("-flagged_at")[:8],
        # Substitution fairness: substitution counts per faculty this month
        "substitution_counts": (
            User.objects.filter(role="faculty", is_active=True)
            .annotate(
                sub_count=Count(
                    "substitution_records",
                    filter=Q(substitution_records__timestamp__gte=thirty_days_ago),
                )
            )
            .order_by("-sub_count")[:10]
        ),
    }
    return render(request, "admin_panel/dashboard.html", context)


# ---------------------------------------------------------------------------
# User Management
# ---------------------------------------------------------------------------

@role_required("admin")
def user_list(request):
    role_filter = request.GET.get("role", "")
    dept_filter = request.GET.get("dept", "")
    search = request.GET.get("q", "")

    users = User.objects.select_related("department").order_by("role", "last_name", "first_name")

    if role_filter:
        users = users.filter(role=role_filter)
    if dept_filter:
        users = users.filter(department_id=dept_filter)
    if search:
        users = users.filter(
            Q(first_name__icontains=search) | Q(last_name__icontains=search) |
            Q(email__icontains=search) | Q(username__icontains=search)
        )

    context = {
        "users": users,
        "departments": Department.objects.all(),
        "role_filter": role_filter,
        "dept_filter": dept_filter,
        "search": search,
    }
    return render(request, "admin_panel/user_list.html", context)


@role_required("admin")
def user_create(request):
    if request.method == "POST":
        form = UserCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(request, f"User '{user.get_full_name()}' created successfully.")
            return redirect("admin_panel:user_list")
    else:
        form = UserCreateForm()
    return render(request, "admin_panel/user_form.html", {"form": form, "title": "Create User"})


@role_required("admin")
def user_edit(request, pk):
    user = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        form = UserEditForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            messages.success(request, f"User '{user.get_full_name()}' updated.")
            return redirect("admin_panel:user_list")
    else:
        form = UserEditForm(instance=user)
    return render(request, "admin_panel/user_form.html", {"form": form, "title": "Edit User", "user_obj": user})


@role_required("admin")
@require_POST
def user_toggle_active(request, pk):
    user = get_object_or_404(User, pk=pk)
    user.is_active = not user.is_active
    user.save(update_fields=["is_active"])
    status = "activated" if user.is_active else "deactivated"
    messages.success(request, f"User '{user.get_full_name()}' {status}.")
    return redirect("admin_panel:user_list")


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------

@role_required("admin")
def department_list(request):
    depts = Department.objects.annotate(
        course_count=Count("courses", distinct=True),
        member_count=Count("members", distinct=True),
    )
    return render(request, "admin_panel/department_list.html", {"departments": depts})


@role_required("admin")
def department_create(request):
    if request.method == "POST":
        form = DepartmentForm(request.POST)
        if form.is_valid():
            dept = form.save()
            messages.success(request, f"Department '{dept.name}' created.")
            return redirect("admin_panel:department_list")
    else:
        form = DepartmentForm()
    return render(request, "admin_panel/simple_form.html", {"form": form, "title": "Add Department"})


@role_required("admin")
def department_edit(request, pk):
    dept = get_object_or_404(Department, pk=pk)
    if request.method == "POST":
        form = DepartmentForm(request.POST, instance=dept)
        if form.is_valid():
            form.save()
            messages.success(request, f"Department '{dept.name}' updated.")
            return redirect("admin_panel:department_list")
    else:
        form = DepartmentForm(instance=dept)
    return render(request, "admin_panel/simple_form.html", {"form": form, "title": "Edit Department"})


# ---------------------------------------------------------------------------
# Courses
# ---------------------------------------------------------------------------

@role_required("admin")
def course_list(request):
    courses = Course.objects.select_related("department").annotate(
        section_count=Count("sections")
    )
    return render(request, "admin_panel/course_list.html", {"courses": courses})


@role_required("admin")
def course_create(request):
    if request.method == "POST":
        form = CourseForm(request.POST)
        if form.is_valid():
            course = form.save()
            messages.success(request, f"Course '{course.code}' created.")
            return redirect("admin_panel:course_list")
    else:
        form = CourseForm()
    return render(request, "admin_panel/simple_form.html", {"form": form, "title": "Add Course"})


@role_required("admin")
def course_edit(request, pk):
    course = get_object_or_404(Course, pk=pk)
    if request.method == "POST":
        form = CourseForm(request.POST, instance=course)
        if form.is_valid():
            form.save()
            messages.success(request, f"Course '{course.code}' updated.")
            return redirect("admin_panel:course_list")
    else:
        form = CourseForm(instance=course)
    return render(request, "admin_panel/simple_form.html", {"form": form, "title": "Edit Course"})


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

@role_required("admin")
def section_list(request):
    sections = Section.objects.select_related(
        "course__department", "faculty"
    ).annotate(student_count=Count("students"))
    return render(request, "admin_panel/section_list.html", {"sections": sections})


@role_required("admin")
def section_create(request):
    if request.method == "POST":
        form = SectionForm(request.POST)
        if form.is_valid():
            section = form.save()
            messages.success(request, f"Section '{section}' created.")
            return redirect("admin_panel:section_list")
    else:
        form = SectionForm()
    return render(request, "admin_panel/simple_form.html", {"form": form, "title": "Add Section"})


@role_required("admin")
def section_edit(request, pk):
    section = get_object_or_404(Section, pk=pk)
    if request.method == "POST":
        form = SectionForm(request.POST, instance=section)
        if form.is_valid():
            form.save()
            messages.success(request, f"Section '{section}' updated.")
            return redirect("admin_panel:section_list")
    else:
        form = SectionForm(instance=section)
    return render(request, "admin_panel/simple_form.html", {"form": form, "title": "Edit Section"})


# ---------------------------------------------------------------------------
# Timetable
# ---------------------------------------------------------------------------

@role_required("admin")
def timetable_list(request):
    from core.timetable_helper import build_timetable_grid

    section_id = request.GET.get("section", "")
    faculty_id = request.GET.get("faculty", "")
    view_mode = request.GET.get("view", "grid")

    slots = TimetableSlot.objects.select_related(
        "section__course", "section__faculty"
    ).order_by("day", "period_number")

    if section_id:
        slots = slots.filter(section_id=section_id)
    if faculty_id:
        slots = slots.filter(section__faculty_id=faculty_id)

    grid_data = build_timetable_grid(slots)
    faculties = User.objects.filter(role="faculty", is_active=True).order_by("first_name", "last_name")

    return render(request, "admin_panel/timetable_list.html", {
        "slots": slots,
        "sections": Section.objects.select_related("course").all(),
        "faculties": faculties,
        "section_id": section_id,
        "faculty_id": faculty_id,
        "view_mode": view_mode,
        "grid": grid_data,
        "total_slots": slots.count(),
    })


@role_required("admin")
def timetable_create(request):
    if request.method == "POST":
        form = TimetableSlotForm(request.POST)
        if form.is_valid():
            slot = form.save()
            messages.success(request, f"Timetable slot '{slot}' created.")
            return redirect("admin_panel:timetable_list")
    else:
        form = TimetableSlotForm()
    return render(request, "admin_panel/simple_form.html", {"form": form, "title": "Add Timetable Slot"})


@role_required("admin")
def timetable_edit(request, pk):
    slot = get_object_or_404(TimetableSlot, pk=pk)
    if request.method == "POST":
        form = TimetableSlotForm(request.POST, instance=slot)
        if form.is_valid():
            form.save()
            messages.success(request, "Timetable slot updated.")
            return redirect("admin_panel:timetable_list")
    else:
        form = TimetableSlotForm(instance=slot)
    return render(request, "admin_panel/simple_form.html", {"form": form, "title": "Edit Timetable Slot"})


@role_required("admin")
@require_POST
def timetable_delete(request, pk):
    slot = get_object_or_404(TimetableSlot, pk=pk)
    slot.delete()
    messages.success(request, "Timetable slot deleted.")
    return redirect("admin_panel:timetable_list")


# ---------------------------------------------------------------------------
# System Configuration
# ---------------------------------------------------------------------------

@role_required("admin")
def system_config(request):
    config = SystemConfig.get()
    if request.method == "POST":
        form = SystemConfigForm(request.POST, instance=config)
        if form.is_valid():
            form.save()
            messages.success(request, "System configuration saved.")
            return redirect("admin_panel:system_config")
    else:
        form = SystemConfigForm(instance=config)
    return render(request, "admin_panel/system_config.html", {"form": form, "config": config})


# ---------------------------------------------------------------------------
# Absence Override
# ---------------------------------------------------------------------------

@role_required("admin")
def absence_override_list(request):
    reports = AbsenceReport.objects.select_related(
        "faculty", "timetable_slot__section__course", "proposed_substitute"
    ).order_by("-created_at")
    return render(request, "admin_panel/absence_override_list.html", {"reports": reports})


@role_required("admin")
@require_POST
def absence_override_action(request, pk):
    """Admin manually reassigns or marks self-study for an absence."""
    from absence.engine import mark_self_study, confirm_substitution
    report = get_object_or_404(AbsenceReport, pk=pk)
    action = request.POST.get("action")

    if action == "self_study":
        mark_self_study(report)
        messages.success(request, "Period marked as Self-Study.")
    elif action == "reassign":
        substitute_id = request.POST.get("substitute_id")
        if substitute_id:
            try:
                sub = User.objects.get(pk=substitute_id, role="faculty")
                report.proposed_substitute = sub
                report.status = AbsenceReport.STATUS_PENDING_CONFIRMATION
                report.save()
                confirm_substitution(report)
                messages.success(request, f"Manually reassigned to {sub.get_full_name()}.")
            except User.DoesNotExist:
                messages.error(request, "Faculty member not found.")
        else:
            messages.error(request, "No substitute selected.")

    return redirect("admin_panel:absence_override_list")


# ---------------------------------------------------------------------------
# Announcements
# ---------------------------------------------------------------------------

@role_required("admin")
def send_announcement(request):
    if request.method == "POST":
        form = AnnouncementForm(request.POST)
        if form.is_valid():
            message_text = form.cleaned_data["message"]
            audience = form.cleaned_data["audience"]

            if audience == "all":
                recipients = User.objects.filter(is_active=True)
            elif audience == "faculty":
                recipients = User.objects.filter(role="faculty", is_active=True)
            elif audience == "students":
                recipients = User.objects.filter(role="student", is_active=True)
            else:
                recipients = User.objects.filter(is_active=True)

            count = 0
            for recipient in recipients:
                create_notification(
                    recipient=recipient,
                    notif_type="announcement",
                    message=message_text,
                )
                count += 1

            messages.success(request, f"Announcement sent to {count} user(s).")
            return redirect("admin_panel:dashboard")
    else:
        form = AnnouncementForm()
    return render(request, "admin_panel/announcement_form.html", {"form": form})
