from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from local_db.models import RequestMetadata
from users.auth import Level4AuthenticationBackend
from users.models import User


class Command(BaseCommand):
    """
    Backpopulate release requests with project and organisation information
    retrieved from the job-server API for their author
    """

    def handle(self, **kwargs):
        retrieved_users: dict[str, User] = {}
        auth_backend = Level4AuthenticationBackend()
        # Retrieve requests with no project or org information
        for request in RequestMetadata.objects.filter(
            Q(project="") | Q(organisations="")
        ):
            user = retrieved_users.get(request.author)
            if user is None:
                user = auth_backend.create_or_update(request.author, force_refresh=True)
                if user is None or request.workspace not in user.workspaces:
                    self.stdout.write(
                        f"Error updating request {request.id}: Could not retrieve information for workspace from API for user '{request.author}'"
                    )
                    continue
                retrieved_users[user.user_id] = user

            project = user.get_project_for_workspace(request.workspace)
            with transaction.atomic():
                request.project = project["name"]
                request.organisations = ",".join(project["orgs"])
                request.save()
