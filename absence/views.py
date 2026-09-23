"""Absence app views."""
from django.contrib import messages
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.decorators import role_required
from core.models import TimetableSlot, SystemConfig
from .models import AbsenceReport, FacultyAvailability
from .engine import (
    broadcast_substitution_request,
    propose_next_substitute,
    confirm_substitution,
    decline_or_timeout,
    mark_self_study,
)
from .forms import AbsenceReportForm


# ---------------------------------------------------------------------------
# Faculty: report absence
# ---------------------------------------------------------------------------

@role_required("faculty")
def report_absence(request):
    if request.method == "POST":
        form = AbsenceReportForm(request.POST, faculty=request.user)
        if form.is_valid():
            report = form.save(commit=False)
            report.faculty = request.user
            report.status = AbsenceReport.STATUS_PENDING
            report.save()

            # Broadcast substitution request to all other eligible faculty members
            candidates = broadcast_substitution_request(report)
            if candidates and candidates.exists():
                messages.success(
                    request,
                    f"Absence reported. Substitution request has been sent to all {candidates.count()} eligible faculty members "
                    f"who can now accept or decline."
                )
            else:
                mark_self_study(report)
                messages.warning(
                    request,
                    "Absence reported. No eligible substitute found — period marked as Self-Study."
                )
            return redirect("absence:my_absences")
    else:
        form = AbsenceReportForm(faculty=request.user)

    return render(request, "absence/report_absence.html", {"form": form})


@role_required("faculty")
def my_absences(request):
    absences = AbsenceReport.objects.filter(faculty=request.user).select_related(
        "timetable_slot__section__course", "proposed_substitute"
    ).prefetch_related("notified_substitutes", "declined_substitutes").order_by("-date")
    return render(request, "absence/my_absences.html", {"absences": absences})


# ---------------------------------------------------------------------------
# Faculty: opt-in/out
# ---------------------------------------------------------------------------

@role_required("faculty")
def opt_in_status(request):
    availability, _ = FacultyAvailability.objects.get_or_create(faculty=request.user)
    if request.method == "POST":
        availability.opted_in = not availability.opted_in
        availability.notes = request.POST.get("notes", availability.notes)
        availability.save()
        status = "opted in to" if availability.opted_in else "opted out of"
        messages.success(request, f"You have {status} substitution requests.")
        return redirect("absence:opt_in_status")
    return render(request, "absence/opt_in_status.html", {"availability": availability})


# ---------------------------------------------------------------------------
# Faculty: confirm / decline substitution
# ---------------------------------------------------------------------------

@role_required("faculty")
@require_POST
def confirm_substitute(request, pk):
    report = get_object_or_404(AbsenceReport, pk=pk)

    if report.status != AbsenceReport.STATUS_PENDING_CONFIRMATION:
        if report.status == AbsenceReport.STATUS_REASSIGNED:
            sub_name = report.proposed_substitute.get_full_name() if report.proposed_substitute else "another faculty member"
            messages.warning(request, f"This substitution request has already been accepted by {sub_name}.")
        else:
            messages.warning(request, "This substitution request is no longer available.")
        return redirect("core:faculty_dashboard")

    if report.is_confirmation_expired:
        messages.error(request, "Confirmation window has expired.")
        return redirect("core:faculty_dashboard")

    confirm_substitution(report, substitute_faculty=request.user)
    messages.success(
        request,
        f"You have confirmed substitution for {report.timetable_slot.section.course.name} on {report.date}."
    )
    return redirect("core:faculty_dashboard")


@role_required("faculty")
@require_POST
def decline_substitute(request, pk):
    report = get_object_or_404(AbsenceReport, pk=pk)
    if report.status == AbsenceReport.STATUS_PENDING_CONFIRMATION:
        decline_or_timeout(report, declining_faculty=request.user)
        messages.info(request, "You have declined this substitution request.")
    return redirect("core:faculty_dashboard")


# ---------------------------------------------------------------------------
# Substitution history (for admin/faculty view)
# ---------------------------------------------------------------------------

@role_required("admin", "faculty")
def substitution_history(request):
    from .models import SubstitutionRecord
    records = SubstitutionRecord.objects.select_related(
        "substitute_faculty", "absence_report__faculty",
        "absence_report__timetable_slot__section__course",
    ).order_by("-timestamp")

    if request.user.role == "faculty":
        records = records.filter(substitute_faculty=request.user)

    return render(request, "absence/substitution_history.html", {"records": records})
