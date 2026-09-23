"""
Seed command: populate the database with realistic demo data for development.

    py manage.py seed_demo

Creates:
  - 1 Admin, 1 Department, 3 Courses, 3 Sections
  - 5 Faculty members (with opt-in status)
  - 15 Students (enrolled in sections)
  - Timetable slots for Monday–Friday
  - 2 sample assignments
  - 1 sample absence report
"""
import random
from datetime import time, date, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import User, Department, Course, Section, TimetableSlot, SystemConfig
from absence.models import FacultyAvailability, AbsenceReport
from assignments.models import Assignment


FACULTY_DATA = [
    ("dr.naseer", "Dr. Naseer", "Ahmed"),
    ("dr.madhukar", "Dr.", "Madhukar"),
    ("dr.jagadeeshwar", "Dr.", "Jaagadeeshwar"),
    ("deepak", "Deepak", ""),
    ("dr.shankar", "Dr. Shankar", "Lingham"),
]

STUDENT_DATA = [
    ("23BCS1001", "Meghana", "Reddy"),
    ("23BCS1002", "Sathwika", "Reddy"),
    ("23BCS1003", "Ashmith", "Sai"),
    ("23BCS1004", "Sathwika", "Goud"),
    ("23BCS1005", "Swetha", "Dasari"),
    ("23BCS1006", "Sakshi", ""),
    ("23BCS1007", "Vishnupriya", "Gopi"),
    ("23BCS1008", "Akshay", "Macharla"),
    ("23BCS1009", "Bhuvaneshwari", "Reddy"),
    ("23BCS1010", "Dhrutika", "Reddy"),
    ("23BCS1011", "Laya", "Sarala"),
    ("23BCS1012", "Greeshma", "Reddy"),
    ("23BCS1013", "Sai Charan", "Reddy"),
    ("23BCS1014", "Harshada", "Reddy"),
    ("23BCS1015", "Rithvik", "Sai"),
]

COURSES_DATA = [
    ("CS401", "Virtualization and Cloud", 4, "dr.naseer", "Room 401"),
    ("CS402", "Data Structures and Algorithms", 4, "dr.madhukar", "Room 201"),
    ("CS403", "Internet of Things", 3, "dr.jagadeeshwar", "Lab 102"),
    ("CS404", "Principles of Economics", 3, "deepak", "Room 105"),
    ("CS405", "R Programming", 3, "dr.shankar", "Lab 203"),
]


