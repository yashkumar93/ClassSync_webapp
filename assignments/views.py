"""
Assignments app views — thin wrappers around services.py.

Faculty:
  post_assignment          — create assignment for a section
  faculty_assignment_list  — list all assignments for taught sections
  submission_dashboard     — per-student submission status table

Student:
  student_assignment_list  — all assignments across enrolled sections
  submit_assignment        — file upload (with resubmission support)
"""
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from core.decorators import role_required
from core.models import Section
from .models import Assignment, Submission
from .forms import AssignmentForm, SubmissionForm
from . import services


# ---------------------------------------------------------------------------
# Faculty: Post Assignment
# ---------------------------------------------------------------------------

@role_required("faculty")
def post_assignment(request):
    if request.method == "POST":
        form = AssignmentForm(request.POST, request.FILES, faculty=request.user)
        if form.is_valid():
            assignment = form.save(commit=False)
            assignment.created_by = request.user
            assignment.save()
            _notify_new_assignment(assignment)
            messages.success(request, f"Assignment '{assignment.title}' posted.")
            return redirect("assignments:faculty_list")
    else:
        form = AssignmentForm(faculty=request.user)
    return render(request, "assignments/post_assignment.html", {"form": form})


# ---------------------------------------------------------------------------
# Faculty: Assignment List
# ---------------------------------------------------------------------------

@role_required("faculty")
def faculty_assignment_list(request):
    sections = request.user.teaching_sections.all()
    assignments = Assignment.objects.filter(
        section__in=sections
    ).select_related("section__course").order_by("due_date")
    now = timezone.now()
    local_now = timezone.localtime(now)
    tomorrow_date = local_now.date() + timezone.timedelta(days=1)

    enriched = []
    for a in assignments:
        assign_local = timezone.localtime(a.due_date)
        is_due_tomorrow = not a.is_past_due and (
            assign_local.date() == tomorrow_date or (a.due_date > now and a.due_date <= now + timezone.timedelta(days=1, hours=2))
        )
        total_students = a.section.students.filter(is_active=True).count()
        submitted_count = a.submissions.count()
        missing_count = max(0, total_students - submitted_count)
        enriched.append({
            "assignment": a,
            "is_past_due": a.is_past_due,
            "is_due_tomorrow": is_due_tomorrow,
            "total_students": total_students,
            "submitted_count": submitted_count,
            "missing_count": missing_count,
        })
    return render(request, "assignments/faculty_list.html", {"assignments": enriched})


# ---------------------------------------------------------------------------
# Faculty: Submission Dashboard
# ---------------------------------------------------------------------------

@role_required("faculty", "admin")
def submission_dashboard(request, assignment_id):
    assignment = get_object_or_404(Assignment, pk=assignment_id)

    # Faculty can only see their own assignments (admins can see all)
    if request.user.role == "faculty" and assignment.created_by != request.user:
        raise PermissionDenied

    submitted = Submission.objects.filter(
        assignment=assignment
    ).select_related("student").order_by("student__last_name", "student__first_name")
    submitted_ids = submitted.values_list("student_id", flat=True)
    missing_students = assignment.section.students.filter(
        is_active=True
    ).exclude(id__in=submitted_ids).order_by("last_name", "first_name")

    # Separate late vs on-time submissions
    on_time = [s for s in submitted if not s.is_late]
    late = [s for s in submitted if s.is_late]

    return render(request, "assignments/submission_dashboard.html", {
        "assignment": assignment,
        "submitted": submitted,
        "on_time": on_time,
        "late": late,
        "missing_students": missing_students,
    })


# ---------------------------------------------------------------------------
# Student: Assignment List
# ---------------------------------------------------------------------------

@role_required("student")
def student_assignment_list(request):
    sections = request.user.enrolled_sections.all()
    now = timezone.now()
    local_now = timezone.localtime(now)
    tomorrow_date = local_now.date() + timezone.timedelta(days=1)
    assignments = Assignment.objects.filter(
        section__in=sections
    ).select_related("section__course").order_by("due_date")

    enriched = []
    for a in assignments:
        submission = Submission.objects.filter(
            assignment=a, student=request.user
        ).first()
        assign_local = timezone.localtime(a.due_date)
        is_due_tomorrow = not (now > a.due_date) and (
            assign_local.date() == tomorrow_date or (a.due_date > now and a.due_date <= now + timezone.timedelta(days=1, hours=2))
        )
        enriched.append({
            "assignment": a,
            "submission": submission,
            "is_past_due": now > a.due_date,
            "is_due_tomorrow": is_due_tomorrow,
            "can_resubmit": submission is not None and not (now > a.due_date),
        })

    return render(request, "assignments/student_list.html", {"assignments": enriched})


# ---------------------------------------------------------------------------
# Student: Submit Assignment (with resubmission support)
# ---------------------------------------------------------------------------

@role_required("student")
def submit_assignment(request, assignment_id):
    assignment = get_object_or_404(Assignment, pk=assignment_id)
    existing = Submission.objects.filter(
        assignment=assignment, student=request.user
    ).first()

    # Enrollment check
    if not request.user.enrolled_sections.filter(pk=assignment.section.pk).exists():
        messages.error(request, "You are not enrolled in this course.")
        return redirect("assignments:student_list")

    # Post-deadline resubmission block
    if existing and assignment.is_past_due:
        messages.error(
            request,
            "Deadline has passed — this submission can no longer be changed."
        )
        return redirect("assignments:student_list")

    if request.method == "POST":
        form = SubmissionForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                submission = services.submit_assignment(
                    student=request.user,
                    assignment=assignment,
                    file=form.cleaned_data["file"],
                )
                if existing:
                    messages.success(request, "Submission updated successfully.")
                else:
                    late_note = " (marked as late)" if submission.is_late else ""
                    messages.success(
                        request,
                        f"Submission received{late_note}."
                    )
            except ValidationError as e:
                messages.error(request, str(e.message))
            except PermissionDenied as e:
                messages.error(request, str(e))
            return redirect("assignments:student_list")
    else:
        form = SubmissionForm()

    return render(request, "assignments/submit.html", {
        "form": form,
        "assignment": assignment,
        "existing": existing,
    })


# ---------------------------------------------------------------------------
# Notification helper
# ---------------------------------------------------------------------------

def _notify_new_assignment(assignment):
    from notifications.utils import create_notification
    for student in assignment.section.students.filter(is_active=True):
        create_notification(
            recipient=student,
            notif_type="assignment_reminder",
            message=(
                f"New assignment posted in {assignment.section.course.name}: "
                f"'{assignment.title}' — due {assignment.due_date:%d %b %Y at %H:%M}."
            ),
            related_object_id=assignment.pk,
        )
