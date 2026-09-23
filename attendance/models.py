"""Attendance app models: AttendanceSession, AttendanceRecord, ThresholdAlert."""
import random
import string
from django.db import models
from django.utils import timezone
from core.models import User, TimetableSlot, Course


class AttendanceSession(models.Model):
    """
    Represents a single class session for which OTP-based attendance is taken.
    One session per class per date.
    """
    timetable_slot = models.ForeignKey(
        TimetableSlot, on_delete=models.CASCADE, related_name="attendance_sessions"
    )
    date = models.DateField()
    otp_code = models.CharField(max_length=6)
    generated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        limit_choices_to={"role": "faculty"},
        related_name="generated_sessions",
    )
    expires_at = models.DateTimeField()
    absences_processed = models.BooleanField(
        default=False,
        help_text="Whether absent students have been identified and notified."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("timetable_slot", "date")
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return f"Session: {self.timetable_slot} on {self.date} (OTP: {self.otp_code})"

    @property
    def is_active(self):
        return timezone.now() <= self.expires_at

    @property
    def present_count(self):
        return self.records.filter(status="present").count()

    @property
    def absent_count(self):
        return self.records.filter(status="absent").count()

    @classmethod
    def generate_otp(cls, timetable_slot, generated_by, validity_seconds):
        """Create (or regenerate) a session OTP for the given slot and today."""
        date = timezone.localdate()
        code = "".join(random.choices(string.digits, k=6))
        expires_at = timezone.now() + timezone.timedelta(seconds=validity_seconds)

        session, created = cls.objects.update_or_create(
            timetable_slot=timetable_slot,
            date=date,
            defaults={
                "otp_code": code,
                "generated_by": generated_by,
                "expires_at": expires_at,
            },
        )
        return session


class AttendanceRecord(models.Model):
    """Records a student's attendance for one session."""
    STATUS_PRESENT = "present"
    STATUS_ABSENT = "absent"
    STATUS_CHOICES = [
        (STATUS_PRESENT, "Present"),
        (STATUS_ABSENT, "Absent"),
    ]

    session = models.ForeignKey(
        AttendanceSession, on_delete=models.CASCADE, related_name="records"
    )
    student = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        limit_choices_to={"role": "student"},
        related_name="attendance_records",
    )
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default=STATUS_PRESENT,
    )
    marked_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("session", "student")
        ordering = ["-marked_at"]

    def __str__(self):
        return f"{self.student.get_full_name()} [{self.status}] at {self.session}"


class ThresholdAlert(models.Model):
    """
    Tracks attendance-threshold breaches per student per course.

    Resolve/reopen semantics prevent notification spam:
    - Created (once) when attendance drops below the threshold.
    - Resolved when attendance climbs back above.
    - A new alert is created only if attendance dips below again after resolving.
    """
    student = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        limit_choices_to={"role": "student"},
        related_name="threshold_alerts",
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="threshold_alerts",
    )
    triggered_at = models.DateTimeField(auto_now_add=True)
    resolved = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-triggered_at"]

    def __str__(self):
        status = "resolved" if self.resolved else "active"
        return (
            f"ThresholdAlert: {self.student.get_full_name()} — "
            f"{self.course.code} [{status}]"
        )
