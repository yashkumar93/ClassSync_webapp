"""
Notification services: central push-aware notification path, FCM delivery,
announcement fan-out, and early-warning risk flag evaluation.

All notification creation goes through create_notification() so push
is wired in exactly once. Every prior Phase 1-3 caller imports from
notifications.utils, which re-exports from here.
"""
import logging
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import User, SystemConfig
from .models import Notification, DeviceToken, Announcement, RiskFlag

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Central Notification Path
# ---------------------------------------------------------------------------

def create_notification(recipient, notif_type, message, related_object_id=None):
    """
    Create an in-app Notification record, then attempt push delivery.
    Push failure is logged but never prevents the in-app record from being saved.
    """
    notification = Notification.objects.create(
        recipient=recipient,
        notif_type=notif_type,
        message=message,
        related_object_id=related_object_id,
    )

    try:
        send_push(recipient, message)
    except Exception:
        logger.warning(
            "Push delivery failed for user %s (notification pk=%s) — "
            "in-app notification still saved.",
            recipient.pk, notification.pk,
            exc_info=True,
        )

    return notification


# ---------------------------------------------------------------------------
# FCM Push Delivery (pluggable stub)
# ---------------------------------------------------------------------------

def send_push(user, message):
    """
    Attempt to send a push notification via FCM to all of the user's
    registered device tokens.

    This is a pluggable stub: it logs the push attempt and is ready to
    wire to firebase-admin when credentials are configured. If
    firebase-admin is not installed, it degrades gracefully to a log line.
    """
    tokens = DeviceToken.objects.filter(user=user).values_list("token", flat=True)
    if not tokens:
        return

    try:
        import firebase_admin
        from firebase_admin import messaging

        # Ensure default app is initialised
        if not firebase_admin._apps:
            logger.info("Firebase not initialised — skipping push delivery.")
            return

        for token in tokens:
            try:
                msg = messaging.Message(
                    notification=messaging.Notification(
                        title="Class Sync",
                        body=message[:200],
                    ),
                    token=token,
                )
                messaging.send(msg)
                logger.debug("Push sent to token %s…", token[:12])
            except Exception as e:
                logger.warning("Push failed for token %s…: %s", token[:12], e)

    except ImportError:
        logger.debug(
            "firebase-admin not installed — push skipped for user %s (%d tokens).",
            user.pk, len(tokens),
        )


# ---------------------------------------------------------------------------
# Announcements
# ---------------------------------------------------------------------------

def send_announcement(sender, scope, message, section=None):
    """
    Create an Announcement record and fan out notifications to recipients.

    scope="section": only that section's enrolled students.
    scope="institution": all active users.
    """
    if scope == Announcement.SCOPE_SECTION and section is None:
        raise ValidationError("Section is required for section-scoped announcements.")

    announcement = Announcement.objects.create(
        sender=sender,
        scope=scope,
        section=section,
        message=message,
    )

    if scope == Announcement.SCOPE_SECTION:
        recipients = section.students.filter(is_active=True)
    else:
        recipients = User.objects.filter(is_active=True)

    count = 0
    for recipient in recipients:
        create_notification(
            recipient=recipient,
            notif_type=Notification.TYPE_ANNOUNCEMENT,
            message=message,
            related_object_id=announcement.pk,
        )
        count += 1

    return announcement, count


# ---------------------------------------------------------------------------
# Early-Warning Risk Flag Evaluation
# ---------------------------------------------------------------------------

def evaluate_risk_flags():
    """
    Nightly rule-based evaluation: flag students who are at risk based on
    BOTH attendance and missed/late submission thresholds.

    Per-student, per-course evaluation with auto-resolve:
    - At risk + no open flag → create flag + notify faculty/admin
    - At risk + open flag exists → do nothing (no spam)
    - Not at risk + open flag → resolve it
    """
    from attendance.services import attendance_percentage

    config = SystemConfig.get()
    threshold_pct = config.attendance_threshold
    min_missed = config.risk_missed_submissions
    window_days = config.risk_window_days
    window_start = timezone.now() - timedelta(days=window_days)

    students = User.objects.filter(role="student", is_active=True)
    flagged_count = 0
    resolved_count = 0

    for student in students:
        courses = _courses_for_student(student)
        for course in courses:
            pct = attendance_percentage(student, course)
            missed = count_missed_or_late_submissions(student, course, window_start)

            is_at_risk = (
                pct < threshold_pct
                and missed >= min_missed
            )

            existing = RiskFlag.objects.filter(
                student=student, course=course, resolved=False
            ).first()

            if is_at_risk and not existing:
                reason = (
                    f"Attendance {pct:.1f}% (below {threshold_pct}%) "
                    f"with {missed} missed/late submissions in the last "
                    f"{window_days} days."
                )
                flag = RiskFlag.objects.create(
                    student=student,
                    course=course,
                    reason=reason,
                    attendance_pct=pct,
                    missed_submissions=missed,
                )
                _notify_risk_flag(flag)
                flagged_count += 1

            elif not is_at_risk and existing:
                existing.resolved = True
                existing.resolved_at = timezone.now()
                existing.save(update_fields=["resolved", "resolved_at"])
                resolved_count += 1

    return flagged_count, resolved_count


def count_missed_or_late_submissions(student, course, window_start):
    """
    Count late submissions + missed assignments for a student in a
    specific course within the given time window.
    """
    from assignments.models import Submission, Assignment

    now = timezone.now()

    late = Submission.objects.filter(
        student=student,
        assignment__section__course=course,
        submitted_at__gte=window_start,
        is_late=True,
    ).count()

    missed = Assignment.objects.filter(
        section__course=course,
        section__students=student,
        due_date__gte=window_start,
        due_date__lt=now,
    ).exclude(
        submissions__student=student,
    ).count()

    return late + missed


def _courses_for_student(student):
    """Return distinct courses the student is enrolled in."""
    from core.models import Course
    return Course.objects.filter(
        sections__students=student
    ).distinct()


def _notify_risk_flag(flag):
    """Notify the student's faculty for that course + all admins."""
    # Faculty for sections of this course that the student is in
    sections = flag.course.sections.filter(students=flag.student)
    notified_ids = set()

    for section in sections:
        if section.faculty and section.faculty.pk not in notified_ids:
            create_notification(
                recipient=section.faculty,
                notif_type=Notification.TYPE_RISK_FLAG,
                message=(
                    f"Early-warning flag raised for {flag.student.get_full_name()} "
                    f"in {flag.course.code}: {flag.course.name}. {flag.reason}"
                ),
                related_object_id=flag.pk,
            )
            notified_ids.add(section.faculty.pk)

    # Notify admins
    admins = User.objects.filter(role="admin", is_active=True)
    for admin in admins:
        if admin.pk not in notified_ids:
            create_notification(
                recipient=admin,
                notif_type=Notification.TYPE_RISK_FLAG,
                message=(
                    f"Early-warning flag raised for {flag.student.get_full_name()} "
                    f"in {flag.course.code}: {flag.course.name}. {flag.reason}"
                ),
                related_object_id=flag.pk,
            )
            notified_ids.add(admin.pk)
