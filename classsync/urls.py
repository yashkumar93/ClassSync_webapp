"""URL configuration for the Class Sync project."""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import TemplateView
from notifications.views import register_device_token

urlpatterns = [
    path("django-admin/", admin.site.urls),  # Django built-in admin (for superuser)
    path("", include("core.urls")),
    path("admin-panel/", include("admin_panel.urls")),
    path("absence/", include("absence.urls")),
    path("attendance/", include("attendance.urls")),
    path("assignments/", include("assignments.urls")),
    path("notifications/", include("notifications.urls")),
    # Firebase Cloud Messaging Service Worker (root scope)
    path(
        "firebase-messaging-sw.js",
        TemplateView.as_view(
            template_name="firebase-messaging-sw.js",
            content_type="application/javascript",
        ),
        name="firebase_messaging_sw",
    ),
    # API route alias for mobile/external apps
    path("api/notifications/device-token/", register_device_token, name="api_register_device_token"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

