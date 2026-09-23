"""Django admin configuration for attendance models."""
from django.contrib import admin as django_admin
from .models import AttendanceSession, AttendanceRecord, ThresholdAlert


@django_admin.register(AttendanceSession)
class AttendanceSessionAdmin(django_admin.ModelAdmin):
    list_display = [
        "timetable_slot", "date", "otp_code", "generated_by",
        "expires_at", "absences_processed", "is_active", "created_at",
    ]
    list_filter = ["date", "absences_processed", "timetable_slot__section__course"]
    search_fields = [
        "otp_code",
        "generated_by__first_name", "generated_by__last_name",
        "timetable_slot__section__course__name",
    ]
    readonly_fields = ["otp_code", "created_at"]

    def is_active(self, obj):
        return obj.is_active
    is_active.boolean = True
    is_active.short_description = "Active?"


@django_admin.register(AttendanceRecord)
class AttendanceRecordAdmin(django_admin.ModelAdmin):
    list_display = ["student", "session", "status", "marked_at", "updated_at"]
    list_filter = ["status", "session__date", "session__timetable_slot__section__course"]
    search_fields = [
        "student__first_name", "student__last_name", "student__roll_number",
    ]
    readonly_fields = ["marked_at", "updated_at"]


@django_admin.register(ThresholdAlert)
class ThresholdAlertAdmin(django_admin.ModelAdmin):
    list_display = [
        "student", "course", "triggered_at", "resolved", "resolved_at",
    ]
    list_filter = ["resolved", "course"]
    search_fields = [
        "student__first_name", "student__last_name", "student__roll_number",
        "course__code", "course__name",
    ]
    readonly_fields = ["triggered_at"]
