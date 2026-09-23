from django.urls import path
from . import views

app_name = "core"

urlpatterns = [
    path("", views.dashboard_redirect, name="index"),
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("dashboard/", views.dashboard_redirect, name="dashboard"),
    path("faculty/dashboard/", views.FacultyDashboardView.as_view(), name="faculty_dashboard"),
    path("faculty/timetable/", views.faculty_timetable, name="faculty_timetable"),
    path("student/dashboard/", views.StudentDashboardView.as_view(), name="student_dashboard"),
    path("student/timetable/", views.student_timetable, name="student_timetable"),
]
