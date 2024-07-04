import dataclasses
import time
from enum import Enum
from typing import Any


@dataclasses.dataclass(frozen=True)
class User:
    """
    A datastructure to manage users.
    Information about a user is stored on the session. This
    is a convenience for passing that information around.
    """

    username: str
    workspaces: dict[str, Any] = dataclasses.field(default_factory=dict)
    output_checker: bool = dataclasses.field(default=False)
    last_refresh: float = dataclasses.field(default_factory=time.time)

    class ActionDeniedReason(Enum):
        NO_PERMISSION = "NO_PERMISSION"
        WORKSPACE_ARCHIVED = "WORKSPACE_ARCHIVED"
        PROJECT_INACTIVE = "PROJECT_INACTIVE"

    def __post_init__(self):
        assert isinstance(self.workspaces, dict)

    @classmethod
    def from_session(cls, session_data):
        user = session_data.get("user")
        if user is None:
            return
        workspaces = user.get("workspaces", dict())
        output_checker = user.get("output_checker", False)
        last_refresh = user.get("last_refresh", time.time())
        return cls(user["username"], workspaces, output_checker, last_refresh)

    def to_dict(self):
        return {
            "username": self.username,
            "workspaces": self.workspaces,
            "output_checker": self.output_checker,
            "last_refresh": self.last_refresh,
        }

    def has_permission(self, workspace_name):
        return (
            # Output checkers can view all workspaces
            # Authors can view all workspaces they have access to (regardless
            # of archive or project ongoing status)
            self.output_checker or workspace_name in self.workspaces
        )

    def can_action_request(self, workspace_name):
        # Only users with explict access to the workspace can create/modify release
        # requests.
        if workspace_name not in self.workspaces:
            return False, self.ActionDeniedReason.NO_PERMISSION
        # Requests for archived workspaces cannot be created/modified
        if self.workspaces[workspace_name]["archived"]:
            return False, self.ActionDeniedReason.WORKSPACE_ARCHIVED
        # Requests for workspaces in not-ongoing projects cannot be created/modified
        if not self.workspaces[workspace_name]["project_details"]["ongoing"]:
            return False, self.ActionDeniedReason.PROJECT_INACTIVE

        return True, None

    def is_authenticated(self):
        return True
