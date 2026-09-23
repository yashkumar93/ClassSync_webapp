"""
Fair Reassignment Engine — the core business logic of Class Sync.

Algorithm:
1. Find all faculty who are (a) opted-in, (b) free during the slot,
   (c) not already absent that day, (d) not the reporting faculty.
2. Rank by cumulative substitution count (ascending) within tracking window.
3. Among tied lowest-count candidates → random selection (logged for audit).
4. Propose substitute → set confirmation_deadline.
5. If no eligible faculty → self-study fallback + makeup flag.

The confirm/decline step is handled in absence/views.py + a management
command (check_confirmation_timeouts) that processes expired deadlines.
"""
import random
import logging
from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from core.models import User, SystemConfig
from .models import AbsenceReport, SubstitutionRecord, FacultyAvailability

logger = logging.getLogger(__name__)


def _get_substitution_count(faculty, window_days=30):
    """Return how many times `faculty` has been a substitute in the last `window_days`."""
    since = timezone.now() - timedelta(days=window_days)
    return SubstitutionRecord.objects.filter(
        substitute_faculty=faculty,
        timestamp__gte=since,
    ).count()


def find_eligible_substitutes(report: AbsenceReport):
    """
    Return a queryset of faculty eligible to substitute for `report`,
    ordered by ascending substitution count (lowest burden first).

    Eligibility criteria:
    - role = faculty
    - opted in (FacultyAvailability.opted_in = True)
    - NOT the absent faculty themselves
    - NOT already absent that same date
    - NOT already scheduled to teach a class in the same time slot
      (i.e. does not have a TimetableSlot overlapping the slot's day & period)
    - NOT already the proposed_substitute for another pending absence in that slot
    """
    slot = report.timetable_slot
    date = report.date
    config = SystemConfig.get()
    window_days = config.risk_window_days  # reuse same window for fairness

    # Faculty busy during this exact slot (same day, overlapping period)
    busy_faculty_ids = (
        User.objects.filter(
            teaching_sections__timetable_slots__day=slot.day,
            teaching_sections__timetable_slots__period_number=slot.period_number,
        )
        .values_list("id", flat=True)
    )

    # Faculty absent that day
    absent_that_day_ids = (
        AbsenceReport.objects.filter(
            date=date,
            status__in=[
                AbsenceReport.STATUS_PENDING,
                AbsenceReport.STATUS_PENDING_CONFIRMATION,
                AbsenceReport.STATUS_REASSIGNED,
            ],
        )
        .values_list("faculty_id", flat=True)
    )

    # Faculty already proposed for a different absence in this slot
    already_proposed_ids = (
        AbsenceReport.objects.filter(
            timetable_slot__day=slot.day,
            timetable_slot__period_number=slot.period_number,
            date=date,
            status=AbsenceReport.STATUS_PENDING_CONFIRMATION,
        )
        .exclude(pk=report.pk)
        .values_list("proposed_substitute_id", flat=True)
    )

    opted_in_ids = FacultyAvailability.objects.filter(
        opted_in=True
    ).values_list("faculty_id", flat=True)

    eligible = User.objects.filter(
        role="faculty",
        is_active=True,
        id__in=opted_in_ids,
    ).exclude(
        id=report.faculty_id,
    ).exclude(
        id__in=busy_faculty_ids,
    ).exclude(
        id__in=absent_that_day_ids,
    ).exclude(
        id__in=already_proposed_ids,
    )

    # Annotate each eligible faculty with their substitution count in window
    since = timezone.now() - timedelta(days=window_days)
    eligible = eligible.annotate(
        sub_count=Count(
            "substitution_records",
            filter=Q(substitution_records__timestamp__gte=since),
        )
    ).order_by("sub_count")

    return eligible


def broadcast_substitution_request(report: AbsenceReport):
    """
    Broadcast the substitution request to all eligible faculty members simultaneously.
    Each eligible faculty member receives a notification and sees the Accept/Decline options.
    Whoever accepts first is assigned to cover the class.
    """
    config = SystemConfig.get()
    eligible = find_eligible_substitutes(report)

    if not eligible.exists():
        logger.info("No eligible substitutes for AbsenceReport pk=%s", report.pk)
        return None

    deadline = timezone.now() + timedelta(minutes=config.confirmation_window_minutes)

    report.confirmation_deadline = deadline
    report.status = AbsenceReport.STATUS_PENDING_CONFIRMATION
    report.proposed_substitute = None
    report.save(update_fields=["confirmation_deadline", "status", "proposed_substitute", "updated_at"])

    # Set all eligible faculty as notified and clear any previous declines
    report.notified_substitutes.set(eligible)
    report.declined_substitutes.clear()

    logger.info(
        "Broadcasted substitution request for AbsenceReport pk=%s to %d eligible faculty. Deadline: %s",
        report.pk,
        eligible.count(),
        deadline,
    )

    # Notify every eligible candidate
    for candidate in eligible:
        _notify_proposed_substitute(report, candidate, is_tiebreak=False)

    return eligible


def propose_next_substitute(report: AbsenceReport):
    """
    Broadcasts the substitution request to all eligible faculty members.
    Retained for backwards-compatibility with existing calls.
    """
    eligible = broadcast_substitution_request(report)
    return eligible.first() if eligible and eligible.exists() else None


