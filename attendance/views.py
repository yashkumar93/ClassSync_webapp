"""
Attendance app views — thin wrappers around services.py.

Faculty:
  generate_otp_view  — pick slot + date, generate session, redirect to display
  session_display    — large-text OTP + live countdown (projector view)
  section_dashboard  — per-student attendance table for a section

Student:
  submit_otp     — enter OTP code to mark attendance
  my_attendance  — personal attendance overview across enrolled courses

"""
from datetime import date as date_type

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from core.decorators import role_required
from core.models import SystemConfig, Section, TimetableSlot
from .models import AttendanceSession, AttendanceRecord
from .analytics import get_student_attendance_summary, get_section_attendance_dashboard
from . import services


# ---------------------------------------------------------------------------
# Faculty: Generate OTP (slot picker + creation)
# ---------------------------------------------------------------------------

@role_required("faculty", "admin")
def generate_otp_view(request, slot_id, date_str):
    """
    POST: generate (or regenerate) an OTP session for this slot/date.
    GET:  show confirmation and class timing status before generating.
    """
    slot = get_object_or_404(TimetableSlot, pk=slot_id)

    try:
        target_date = date_type.fromisoformat(date_str)
    except (ValueError, TypeError):
        messages.error(request, "Invalid date format. Use YYYY-MM-DD.")
        return redirect("core:faculty_dashboard") if request.user.role == "faculty" else redirect("admin_panel:dashboard")

    now = timezone.localtime()
    today = now.date()
    now_time = now.time()

    is_today = (target_date == today)
    is_correct_day = (slot.day == target_date.weekday())
    is_class_time = is_today and is_correct_day and (slot.start_time <= now_time <= slot.end_time)
    is_before_class = is_today and is_correct_day and (now_time < slot.start_time)
    is_after_class = is_today and is_correct_day and (now_time > slot.end_time)

    if request.method == "POST":
        allow_override = (
            request.POST.get("allow_timing_override") in ("true", "1", "on")
            or request.user.role == "admin"
        )
        try:
            session = services.generate_otp(
                slot,
                target_date,
                request.user,
                enforce_timings=True,
                allow_timing_override=allow_override,
            )
        except ValidationError as e:
            messages.error(request, str(e.message if hasattr(e, "message") else e))
            return redirect("attendance:generate_otp", slot_id=slot.pk, date_str=date_str)
        except PermissionDenied as e:
            messages.error(request, str(e))
            return redirect("core:faculty_dashboard") if request.user.role == "faculty" else redirect("attendance:section_dashboard", section_id=slot.section_id)

        return redirect("attendance:session_display", session_id=session.pk)

    # GET — show the generate-OTP confirmation page
    config = SystemConfig.get()
    existing_session = AttendanceSession.objects.filter(
        timetable_slot=slot, date=target_date
    ).first()

    return render(request, "attendance/generate_otp.html", {
        "slot": slot,
        "target_date": target_date,
        "validity_seconds": config.otp_validity_seconds,
        "existing_session": existing_session,
        "now_time": now_time,
        "is_today": is_today,
        "is_correct_day": is_correct_day,
        "is_class_time": is_class_time,
        "is_before_class": is_before_class,
        "is_after_class": is_after_class,
    })


# ---------------------------------------------------------------------------
# Faculty: Session Display (projector view)
# ---------------------------------------------------------------------------

@role_required("faculty", "admin")
def session_display(request, session_id):
    """
    Large-text OTP display with live countdown — meant for projector/screen.
    Only accessible by the faculty who generated it (or admin).
    """
    session = get_object_or_404(AttendanceSession, pk=session_id)

    # Access control: only the generator or admins can view
    if request.user.role == "faculty" and session.generated_by != request.user:
        raise PermissionDenied("You can only view OTP sessions you generated.")

    # Auto-process absences if session has expired and not processed yet
    if timezone.now() > session.expires_at and not session.absences_processed:
        services.process_session_absences(session, triggered_by=request.user)

    config = SystemConfig.get()

    # Calculate remaining seconds for the countdown
    remaining = (session.expires_at - timezone.now()).total_seconds()
    remaining = max(0, int(remaining))

    return render(request, "attendance/otp_display.html", {
        "session": session,
        "slot": session.timetable_slot,
        "validity_seconds": config.otp_validity_seconds,
        "remaining_seconds": remaining,
    })


# ---------------------------------------------------------------------------
# Faculty: Session Roster, Finalize & Manual Toggle
# ---------------------------------------------------------------------------

@role_required("faculty", "admin")
def session_roster(request, session_id):
    """
    Shows student-by-student attendance for a specific session.
    Faculty can view Present/Absent status and toggle attendance.
    """
    session = get_object_or_404(AttendanceSession, pk=session_id)
    slot = session.timetable_slot
    section = slot.section

    # Access control: effective faculty, generator, assigned faculty, or admin
    effective_faculty = services.get_effective_faculty(slot, session.date)
    if request.user.role == "faculty" and request.user not in (session.generated_by, section.faculty, effective_faculty):
        raise PermissionDenied("You do not have access to view this session's roster.")

    # Auto-process absences if session has expired and not processed yet
    if timezone.now() > session.expires_at and not session.absences_processed:
        services.process_session_absences(session, triggered_by=request.user)

    students = section.students.filter(is_active=True).order_by("last_name", "first_name")
    records_by_student = {r.student_id: r for r in session.records.all()}

    student_rows = []
    for student in students:
        rec = records_by_student.get(student.pk)
        status = rec.status if rec else ("unmarked" if session.is_active else "absent")
        student_rows.append({
            "student": student,
            "record": rec,
            "status": status,
        })

    return render(request, "attendance/session_roster.html", {
        "session": session,
        "slot": slot,
        "section": section,
        "effective_faculty": effective_faculty,
        "student_rows": student_rows,
        "present_count": session.present_count,
        "absent_count": session.absent_count,
    })