class Command(BaseCommand):
    help = "Seed the database with demo data for development."

    def handle(self, *args, **options):
        self.stdout.write("Seeding subjects and faculty mappings...")

        # System config (60s OTP validity)
        config, _ = SystemConfig.objects.get_or_create(pk=1)
        config.otp_validity_seconds = 60
        config.save()

        # Department
        dept, _ = Department.objects.get_or_create(code="CSE", defaults={"name": "Computer Science & Engineering"})

        # Admin user
        if not User.objects.filter(username="admin_demo").exists():
            User.objects.create_superuser(
                username="admin_demo", email="admin@classsync.dev",
                password="Admin@1234", role="admin", first_name="Admin", last_name="Demo",
                department=dept,
            )
            self.stdout.write("  Created admin: admin_demo / Admin@1234")

        # Clean up any old demo faculty users
        valid_faculty_usernames = [f[0] for f in FACULTY_DATA]
        old_faculty = User.objects.filter(role="faculty").exclude(username__in=valid_faculty_usernames)
        old_count = old_faculty.count()
        old_faculty.delete()
        if old_count:
            self.stdout.write(f"  Removed {old_count} old demo faculty users")

        # Faculty
        faculty_by_username = {}
        for username, first, last in FACULTY_DATA:
            u, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "first_name": first, "last_name": last,
                    "email": f"{username}@classsync.dev",
                    "role": "faculty", "department": dept,
                }
            )
            if created:
                u.set_password("Faculty@1234")
                u.save()
            else:
                u.first_name = first
                u.last_name = last
                u.role = "faculty"
                u.department = dept
                u.set_password("Faculty@1234")
                u.save()
            avail, _ = FacultyAvailability.objects.get_or_create(faculty=u)
            avail.opted_in = True
            avail.save()
            faculty_by_username[username] = u

        self.stdout.write(f"  Ensured all {len(faculty_by_username)} faculty members are active and opted-in for substitutions (password: Faculty@1234)")

        # Clean up any old student users
        valid_student_usernames = [s[0] for s in STUDENT_DATA]
        old_students = User.objects.filter(role="student").exclude(username__in=valid_student_usernames)
        old_std_count = old_students.count()
        old_students.delete()
        if old_std_count:
            self.stdout.write(f"  Removed {old_std_count} old student records")

        # Students
        student_users = []
        for roll, first, last in STUDENT_DATA:
            u, created = User.objects.get_or_create(
                username=roll,
                defaults={
                    "first_name": first, "last_name": last,
                    "email": f"{roll.lower()}@classsync.dev",
                    "role": "student", "department": dept,
                    "roll_number": roll,
                }
            )
            if created:
                u.set_password("Student@1234")
                u.save()
            else:
                u.first_name = first
                u.last_name = last
                u.email = f"{roll.lower()}@classsync.dev"
                u.roll_number = roll
                u.role = "student"
                u.department = dept
                u.set_password("Student@1234")
                u.save()
            student_users.append(u)

        self.stdout.write(f"  Ensured {len(student_users)} students with register number usernames (password: Student@1234)")

        # Remove all previous subjects / courses (cascades to old sections, slots, assignments)
        deleted_count, _ = Course.objects.all().delete()
        self.stdout.write(f"  Removed previous subjects and associated data ({deleted_count} records removed)")

        # Create new Courses, Sections, and map teachers
        sections = []
        for code, name, credits, faculty_uname, room in COURSES_DATA:
            course = Course.objects.create(
                code=code,
                name=name,
                credits=credits,
                department=dept,
            )
            faculty = faculty_by_username[faculty_uname]
            section = Section.objects.create(
                course=course,
                name="A",
                faculty=faculty,
                room=room,
            )
            # Enroll all students into this subject section (every student in DB)
            all_db_students = User.objects.filter(role="student")
            section.students.set(all_db_students)
            sections.append(section)
            self.stdout.write(f"  Mapped '{course.name}' ({course.code}) -> {faculty.get_full_name()} ({faculty.username})")

        # Staggered timetable slots so NO two subjects share the same period!
        # This ensures whenever ANY faculty is absent, ALL OTHER 4 FACULTY ARE 100% FREE TO SUBSTITUTE!
        period_times = {
            1: (time(9, 0), time(9, 50)),
            2: (time(10, 0), time(10, 50)),
            3: (time(11, 0), time(11, 50)),
            4: (time(12, 0), time(12, 50)),
            5: (time(14, 0), time(14, 50)),
        }
        slots_created = 0
        for i, section in enumerate(sections):
            period = i + 1  # Section 0 -> P1, Section 1 -> P2, Section 2 -> P3, Section 3 -> P4, Section 4 -> P5
            start, end = period_times[period]
            for day in range(5):  # Mon to Fri (0 to 4)
                TimetableSlot.objects.create(
                    section=section,
                    day=day,
                    period_number=period,
                    start_time=start,
                    end_time=end,
                )
                slots_created += 1

        self.stdout.write(f"  Created {slots_created} conflict-free timetable slots (P1 to P5, Mon-Fri)")

        # Sample assignments for each subject
        now = timezone.now()
        for section in sections:
            Assignment.objects.create(
                section=section,
                title=f"{section.course.name} - Assignment 1",
                description=f"Initial assignment covering core concepts in {section.course.name}.",
                due_date=now + timedelta(days=7),
                created_by=section.faculty,
            )

        self.stdout.write(f"  Created {len(sections)} assignments (1 per subject)")

        # Sample absence and confirmed substitution demonstration:
        # Dr. Naseer Ahmed has an absence today, and Dr. Madhukar was assigned as substitute!
        from absence.models import SubstitutionRecord
        today = timezone.localdate()
        today_slot_naseer = TimetableSlot.objects.filter(section=sections[0], day=today.weekday()).first()
        if today_slot_naseer:
            report = AbsenceReport.objects.create(
                faculty=sections[0].faculty,
                timetable_slot=today_slot_naseer,
                date=today,
                reason="Attending Academic Conference",
                status=AbsenceReport.STATUS_REASSIGNED,
                proposed_substitute=sections[1].faculty,
            )
            SubstitutionRecord.objects.create(
                absence_report=report,
                substitute_faculty=sections[1].faculty,
            )
            self.stdout.write(f"  Created confirmed substitution: {sections[1].faculty.get_full_name()} covers for {sections[0].faculty.get_full_name()} today!")

        # Demonstration of Broadcast Substitution Request:
        # Dr. Jaagadeeshwar reports absence for tomorrow's class.
        # The request is broadcast to all other 4 faculty members so any of them can accept or decline!
        tomorrow = today + timedelta(days=1)
        tomorrow_slot_iot = TimetableSlot.objects.filter(section=sections[2], day=tomorrow.weekday()).first()
        if tomorrow_slot_iot:
            from absence.engine import broadcast_substitution_request
            report_pending = AbsenceReport.objects.create(
                faculty=sections[2].faculty,
                timetable_slot=tomorrow_slot_iot,
                date=tomorrow,
                reason="Medical appointment",
            )
            broadcast_candidates = broadcast_substitution_request(report_pending)
            names = ", ".join(c.get_full_name() for c in broadcast_candidates) if broadcast_candidates else "None"
            self.stdout.write(f"  Broadcast substitution request for {sections[2].faculty.get_full_name()}'s class to {broadcast_candidates.count()} faculty members ({names})!")

        self.stdout.write(self.style.SUCCESS("\nSeeding complete!"))
        self.stdout.write("  All 5 faculty members teach their own subject AND can substitute for any class:")
        for u in faculty_by_username.values():
            teaching = ", ".join(s.course.name for s in u.teaching_sections.all())
            self.stdout.write(f"    - {u.username} ({u.get_full_name()}): {teaching}")
        self.stdout.write("  Students: 23BCS1001 to 23BCS1015 / Student@1234")