def confirm_substitution(report: AbsenceReport, substitute_faculty=None):
    """
    Called when an eligible substitute accepts the request.
    Finalises the SubstitutionRecord, marks status as reassigned,
    and notifies students, the substitute, and the absent faculty.
    """
    if report.status != AbsenceReport.STATUS_PENDING_CONFIRMATION:
        raise ValueError("Report is not in pending_confirmation state.")

    sub = substitute_faculty or report.proposed_substitute or report.notified_substitutes.first()
    if sub is None:
        raise ValueError("No substitute provided to confirm.")

    report.proposed_substitute = sub
    SubstitutionRecord.objects.update_or_create(
        absence_report=report,
        defaults={
            "substitute_faculty": sub,
            "was_random_tiebreak": False,
        },
    )

    report.status = AbsenceReport.STATUS_REASSIGNED
    report.save(update_fields=["proposed_substitute", "status", "updated_at"])

    logger.info(
        "Substitution confirmed: %s will cover %s.",
        sub.get_full_name(),
        report,
    )

    # Notify students & absent faculty
    _notify_students_reassignment(report)
    _notify_faculty_reassignment(report, sub)


def decline_or_timeout(report: AbsenceReport, declining_faculty=None):
    """
    Called when a faculty declines or the deadline passes.
    If declining_faculty is provided, they are marked as declined.
    If all notified faculty have declined or the deadline expired with 0 accepts,
    the class falls back to self-study.
    """
    if declining_faculty:
        report.declined_substitutes.add(declining_faculty)
        logger.info(
            "Faculty %s declined substitution request for AbsenceReport pk=%s.",
            declining_faculty.get_full_name(),
            report.pk,
        )
        # Check if any eligible faculty remain who have NOT declined
        remaining = report.notified_substitutes.exclude(id__in=report.declined_substitutes.all())
        if not remaining.exists():
            logger.info("All notified substitutes declined AbsenceReport pk=%s. Falling back to self-study.", report.pk)
            mark_self_study(report)
    else:
        # Full timeout: deadline passed without any acceptance
        logger.info("Confirmation deadline expired for AbsenceReport pk=%s. Falling back to self-study.", report.pk)
        mark_self_study(report)


def mark_self_study(report: AbsenceReport):
    """Fall back to self-study and flag as makeup candidate."""
    report.status = AbsenceReport.STATUS_SELF_STUDY
    report.is_makeup_candidate = True
    report.proposed_substitute = None
    report.confirmation_deadline = None
    report.save(update_fields=[
        "status", "is_makeup_candidate", "proposed_substitute",
        "confirmation_deadline", "updated_at",
    ])

    logger.info("AbsenceReport pk=%s marked as Self-Study / Makeup Candidate.", report.pk)
    _notify_students_self_study(report)
    _notify_admin_self_study(report)


# ---------------------------------------------------------------------------
# Notification helpers (delegate to notifications app)
# ---------------------------------------------------------------------------

def _notify_proposed_substitute(report, substitute, is_tiebreak):
    from notifications.utils import create_notification
    slot = report.timetable_slot
    create_notification(
        recipient=substitute,
        notif_type="substitution_request",
        message=(
            f"You have been proposed to substitute for "
            f"{report.faculty.get_full_name()} in "
            f"{slot.section.course.name} (Section {slot.section.name}) "
            f"on {report.date} at {slot.start_time:%H:%M}. "
            f"Please confirm or decline within the next "
            f"{SystemConfig.get().confirmation_window_minutes} minutes."
        ),
        related_object_id=report.pk,
    )


def _notify_students_reassignment(report):
    from notifications.utils import create_notification
    slot = report.timetable_slot
    sub = report.proposed_substitute
    students = slot.section.students.filter(is_active=True)
    for student in students:
        create_notification(
            recipient=student,
            notif_type="class_reassigned",
            message=(
                f"Your {slot.section.course.name} class on {report.date} at "
                f"{slot.start_time:%H:%M} will be taken by "
                f"{sub.get_full_name()} instead of {report.faculty.get_full_name()}."
            ),
            related_object_id=report.pk,
        )


def _notify_faculty_reassignment(report, sub):
    from notifications.utils import create_notification
    slot = report.timetable_slot
    create_notification(
        recipient=report.faculty,
        notif_type="class_reassigned",
        message=(
            f"{sub.get_full_name()} has accepted your substitution request and will cover your "
            f"{slot.section.course.name} class on {report.date} at {slot.start_time:%H:%M}."
        ),
        related_object_id=report.pk,
    )



def _notify_students_self_study(report):
    from notifications.utils import create_notification
    slot = report.timetable_slot
    students = slot.section.students.filter(is_active=True)
    for student in students:
        create_notification(
            recipient=student,
            notif_type="self_study",
            message=(
                f"Your {slot.section.course.name} class on {report.date} at "
                f"{slot.start_time:%H:%M} has been marked as Self-Study. "
                f"No substitute could be arranged. This period may be rescheduled later."
            ),
            related_object_id=report.pk,
        )


def _notify_admin_self_study(report):
    from notifications.utils import create_notification
    from core.models import User
    admins = User.objects.filter(role="admin", is_active=True)
    for admin in admins:
        create_notification(
            recipient=admin,
            notif_type="makeup_candidate",
            message=(
                f"No substitute found for {report.faculty.get_full_name()}'s class "
                f"({report.timetable_slot.section.course.name}, Section "
                f"{report.timetable_slot.section.name}) on {report.date}. "
                f"Period flagged as Makeup Candidate."
            ),
            related_object_id=report.pk,
        )


def _was_random_tiebreak(report):
    """Heuristic: return (sub_count, was_tiebreak). Used only for logging."""
    config = SystemConfig.get()
    since = timezone.now() - timedelta(days=config.risk_window_days)
    from django.db.models import Count, Q
    if report.proposed_substitute is None:
        return 0, False
    count = SubstitutionRecord.objects.filter(
        substitute_faculty=report.proposed_substitute,
        timestamp__gte=since,
    ).count()
    return count, False  # tiebreak flag stored at proposal time; simplified here
