"""
Assignment services: submission logic with proper resubmission semantics,
and reminder dispatch with deduplication via ReminderLog.
"""
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from .models import Assignment, Submission, ReminderLog


# ---------------------------------------------------------------------------
# Submission Logic
# ---------------------------------------------------------------------------

def submit_assignment(student, assignment, file):
    """
    Handle assignment submission with the following rules:

    Before deadline:
      - First submission: allowed, is_late = False
      - Resubmission: allowed (update_or_create replaces the file)

    After deadline:
      - First submission: allowed, is_late = True (late but genuine)
      - Resubmission: BLOCKED (protects grading integrity)
    """
    # Enrollment check
    if not assignment.section.students.filter(pk=student.pk).exists():
        raise PermissionDenied("Not enrolled in this section.")

    existing = Submission.objects.filter(
        assignment=assignment, student=student
    ).first()
    now = timezone.now()

    if existing and now > assignment.due_date:
        raise ValidationError(
            "Deadline has passed — this submission can no longer be changed."
        )

    submission, _ = Submission.objects.update_or_create(
        assignment=assignment,
        student=student,
        defaults={
            "file": file,
            "submitted_at": now,
            "is_late": now > assignment.due_date,
        },
    )
    return submission


# ---------------------------------------------------------------------------
# Reminder Logic
# ---------------------------------------------------------------------------

REMINDER_OFFSETS = [
    (timedelta(days=3), "3_day"),
    (timedelta(days=1), "1_day"),
    (timedelta(hours=2), "2_hour"),
]

# Matches the cron cadence — run every ~10 minutes
BUFFER = timedelta(minutes=10)


def send_assignment_reminders():
    """
    Fire reminder notifications for assignments approaching their deadline.

    For assignments due tomorrow (1_day offset):
    - Identifies assignments whose deadline is tomorrow (or within the 24-36h window).
    - For each enrolled student who hasn't submitted yet:
      - Sends an urgent deadline reminder notification to the STUDENT.
      - Sends an alert notification to the CONCERNED FACULTY regarding the unsubmitted student.
      - Logs in ReminderLog to ensure deduplication (safe to run repeatedly).

    For 3-day and 2-hour offsets:
    - Sends reminder notification to unsubmitted students.

    Returns the total number of notifications sent.
    """
    from notifications.utils import create_notification

    now = timezone.now()
    local_now = timezone.localtime(now)
    tomorrow_date = local_now.date() + timedelta(days=1)
    sent = 0

    for offset, label in REMINDER_OFFSETS:
        if label == "1_day":
            # Match assignments whose deadline is tomorrow or within [now, now + 36h]
            due_soon = Assignment.objects.filter(
                due_date__gt=now,
                due_date__lte=now + timedelta(hours=36),
            ).select_related("section__course", "section__faculty", "created_by")
        else:
            window_center = now + offset
            due_soon = Assignment.objects.filter(
                due_date__range=(window_center - BUFFER, window_center + BUFFER)
            ).select_related("section__course", "section__faculty", "created_by")

        for assignment in due_soon:
            # If label is 1_day, make sure it is due tomorrow in local time or within 24h
            if label == "1_day":
                assign_local_due = timezone.localtime(assignment.due_date)
                is_due_tomorrow = (
                    assign_local_due.date() == tomorrow_date
                    or (assignment.due_date > now and assignment.due_date <= now + timedelta(days=1, hours=2))
                )
                if not is_due_tomorrow:
                    continue

            # Students who already submitted — skip them
            already_submitted_ids = Submission.objects.filter(
                assignment=assignment
            ).values_list("student_id", flat=True)

            # Enrolled students who haven't submitted
            pending_students = assignment.section.students.filter(
                is_active=True
            ).exclude(id__in=already_submitted_ids)

            faculty = assignment.section.faculty or assignment.created_by

            for student in pending_students:
                # Check if already sent this specific reminder
                already_sent = ReminderLog.objects.filter(
                    assignment=assignment,
                    student=student,
                    offset_label=label,
                ).exists()

                if already_sent:
                    continue

                formatted_due = timezone.localtime(assignment.due_date).strftime("%d %b %Y at %H:%M")

                if label == "1_day":
                    # 1. Alert to Student
                    create_notification(
                        recipient=student,
                        notif_type="assignment_reminder",
                        message=(
                            f"Assignment Reminder: '{assignment.title}' in "
                            f"{assignment.section.course.name} is due tomorrow "
                            f"({formatted_due}). You have not submitted your assignment yet. "
                            f"Please submit before the deadline."
                        ),
                        related_object_id=assignment.pk,
                    )
                    sent += 1

                    # 2. Alert to Concerned Faculty
                    if faculty:
                        roll_str = f" ({student.roll_number})" if student.roll_number else ""
                        create_notification(
                            recipient=faculty,
                            notif_type="assignment_reminder",
                            message=(
                                f"Pending Submission Alert: Student {student.get_full_name()}{roll_str} "
                                f"has not yet submitted '{assignment.title}' "
                                f"({assignment.section.course.code}: {assignment.section.course.name}), "
                                f"which is due tomorrow ({formatted_due})."
                            ),
                            related_object_id=assignment.pk,
                        )
                        sent += 1
                else:
                    # General student reminder for 3_day and 2_hour offsets
                    display_label = label.replace("_", " ").replace("day", " day").replace("hour", " hour")
                    display_label = display_label.replace("  ", " ").strip()

                    create_notification(
                        recipient=student,
                        notif_type="assignment_reminder",
                        message=(
                            f"Reminder: '{assignment.title}' in "
                            f"{assignment.section.course.name} is due in "
                            f"{display_label} ({formatted_due})."
                        ),
                        related_object_id=assignment.pk,
                    )
                    sent += 1

                ReminderLog.objects.create(
                    assignment=assignment,
                    student=student,
                    offset_label=label,
                )

    return sent
