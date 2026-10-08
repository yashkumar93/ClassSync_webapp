from django.urls import path
from . import views

app_name = "notifications"

urlpatterns = [
    path("", views.notification_center, name="center"),
    path("<int:pk>/read/", views.mark_notification_read, name="mark_read"),
    path("read-all/", views.mark_all_notifications_read, name="mark_all_read"),
    path("risk-flags/", views.risk_flags, name="risk_flags"),
    path("risk-flags/<int:pk>/resolve/", views.resolve_flag, name="resolve_flag"),
    path("device-token/", views.register_device_token, name="register_device_token"),
    path("unregister-device-token/", views.unregister_device_token, name="unregister_device_token"),
    path("test-push/", views.test_push_notification, name="test_push"),
]

