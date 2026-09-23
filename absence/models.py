"""
Absence & Fair Reassignment models.
"""
from django.db import models
from django.utils import timezone
from core.models import User, TimetableSlot


class FacultyAvailability(models.Model):
    """
    Tracks each faculty member's substitution opt-in status.
    Faculty who opt out will never be proposed as substitutes.
    """
    faculty = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        limit_choices_to={"role": "faculty"},
        related_name="availability",
    )
    opted_in = models.BooleanField(
        default=True,
        help_text="Whether this faculty has opted in to receive substitution requests.",
    )
    notes = models.TextField(
        blank=True,
        help_text="Optional: general availability notes (e.g. not available Mon mornings).",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Faculty Availability"
        verbose_name_plural = "Faculty Availabilities"

    def __str__(self):
        status = "opted in" if self.opted_in else "opted out"
        return f"{self.faculty.get_full_name()} ({status})"


class AbsenceReport(models.Model):
    """
    A faculty member's report that they cannot take a scheduled class.
    Drives the entire reassignment engine.
    """
    STATUS_PENDING = "pending"
    STATUS_PENDING_CONFIRMATION = "pending_confirmation"
    STATUS_REASSIGNED = "reassigned"
    STATUS_SELF_STUDY = "self_study"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_PENDING_CONFIRMATION, "Pending Confirmation"),
        (STATUS_REASSIGNED, "Reassigned"),
        (STATUS_SELF_STUDY, "Self-Study / Makeup Flagged"),
    ]

    faculty = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        limit_choices_to={"role": "faculty"},
        related_name="absences",
    )
    timetable_slot = models.ForeignKey(
        TimetableSlot,
        on_delete=models.CASCADE,
        related_name="absence_reports",
    )
    date = models.DateField(help_text="The specific date this absence applies to.")
    reason = models.TextField(blank=True, help_text="Optional reason for absence.")
    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default=STATUS_PENDING)

    # Confirmation workflow
    proposed_substitute = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        limit_choices_to={"role": "faculty"},
        related_name="proposed_substitutions",
    )
    notified_substitutes = models.ManyToManyField(
        User,
        blank=True,
        limit_choices_to={"role": "faculty"},
        related_name="substitution_requests_received",
        help_text="All eligible faculty who received this substitution request.",
    )
    declined_substitutes = models.ManyToManyField(
        User,
        blank=True,
        limit_choices_to={"role": "faculty"},
        related_name="substitution_requests_declined",
        help_text="Faculty who explicitly declined this substitution request.",
    )
    confirmation_deadline = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Deadline by which a substitute must confirm.",
    )
    is_makeup_candidate = models.BooleanField(
        default=False,
        help_text="Set True when no substitute found; flags this period for a makeup class.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        # Prevent duplicate absence reports for same faculty/slot/date
        unique_together = ("faculty", "timetable_slot", "date")

    def __str__(self):
        return (
            f"{self.faculty.get_full_name()} absent on {self.date} "
            f"[{self.timetable_slot}] — {self.get_status_display()}"
        )

    @property
    def is_confirmation_expired(self):
        if self.confirmation_deadline is None:
            return False
        return timezone.now() > self.confirmation_deadline


class SubstitutionRecord(models.Model):
    """
    Immutable audit log of every completed substitution.
    Used for load-balancing: the engine queries the count per faculty
    within the current tracking window to pick the least-burdened substitute.
    """
    absence_report = models.OneToOneField(
        AbsenceReport,
        on_delete=models.CASCADE,
        related_name="substitution_record",
    )
    substitute_faculty = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        limit_choices_to={"role": "faculty"},
        related_name="substitution_records",
    )
    was_random_tiebreak = models.BooleanField(
        default=False,
        help_text="True if this substitute was chosen via random tie-break (for audit transparency).",
    )
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return (
            f"{self.substitute_faculty.get_full_name()} covered "
            f"{self.absence_report} on {self.timestamp:%Y-%m-%d %H:%M}"
        )