@role_required("faculty", "admin")
def finalize_session(request, session_id):
    """
    Explicitly finalize an attendance session and mark all unrecorded
    students absent, sending alert notifications to both students and faculty.
    """
    session = get_object_or_404(AttendanceSession, pk=session_id)
    slot = session.timetable_slot
    section = slot.section
    effective_faculty = services.get_effective_faculty(slot, session.date)

    if request.user.role == "faculty" and request.user not in (session.generated_by, section.faculty, effective_faculty):
        raise PermissionDenied("You do not have access to finalize this session.")

    count = services.process_session_absences(session, triggered_by=request.user)
    if count > 0:
        messages.success(
            request,
            f"Attendance finalized! {count} student(s) marked absent. "
            f"Alert notifications sent to the absent students and faculty."
        )
    else:
        messages.info(request, "Attendance finalized. No new absences to record.")

    return redirect("attendance:session_roster", session_id=session.pk)


@role_required("faculty", "admin")
def toggle_attendance(request, session_id, student_id):
    """
    Toggle a student's attendance between Present and Absent for a session.
    When marked absent, alerts are dispatched to both student and faculty.
    """
    if request.method != "POST":
        return redirect("attendance:session_roster", session_id=session_id)

    session = get_object_or_404(AttendanceSession, pk=session_id)
    slot = session.timetable_slot
    section = slot.section
    effective_faculty = services.get_effective_faculty(slot, session.date)

    if request.user.role == "faculty" and request.user not in (session.generated_by, section.faculty, effective_faculty):
        raise PermissionDenied("You do not have access to modify attendance for this session.")

    from core.models import User
    student = get_object_or_404(User, pk=student_id, role="student")

    target = request.POST.get("target_status")
    rec = session.records.filter(student=student).first()
    current_status = rec.status if rec else "absent"

    if target == "absent" or (not target and current_status == "present"):
        services.mark_student_absent(student, session, marked_by=request.user, notify=True)
        messages.success(
            request,
            f"{student.get_full_name()} marked ABSENT. Alert notification sent to student and faculty."
        )
    else:
        services.mark_student_present(student, session, marked_by=request.user, notify=True)
        messages.success(
            request,
            f"{student.get_full_name()} marked PRESENT."
        )

    return redirect("attendance:session_roster", session_id=session.pk)


# ---------------------------------------------------------------------------
# Student: Submit OTP
# ---------------------------------------------------------------------------

@role_required("student")
def submit_otp(request):
    """
    Shows today's classes with active sessions.
    POST: validate and record attendance via services.mark_attendance.
    """
    today = timezone.localdate()
    student = request.user

    if request.method == "POST":
        code = request.POST.get("otp_code", "").strip()
        slot_id = request.POST.get("slot_id", "")

        if not slot_id:
            messages.error(request, "Please select a class.")
            return redirect("attendance:submit_otp")

        slot = get_object_or_404(TimetableSlot, pk=slot_id)

        try:
            services.mark_attendance(student, slot, today, code)
        except ValidationError as e:
            messages.error(request, str(e.message))
            return redirect("attendance:submit_otp")
        except PermissionDenied as e:
            messages.error(request, str(e))
            return redirect("attendance:submit_otp")

        messages.success(
            request,
            f"Attendance marked for {slot.section.course.name}!"
        )
        return redirect("core:student_dashboard")

    # GET — show today's classes that have active sessions
    enrolled_sections = student.enrolled_sections.all()
    today_weekday = today.weekday()

    active_sessions = []
    for section in enrolled_sections:
        slots = section.timetable_slots.filter(day=today_weekday)
        for slot in slots:
            session = AttendanceSession.objects.filter(
                timetable_slot=slot, date=today
            ).first()
            already_marked = False
            if session:
                already_marked = AttendanceRecord.objects.filter(
                    session=session, student=student, status=AttendanceRecord.STATUS_PRESENT
                ).exists()
            active_sessions.append({
                "slot": slot,
                "section": section,
                "session": session,
                "is_active": session.is_active if session else False,
                "already_marked": already_marked,
            })

    return render(request, "attendance/submit_otp.html", {
        "active_sessions": active_sessions,
        "today": today,
        "now_time": timezone.localtime(timezone.now()).time(),
    })


# ---------------------------------------------------------------------------
# Faculty/Admin: Section Attendance Dashboard
# ---------------------------------------------------------------------------

@role_required("faculty", "admin")
def section_dashboard(request, section_id):
    section = get_object_or_404(Section, pk=section_id)
    config = SystemConfig.get()

    sessions = AttendanceSession.objects.filter(
        timetable_slot__section=section
    ).order_by("-date")[:10]

    # Auto-process any expired sessions for this section that haven't processed absences yet
    now = timezone.now()
    for s in sessions:
        if now > s.expires_at and not s.absences_processed:
            services.process_session_absences(s)

    rows = get_section_attendance_dashboard(section)

    # Today's slots for this section (for "Generate OTP" buttons)
    today = timezone.localdate()
    today_weekday = today.weekday()
    today_slots = section.timetable_slots.filter(day=today_weekday)

    return render(request, "attendance/section_dashboard.html", {
        "section": section,
        "rows": rows,
        "sessions": sessions,
        "threshold": config.attendance_threshold,
        "today_slots": today_slots,
        "today": today,
    })


# ---------------------------------------------------------------------------
# Student: My Attendance
# ---------------------------------------------------------------------------

@role_required("student")
def my_attendance(request):
    summary = get_student_attendance_summary(request.user)
    return render(request, "attendance/my_attendance.html", {"summary": summary})
