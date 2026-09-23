from django.urls import path
from . import views

app_name = "attendance"

urlpatterns = [
    # Faculty: generate OTP for a slot on a specific date
    path("generate/<int:slot_id>/<str:date_str>/", views.generate_otp_view, name="generate_otp"),
    # Faculty/Admin: projector display of an active session's OTP
    path("session/<int:session_id>/display/", views.session_display, name="session_display"),
    # Faculty/Admin: per-session attendance roster
    path("session/<int:session_id>/roster/", views.session_roster, name="session_roster"),
    # Faculty/Admin: finalize session attendance and mark absences
    path("session/<int:session_id>/finalize/", views.finalize_session, name="finalize_session"),
    # Faculty/Admin: toggle individual student attendance (Present/Absent)
    path("session/<int:session_id>/toggle/<int:student_id>/", views.toggle_attendance, name="toggle_attendance"),
    # Faculty/Admin: per-section attendance dashboard
    path("dashboard/<int:section_id>/", views.section_dashboard, name="section_dashboard"),
    # Student: mark attendance via OTP
    path("mark/", views.submit_otp, name="submit_otp"),
    # Student: personal attendance overview
    path("my/", views.my_attendance, name="my_attendance"),
]
