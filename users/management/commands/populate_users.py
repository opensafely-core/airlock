from django.core.management.base import BaseCommand

from local_db.models import AuditLog
from users import login_api
from users.models import User


class Command(BaseCommand):
    def handle(self, **kwargs):
        # should be anyone whose done anything in airlock, ever
        users = AuditLog.objects.values_list("user", flat=True).distinct()
        n = len(users)

        for i, user in enumerate(users):
            print(f"{i + 1}/{n}: {user}")
            api_data = login_api.get_user_authz_prod(user)
            User.from_api_data(api_data)
