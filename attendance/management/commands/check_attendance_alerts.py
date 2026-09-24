"""
Management command: check_attendance_alerts

Scan all active students and courses for attendance falling below the threshold (<75%).
Dispatches in-app attendance alert notifications to affected students.

    py manage.py check_attendance_alerts
"""
from django.core.management.base import BaseCommand
from attendance.services import evaluate_all_attendance_thresholds


class Command(BaseCommand):
    help = "Evaluate attendance threshold breaches (<75%) and notify affected students."

    def handle(self, *args, **options):
        triggered, resolved = evaluate_all_attendance_thresholds()
        self.stdout.write(
            self.style.SUCCESS(
                f"Attendance check complete: {triggered} new alert(s) triggered, {resolved} alert(s) resolved."
            )
        )
