"""Management command to populate realistic alert and notification records into the database."""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
import random

from core.models import User, Course, Section
from notifications.models import Notification, RiskFlag, Announcement
from attendance.models import ThresholdAlert


class Command(BaseCommand):
    help = "Populate realistic alerts and notifications across all categories for students, faculty, and admins."

    def handle(self, *args, **options):
        self.stdout.write("Seeding alerts and notifications...")

        admins = list(User.objects.filter(role="admin", is_active=True))
        faculty = list(User.objects.filter(role="faculty", is_active=True))
        students = list(User.objects.filter(role="student", is_active=True))
        courses = list(Course.objects.all())

        if not students or not courses:
            self.stdout.write(self.style.ERROR("No students or courses found in the database. Run seed_demo first."))
            return

        now = timezone.now()
        created_count = 0

        # ---------------------------------------------------------
        # 1. System Alerts & Attendance Threshold Alerts (Students)
        # ---------------------------------------------------------
        for s in students[:10]:
            # The exact alert mentioned by user
            Notification.objects.create(
                recipient=s,
                notif_type=Notification.TYPE_ATTENDANCE_ALERT,
                message="Attendance threshold alert: Check your attendance report.",
                sent_at=now - timedelta(minutes=random.randint(5, 120)),
            )
            created_count += 1

            # Detailed course attendance breach alert
            course = courses[random.randint(0, len(courses) - 1)]
            pct = round(random.uniform(55.0, 72.5), 1)
            Notification.objects.create(
                recipient=s,
                notif_type=Notification.TYPE_ATTENDANCE_ALERT,
                message=f"Attendance Alert: Your attendance in {course.name} ({course.code}) has dropped to {pct}%, which is below the required 75%. Please attend upcoming classes to avoid academic penalties.",
                sent_at=now - timedelta(hours=random.randint(1, 48)),
            )
            created_count += 1

            # Ensure ThresholdAlert record exists
            ThresholdAlert.objects.get_or_create(
                student=s,
                course=course,
                resolved=False,
            )

        # ---------------------------------------------------------
        # 2. Risk Flags & Early-Warning Alerts (Admins & Faculty)
        # ---------------------------------------------------------
        flagged_students = students[2:6]
        for s in flagged_students:
            course = courses[0]  # CS401
            pct = round(random.uniform(58.0, 69.0), 1)
            missed = random.randint(2, 4)
            reason = f"Attendance {pct}% (below 75.0%) with {missed} missed/late submissions in the last 30 days."

            flag, _ = RiskFlag.objects.get_or_create(
                student=s,
                course=course,
                resolved=False,
                defaults={
                    "reason": reason,
                    "attendance_pct": pct,
                    "missed_submissions": missed,
                }
            )

            # Notify all admins
            for a in admins:
                Notification.objects.create(
                    recipient=a,
                    notif_type=Notification.TYPE_RISK_FLAG,
                    message=f"Early-warning flag raised for {s.get_full_name()} in {course.code}: {course.name}. {reason}",
                    related_object_id=flag.pk,
                    sent_at=now - timedelta(hours=random.randint(2, 36)),
                )
                created_count += 1

            # Notify faculty teaching that course
            for f in faculty[:3]:
                Notification.objects.create(
                    recipient=f,
                    notif_type=Notification.TYPE_RISK_FLAG,
                    message=f"Early-warning flag raised for {s.get_full_name()} in {course.code}: {course.name}. {reason}",
                    related_object_id=flag.pk,
                    sent_at=now - timedelta(hours=random.randint(2, 36)),
                )
                created_count += 1

        # ---------------------------------------------------------
        # 3. Assignment Reminders (Students & Faculty)
        # ---------------------------------------------------------
        for s in students[:8]:
            Notification.objects.create(
                recipient=s,
                notif_type=Notification.TYPE_ASSIGNMENT_REMINDER,
                message="Assignment Reminder: 'CS401: Cloud Architecture & Docker Containerization Lab' in Virtualization and Cloud is due tomorrow at 18:00. You have not submitted your assignment yet. Please submit before the deadline.",
                sent_at=now - timedelta(hours=random.randint(1, 24)),
            )
            created_count += 1

        for f in faculty[:2]:
            Notification.objects.create(
                recipient=f,
                notif_type=Notification.TYPE_ASSIGNMENT_REMINDER,
                message="Pending Submission Alert: 6 students have not yet submitted 'CS401: Cloud Architecture & Docker Containerization Lab', which is due tomorrow at 18:00.",
                sent_at=now - timedelta(hours=random.randint(3, 18)),
            )
            created_count += 1

        # ---------------------------------------------------------
        # 4. Substitution Requests & Schedule Alerts (Faculty)
        # ---------------------------------------------------------
        for f in faculty:
            Notification.objects.create(
                recipient=f,
                notif_type=Notification.TYPE_SUBSTITUTION_REQUEST,
                message="Substitution Request: Dr. Naseer has requested substitution for CS401: Virtualization and Cloud (Section A) on 2026-10-05 (Period 3).",
                sent_at=now - timedelta(hours=random.randint(4, 30)),
            )
            created_count += 1

            Notification.objects.create(
                recipient=f,
                notif_type=Notification.TYPE_CLASS_REASSIGNED,
                message="Class Schedule Update: Your timetable has been updated for upcoming departmental seminars on Friday.",
                sent_at=now - timedelta(days=1),
            )
            created_count += 1

        # ---------------------------------------------------------
        # 5. Announcements (All Users)
        # ---------------------------------------------------------
        all_active_users = list(admins) + list(faculty) + list(students)
        for u in all_active_users:
            Notification.objects.create(
                recipient=u,
                notif_type=Notification.TYPE_ANNOUNCEMENT,
                message="Institution Announcement: Mid-term examination schedule and room allocations have been published on the student portal.",
                sent_at=now - timedelta(hours=8),
            )
            created_count += 1

            Notification.objects.create(
                recipient=u,
                notif_type=Notification.TYPE_ANNOUNCEMENT,
                message="System Alert: Firebase Cloud Messaging is connected. Enable push notifications from your navigation bar for live alerts.",
                sent_at=now - timedelta(minutes=45),
            )
            created_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded {created_count} alert and notification records into the database!"
            )
        )
