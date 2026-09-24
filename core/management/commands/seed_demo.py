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
from datetime import time, date, datetime, timedelta
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import User, Department, Course, Section, TimetableSlot, SystemConfig
from absence.models import FacultyAvailability, AbsenceReport, SubstitutionRecord
from attendance.models import AttendanceSession, AttendanceRecord, ThresholdAlert
from attendance.services import evaluate_all_attendance_thresholds
from assignments.models import Assignment, Submission, ReminderLog
from assignments.services import send_assignment_reminders
from notifications.models import Notification


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

        # System config (60s OTP validity, 75% attendance threshold)
        config, _ = SystemConfig.objects.get_or_create(pk=1)
        config.otp_validity_seconds = 60
        config.attendance_threshold = 75
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

        # Clean up old notifications and reminder logs for a fresh demo run
        Notification.objects.all().delete()
        ReminderLog.objects.all().delete()
        AbsenceReport.objects.all().delete()

        # ===================================================================
        # Historical Attendance Mock Data (Past 10 Weekdays)
        # ===================================================================
        self.stdout.write("Seeding historical attendance sessions and student records...")
        today = timezone.localdate()
        past_weekdays = []
        d = today - timedelta(days=1)
        while len(past_weekdays) < 10:
            if d.weekday() < 5:  # Mon to Fri
                past_weekdays.append(d)
            d -= timedelta(days=1)
        past_weekdays.reverse()  # Chronological order

        sessions_created = 0
        records_created = 0

        for section_idx, section in enumerate(sections):
            for sess_idx, sess_date in enumerate(past_weekdays):
                slot = TimetableSlot.objects.filter(section=section, day=sess_date.weekday()).first()
                if not slot:
                    continue

                session = AttendanceSession.objects.create(
                    timetable_slot=slot,
                    date=sess_date,
                    otp_code=f"{random.randint(100000, 999999)}",
                    generated_by=section.faculty,
                    expires_at=timezone.make_aware(datetime.combine(sess_date, time(23, 59))),
                    absences_processed=True,
                )
                sessions_created += 1

                for std in student_users:
                    # Configure specific students with attendance < 75% to demonstrate threshold alerts:
                    # 1. 23BCS1004 (Sathwika Goud):
                    #    - CS401 (Section 0): 4 out of 10 attended = 40.0% (<75%)
                    #    - CS402 (Section 1): 5 out of 10 attended = 50.0% (<75%)
                    #    - Other sections: 8 out of 10 = 80.0%
                    # 2. 23BCS1005 (Swetha Dasari):
                    #    - CS403 (Section 2): 6 out of 10 attended = 60.0% (<75%)
                    #    - Other sections: 8 out of 10 = 80.0%
                    # 3. 23BCS1010 (Dhrutika Reddy):
                    #    - CS405 (Section 4): 5 out of 10 attended = 50.0% (<75%)
                    #    - Other sections: 9 out of 10 = 90.0%
                    # 4. Other students: 80% to 100% attendance
                    is_present = True
                    if std.username == "23BCS1004":
                        if section_idx == 0:
                            is_present = sess_idx in [0, 2, 5, 8]  # 4/10 = 40%
                        elif section_idx == 1:
                            is_present = sess_idx in [1, 3, 5, 7, 9]  # 5/10 = 50%
                        else:
                            is_present = sess_idx not in [2, 7]  # 8/10 = 80%
                    elif std.username == "23BCS1005":
                        if section_idx == 2:
                            is_present = sess_idx in [0, 2, 4, 6, 8, 9]  # 6/10 = 60%
                        else:
                            is_present = sess_idx not in [1, 6]  # 8/10 = 80%
                    elif std.username == "23BCS1010":
                        if section_idx == 4:
                            is_present = sess_idx in [0, 2, 5, 7, 9]  # 5/10 = 50%
                        else:
                            is_present = sess_idx != 4  # 9/10 = 90%
                    else:
                        # General students: 80% - 100% attendance
                        is_present = not ((hash(std.username) + sess_idx + section_idx) % 7 == 0 and sess_idx in [3, 7])

                    AttendanceRecord.objects.create(
                        session=session,
                        student=std,
                        status=AttendanceRecord.STATUS_PRESENT if is_present else AttendanceRecord.STATUS_ABSENT,
                    )
                    records_created += 1

        self.stdout.write(f"  Created {sessions_created} attendance sessions across 10 past dates with {records_created} student records.")

        # Evaluate attendance threshold alerts (<75%)
        att_triggered, att_resolved = evaluate_all_attendance_thresholds()
        self.stdout.write(
            f"  Attendance evaluation: {att_triggered} alert(s) triggered for students with <75% attendance "
            f"(23BCS1004 in CS401 & CS402, 23BCS1005 in CS403, 23BCS1010 in CS405)."
        )

        # ===================================================================
        # Assignments & Submissions Mock Data
        # ===================================================================
        self.stdout.write("Seeding assignments and student submissions...")
        now = timezone.now()
        local_now = timezone.localtime(now)
        tomorrow_date = local_now.date() + timedelta(days=1)

        def mock_pdf(filename="submission.pdf"):
            return ContentFile(
                b"%PDF-1.4 Mock Assignment Submission File for Class Sync.\n"
                b"Student work content demonstrating complete implementation.\n%%EOF",
                name=filename,
            )

        # 1. CS401 Assignment DUE TOMORROW at 18:00 (Dr. Naseer Ahmed)
        due_tomorrow_18 = timezone.make_aware(datetime.combine(tomorrow_date, time(18, 0)))
        a1 = Assignment.objects.create(
            section=sections[0],
            title="CS401: Cloud Architecture & Docker Containerization Lab",
            description=(
                "Deploy a multi-tier web application using Docker containers on AWS EC2. "
                "Submit architecture diagram, docker-compose.yml, and deployment log screenshots."
            ),
            due_date=due_tomorrow_18,
            created_by=sections[0].faculty,
        )
        # Submissions for A1: 4 students submitted, 11 pending (due tomorrow!)
        a1_submitted_usernames = ["23BCS1001", "23BCS1002", "23BCS1007", "23BCS1008"]
        for uname in a1_submitted_usernames:
            std = next(s for s in student_users if s.username == uname)
            Submission.objects.create(
                assignment=a1,
                student=std,
                file=mock_pdf(f"{uname}_cs401_lab.pdf"),
                is_late=False,
            )

        # 2. CS402 Assignment DUE TOMORROW at 23:59 (Dr. Madhukar)
        due_tomorrow_2359 = timezone.make_aware(datetime.combine(tomorrow_date, time(23, 59)))
        a2 = Assignment.objects.create(
            section=sections[1],
            title="CS402: Advanced Graph Algorithms & Shortest Path Problem",
            description=(
                "Implement Dijkstra's algorithm with Fibonacci heaps and Bellman-Ford in Python/C++. "
                "Include benchmark performance analysis on sparse vs dense graphs."
            ),
            due_date=due_tomorrow_2359,
            created_by=sections[1].faculty,
        )
        # Submissions for A2: 5 students submitted, 10 pending (due tomorrow!)
        a2_submitted_usernames = ["23BCS1001", "23BCS1005", "23BCS1006", "23BCS1011", "23BCS1012"]
        for uname in a2_submitted_usernames:
            std = next(s for s in student_users if s.username == uname)
            Submission.objects.create(
                assignment=a2,
                student=std,
                file=mock_pdf(f"{uname}_cs402_graphs.pdf"),
                is_late=False,
            )

        # 3. CS403 Assignment: Due in 5 days (Upcoming)
        a3 = Assignment.objects.create(
            section=sections[2],
            title="CS403: IoT Sensor Network Protocols & MQTT",
            description=(
                "Configure an MQTT broker on Raspberry Pi / ESP32. "
                "Publish temperature and humidity sensor readings to cloud MQTT endpoint."
            ),
            due_date=now + timedelta(days=5),
            created_by=sections[2].faculty,
        )
        # Early submission from 1 student
        std_early = next(s for s in student_users if s.username == "23BCS1001")
        Submission.objects.create(
            assignment=a3,
            student=std_early,
            file=mock_pdf("23BCS1001_cs403_mqtt.pdf"),
            is_late=False,
        )

        # 4. CS404 Assignment: Past deadline (4 days ago) - demonstrations of late submissions
        a4 = Assignment.objects.create(
            section=sections[3],
            title="CS404: Macroeconomic Analysis & Market Equilibrium",
            description="Empirical research report on interest rates, monetary policy, and consumer price index trends.",
            due_date=now - timedelta(days=4),
            created_by=sections[3].faculty,
        )
        # On-time submissions
        for uname in ["23BCS1001", "23BCS1002", "23BCS1003", "23BCS1005"]:
            std = next(s for s in student_users if s.username == uname)
            Submission.objects.create(
                assignment=a4,
                student=std,
                file=mock_pdf(f"{uname}_cs404_macro.pdf"),
                is_late=False,
            )
        # Late submission (submitted after deadline)
        std_late = next(s for s in student_users if s.username == "23BCS1007")
        Submission.objects.create(
            assignment=a4,
            student=std_late,
            file=mock_pdf("23BCS1007_cs404_macro_late.pdf"),
            is_late=True,
        )

        # 5. CS405 Assignment: Due in 8 days (Upcoming)
        Assignment.objects.create(
            section=sections[4],
            title="CS405: Statistical Modeling with R Markdown",
            description="Create reproducible R Markdown notebook demonstrating multivariate regression and ANOVA tests.",
            due_date=now + timedelta(days=8),
            created_by=sections[4].faculty,
        )

        self.stdout.write(f"  Created 5 assignments (2 due tomorrow with pending submissions, 1 past due with late submission, 2 upcoming)")

        # ===================================================================
        # Dispatch Assignment Deadline Alerts for Assignments Due Tomorrow
        # ===================================================================
        reminders_sent = send_assignment_reminders()
        self.stdout.write(
            f"  Dispatched {reminders_sent} deadline reminder alerts for assignments due tomorrow "
            f"(alerts sent to both unsubmitted students AND their concerned faculty)!"
        )

        # ===================================================================
        # Demonstration of Absence & Substitution
        # ===================================================================
        # Dr. Naseer Ahmed has an absence today, and Dr. Madhukar was assigned as substitute!
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

        # Broadcast Substitution Request:
        # Dr. Jaagadeeshwar reports absence for tomorrow's class.
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
        self.stdout.write("  Demonstration Data Ready:")
        self.stdout.write("  - Low Attendance (<75%) Alerts: 23BCS1004 (40% in CS401, 50% in CS402), 23BCS1005 (60% in CS403), 23BCS1010 (50% in CS405)")
        self.stdout.write("  - Due Tomorrow Alerts: CS401 (18:00) & CS402 (23:59) - alerts delivered to both pending students and faculty")
        self.stdout.write("  - Logins:")
        self.stdout.write("    * Student (Low Attendance & Missing Assignment): 23BCS1004 / Student@1234")
        self.stdout.write("    * Student (Good Attendance & Submitted): 23BCS1001 / Student@1234")
        self.stdout.write("    * Faculty (CS401): dr.naseer / Faculty@1234")
        self.stdout.write("    * Faculty (CS402): dr.madhukar / Faculty@1234")
        self.stdout.write("    * Admin: admin_demo / Admin@1234")

