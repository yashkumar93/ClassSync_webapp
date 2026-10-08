"""Management command to test Firebase Cloud Messaging (FCM) push notification delivery."""
from django.core.management.base import BaseCommand
from core.models import User
from notifications.models import DeviceToken
from notifications.services import send_push, initialize_firebase


class Command(BaseCommand):
    help = "Send a test FCM push notification to a user or all users with registered device tokens."

    def add_arguments(self, parser):
        parser.add_argument(
            "--user",
            type=str,
            help="Username of the user to send the test notification to.",
        )
        parser.add_argument(
            "--message",
            type=str,
            default="This is a test notification from Class Sync FCM!",
            help="Message body for the notification.",
        )
        parser.add_argument(
            "--title",
            type=str,
            default="Class Sync — Test Notification",
            help="Title for the notification.",
        )

    def handle(self, *args, **options):
        username = options.get("user")
        message = options["message"]
        title = options["title"]

        app = initialize_firebase()
        if not app:
            self.stdout.write(self.style.ERROR("Firebase Admin SDK could not be initialized. Check FIREBASE_CREDENTIALS_PATH in .env."))
            return

        self.stdout.write(self.style.SUCCESS(f"Firebase Admin SDK initialized successfully ({app.name})."))

        if username:
            try:
                user = User.objects.get(username=username)
            except User.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"User '{username}' does not exist."))
                return

            tokens = DeviceToken.objects.filter(user=user)
            if not tokens.exists():
                self.stdout.write(self.style.WARNING(f"User '{username}' has no registered FCM device tokens."))
                return

            self.stdout.write(f"Sending push notification to {tokens.count()} token(s) for user '{username}'...")
            send_push(user, message, title=title, data={"type": "cli_test"})
            self.stdout.write(self.style.SUCCESS("Push notification dispatched!"))
        else:
            total_tokens = DeviceToken.objects.count()
            if total_tokens == 0:
                self.stdout.write(self.style.WARNING("No device tokens found in the database. Open the web app and grant notification permission first."))
                return

            users = User.objects.filter(device_tokens__isnull=False).distinct()
            self.stdout.write(f"Sending push notification to {users.count()} user(s) ({total_tokens} total tokens)...")
            for u in users:
                send_push(u, message, title=title, data={"type": "cli_test"})
            self.stdout.write(self.style.SUCCESS("Push notifications dispatched to all registered devices!"))
