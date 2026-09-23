"""Authentication backends for Class Sync."""
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model

UserModel = get_user_model()


class CaseInsensitiveModelBackend(ModelBackend):
    """
    Allows case-insensitive username authentication (e.g. 23BCS1001 or 23bcs1001).
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if not username or password is None:
            return None
        try:
            user = UserModel.objects.get(**{f"{UserModel.USERNAME_FIELD}__iexact": username})
        except UserModel.DoesNotExist:
            return None
        except UserModel.MultipleObjectsReturned:
            user = UserModel.objects.filter(**{f"{UserModel.USERNAME_FIELD}__iexact": username}).order_by("id").first()

        if user and user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
