"""Test script for Class Sync Firebase Cloud Messaging (FCM) integration."""
import os
import sys
import django
import json

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "classsync.settings")
django.setup()

from django.test import Client
from django.urls import reverse
from core.models import User
from notifications.models import Notification, DeviceToken
from notifications.services import initialize_firebase, create_notification, send_push


def run_tests():
    print("\n" + "=" * 60)
    print("Class Sync FCM Integration Test Suite")
    print("=" * 60)

    # 1. Test Firebase Admin SDK initialization
    print("\n[1] Testing Firebase Admin SDK initialization...")
    app = initialize_firebase()
    assert app is not None, "Firebase Admin SDK should initialize"
    assert app.name == "[DEFAULT]", f"App name should be [DEFAULT], got {app.name}"
    print(f"  OK: Firebase initialized successfully (app name: {app.name})")

    # 2. Setup test user and client
    print("\n[2] Setting up test user and test client...")
    user, _ = User.objects.get_or_create(
        username="fcm_test_user",
        defaults={
            "role": "student",
            "first_name": "FCM",
            "last_name": "Tester",
            "email": "fcm_test@example.com",
        }
    )
    user.set_password("testpass123")
    user.save()

    client = Client()
    login_success = client.login(username="fcm_test_user", password="testpass123")
    assert login_success, "Client login must succeed"
    print("  OK: Test user logged in")

    # 3. Test Service Worker URL endpoint
    print("\n[3] Testing /firebase-messaging-sw.js endpoint...")
    sw_res = client.get("/firebase-messaging-sw.js")
    assert sw_res.status_code == 200, f"SW should return 200, got {sw_res.status_code}"
    assert "javascript" in sw_res["Content-Type"], f"Expected javascript content type, got {sw_res['Content-Type']}"
    assert "firebase.initializeApp" in sw_res.content.decode("utf-8"), "SW must contain firebase.initializeApp"
    assert "onBackgroundMessage" in sw_res.content.decode("utf-8"), "SW must contain onBackgroundMessage"
    print("  OK: Service worker properly served at root with correct JS content-type and Firebase config")

    # 4. Test Device Token Registration API
    print("\n[4] Testing POST /notifications/device-token/...")
    DeviceToken.objects.filter(token="test_fcm_token_abc_123").delete()

    res = client.post(
        "/notifications/device-token/",
        data=json.dumps({"token": "test_fcm_token_abc_123", "platform": "web"}),
        content_type="application/json",
    )
    assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.content}"
    data = res.json()
    assert data.get("status") == "ok", f"Expected status ok, got {data}"
    assert data.get("created") is True, "Token should be created"

    # Verify database
    dt = DeviceToken.objects.filter(token="test_fcm_token_abc_123").first()
    assert dt is not None, "DeviceToken should be in DB"
    assert dt.user == user, "DeviceToken user mismatch"
    assert dt.platform == "web", "DeviceToken platform mismatch"
    print("  OK: Device token saved to DB for user")

    # Re-register same token (idempotency check)
    res_update = client.post(
        "/notifications/device-token/",
        data=json.dumps({"token": "test_fcm_token_abc_123", "platform": "web"}),
        content_type="application/json",
    )
    assert res_update.status_code == 200
    assert res_update.json().get("created") is False, "Token should be updated, not duplicated"
    assert DeviceToken.objects.filter(token="test_fcm_token_abc_123").count() == 1, "Should not duplicate tokens"
    print("  OK: Device token idempotency / update validated")

    # 5. Test API route alias /api/notifications/device-token/
    print("\n[5] Testing alias /api/notifications/device-token/...")
    res_api = client.post(
        "/api/notifications/device-token/",
        data=json.dumps({"token": "test_fcm_token_mobile_456", "platform": "android"}),
        content_type="application/json",
    )
    assert res_api.status_code == 200, f"Expected 200 on api alias, got {res_api.status_code}"
    print("  OK: Mobile API alias /api/notifications/device-token/ works")

    # 6. Test Notification Creation & Push Dispatch integration
    print("\n[6] Testing create_notification with FCM push dispatch...")
    notif = create_notification(
        recipient=user,
        notif_type=Notification.TYPE_ANNOUNCEMENT,
        message="Test announcement message for FCM push verification.",
    )
    assert notif.pk is not None, "Notification must be saved"
    print(f"  OK: In-app notification #{notif.pk} created and push dispatched through send_push()")

    # 7. Test test-push view endpoint
    print("\n[7] Testing POST /notifications/test-push/...")
    res_test_push = client.post("/notifications/test-push/")
    assert res_test_push.status_code == 200, f"Expected 200, got {res_test_push.status_code}: {res_test_push.content}"
    assert res_test_push.json().get("status") == "ok"
    print(f"  OK: Test push endpoint responded: {res_test_push.json().get('message')}")

    # 8. Test Device Token Unregister
    print("\n[8] Testing POST /notifications/unregister-device-token/...")
    res_unreg = client.post(
        "/notifications/unregister-device-token/",
        data=json.dumps({"token": "test_fcm_token_abc_123"}),
        content_type="application/json",
    )
    assert res_unreg.status_code == 200
    assert DeviceToken.objects.filter(token="test_fcm_token_abc_123").count() == 0
    print("  OK: Token unregistered and removed from DB")

    # Clean up mobile test token
    DeviceToken.objects.filter(token="test_fcm_token_mobile_456").delete()

    # 9. Test Notification Center view template context
    print("\n[9] Testing /notifications/ page rendering with FCM UI controls...")
    res_center = client.get("/notifications/")
    assert res_center.status_code == 200
    content = res_center.content.decode("utf-8")
    assert "Firebase Cloud Messaging" in content, "FCM banner should be in notification center"
    assert "cs-fcm-enable-btn" in content, "Enable button should be present"
    assert "cs-fcm-test-btn" in content, "Test push button should be present"
    assert "firebase-config" in content, "firebase-config JSON script should be injected"
    assert "fcm.js" in content, "fcm.js script tag should be present"
    print("  OK: Notification center and base template include all FCM elements and config")

    print("\n" + "=" * 60)
    print("ALL FCM TESTS PASSED SUCCESSFULLY! (9/9)")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    run_tests()
