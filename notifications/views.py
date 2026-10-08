"""Notifications app views: notification center, mark read, risk flags."""
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse

from core.decorators import role_required
from .models import Notification, RiskFlag, DeviceToken
from .utils import mark_all_read, mark_read



@login_required
def notification_center(request):
    notifications = Notification.objects.filter(
        recipient=request.user
    ).order_by("-sent_at")[:50]
    return render(request, "notifications/center.html", {"notifications": notifications})


@login_required
def mark_notification_read(request, pk):
    mark_read(pk, request.user)
    referer = request.META.get("HTTP_REFERER")
    return redirect(referer if referer else reverse("notifications:center"))


@login_required
def mark_all_notifications_read(request):
    mark_all_read(request.user)
    return redirect("notifications:center")


@role_required("faculty", "admin")
def risk_flags(request):
    flags = RiskFlag.objects.filter(resolved=False).select_related(
        "student__department"
    ).order_by("-flagged_at")

    if request.user.role == "faculty":
        # Only show flags for students in this faculty's sections
        student_ids = request.user.teaching_sections.values_list("students", flat=True)
        flags = flags.filter(student_id__in=student_ids)

    return render(request, "notifications/risk_flags.html", {"flags": flags})


@role_required("faculty", "admin")
def resolve_flag(request, pk):
    flag = get_object_or_404(RiskFlag, pk=pk)
    flag.resolved = True
    flag.resolved_at = timezone.now()
    flag.resolved_by = request.user
    flag.save(update_fields=["resolved", "resolved_at", "resolved_by"])
    return redirect("notifications:risk_flags")


@login_required
def register_device_token(request):
    """Register or refresh an FCM device token for the current user."""
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST method required"}, status=405)
    try:
        import json
        data = json.loads(request.body.decode("utf-8")) if request.body else request.POST
        token = data.get("token", "").strip()
        platform = data.get("platform", "web").strip().lower()
        if not token:
            return JsonResponse({"status": "error", "message": "Token is required"}, status=400)

        device_token, created = DeviceToken.objects.update_or_create(
            token=token,
            defaults={
                "user": request.user,
                "platform": platform or "web",
            },
        )
        return JsonResponse({
            "status": "ok",
            "message": "Device token registered successfully",
            "created": created,
        })
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
def unregister_device_token(request):
    """Unregister an FCM device token on logout or permission revoke."""
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST method required"}, status=405)
    try:
        import json
        data = json.loads(request.body.decode("utf-8")) if request.body else request.POST
        token = data.get("token", "").strip()
        if token:
            deleted_count, _ = DeviceToken.objects.filter(token=token, user=request.user).delete()
            return JsonResponse({"status": "ok", "deleted": deleted_count})
        return JsonResponse({"status": "error", "message": "Token is required"}, status=400)
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=400)


@login_required
def test_push_notification(request):
    """Send a test push notification to all of the current user's registered devices."""
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST method required"}, status=405)
    tokens_count = DeviceToken.objects.filter(user=request.user).count()
    if tokens_count == 0:
        return JsonResponse({
            "status": "warning",
            "message": "No active device tokens found for your account. Please enable push notifications in your browser first.",
        }, status=400)

    try:
        from .services import send_push
        send_push(
            user=request.user,
            message="This is a test notification from Class Sync via Firebase Cloud Messaging!",
            title="Class Sync — Test Notification",
            data={"type": "test_push"}
        )
        return JsonResponse({
            "status": "ok",
            "message": f"Test push notification dispatched to {tokens_count} device token(s).",
        })
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=500)

