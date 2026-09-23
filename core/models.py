"""
Core models for Class Sync.
Defines the custom User model (with role) and the base academic
structure: Department → Course → Section → TimetableSlot.
Also defines SystemConfig for admin-configurable platform settings.
"""
from django.contrib.auth.models import AbstractUser
from django.db import models


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

class User(AbstractUser):
    """
    Extended Django user supporting three roles:
      admin    — institution/department staff
      faculty  — lecturer
      student  — enrolled student
    """
    ROLE_ADMIN = "admin"
    ROLE_FACULTY = "faculty"
    ROLE_STUDENT = "student"
    ROLE_CHOICES = [
        (ROLE_ADMIN, "Admin"),
        (ROLE_FACULTY, "Faculty"),
        (ROLE_STUDENT, "Student"),
    ]

    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    phone = models.CharField(max_length=20, blank=True)
    department = models.ForeignKey(
        "Department",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="members",
    )
    is_active = models.BooleanField(default=True)
    date_joined_platform = models.DateField(auto_now_add=True)

    # Student-specific
    roll_number = models.CharField(max_length=30, blank=True)
    year_of_study = models.PositiveSmallIntegerField(null=True, blank=True)

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"

    @property
    def is_admin_role(self):
        return self.role == self.ROLE_ADMIN

    @property
    def is_faculty(self):
        return self.role == self.ROLE_FACULTY

    @property
    def is_student(self):
        return self.role == self.ROLE_STUDENT


# ---------------------------------------------------------------------------
# Academic Structure
# ---------------------------------------------------------------------------

class Department(models.Model):
    name = models.CharField(max_length=120, unique=True)
    code = models.CharField(max_length=10, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.code} — {self.name}"


class Course(models.Model):
    department = models.ForeignKey(
        Department, on_delete=models.CASCADE, related_name="courses"
    )
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)
    credits = models.PositiveSmallIntegerField(default=3)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code}: {self.name}"


class Section(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="sections")
    name = models.CharField(max_length=10, help_text="E.g. A, B, C or 1, 2, 3")
    faculty = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        limit_choices_to={"role": User.ROLE_FACULTY},
        related_name="teaching_sections",
    )
    students = models.ManyToManyField(
        User,
        blank=True,
        limit_choices_to={"role": User.ROLE_STUDENT},
        related_name="enrolled_sections",
    )
    room = models.CharField(max_length=30, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("course", "name")
        ordering = ["course", "name"]

    def __str__(self):
        return f"{self.course.code} — Section {self.name}"


class TimetableSlot(models.Model):
    DAY_CHOICES = [
        (0, "Monday"),
        (1, "Tuesday"),
        (2, "Wednesday"),
        (3, "Thursday"),
        (4, "Friday"),
        (5, "Saturday"),
        (6, "Sunday"),
    ]

    section = models.ForeignKey(
        Section, on_delete=models.CASCADE, related_name="timetable_slots"
    )
    day = models.PositiveSmallIntegerField(choices=DAY_CHOICES)
    period_number = models.PositiveSmallIntegerField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("section", "day", "period_number")
        ordering = ["day", "period_number"]

    def __str__(self):
        return (
            f"{self.section} | {self.get_day_display()} P{self.period_number} "
            f"({self.start_time:%H:%M}–{self.end_time:%H:%M})"
        )


# ---------------------------------------------------------------------------
# System Configuration (admin-configurable platform-wide settings)
# ---------------------------------------------------------------------------

class SystemConfig(models.Model):
    """
    Singleton-style model for admin-editable platform defaults.
    There should only ever be one row (pk=1).
    """
    otp_validity_seconds = models.PositiveIntegerField(
        default=60,
        help_text="How long (seconds) an OTP is valid for attendance marking.",
    )
    attendance_threshold = models.PositiveSmallIntegerField(
        default=75,
        help_text="Attendance % below which a student alert is triggered.",
    )
    risk_missed_submissions = models.PositiveSmallIntegerField(
        default=2,
        help_text="Number of missed/late submissions (within risk window) required to trigger early-warning.",
    )
    risk_window_days = models.PositiveSmallIntegerField(
        default=30,
        help_text="Look-back window (days) for counting missed/late submissions in risk evaluation.",
    )
    confirmation_window_minutes = models.PositiveSmallIntegerField(
        default=12,
        help_text="Minutes a proposed substitute has to confirm/decline before falling through to next.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "System Configuration"

    def __str__(self):
        return "System Configuration"

    @classmethod
    def get(cls):
        """Return the singleton config row, creating defaults if absent."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
