from django.core.management.base import BaseCommand
from django.db import transaction

from airlock.business_logic import AuditEventType, bll


WIDTH = max(len(k.name) for k in AuditEventType)


class Command(BaseCommand):
    """
    Show audit log
    """

    def add_arguments(self, parser):
        parser.add_argument("-u", "--user", help="Filter by user", default=None)
        parser.add_argument(
            "-w", "--workspace", help="Filter by workspace", default=None
        )
        parser.add_argument(
            "-r", "--request", help="Filter by request id", default=None
        )

    @transaction.atomic()
    def handle(self, *args, **options):
        audit_log = bll.get_audit_log(
            user=options["user"],
            workspace=options["workspace"],
            request=options["request"],
        )

        for log in audit_log:
            if log.created_at:
                ts = log.created_at.isoformat()[:-13]  # seconds precision
            else:
                ts = "TIME UNKNOWN"
            msg = f"{ts}: {log.type.name:<{WIDTH}} user={log.user} workspace={log.workspace} request={log.request}"

            if log.path:
                msg += f" path={log.path}"

            self.stdout.write(msg + "\n")
