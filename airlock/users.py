import dataclasses
from typing import Tuple


@dataclasses.dataclass(frozen=True)
class User:
    """
    A datastructure to manage users.
    Information about a user is stored on the session. This
    is a convenience for passing that information around.
    """

    id: int
    username: str
    workspaces: Tuple = dataclasses.field(default_factory=tuple)
    output_checker: bool = dataclasses.field(default=False)

    @classmethod
    def from_session(cls, session_data):
        user = session_data.get("user")
        if user is None:
            return
        workspaces = tuple(user.get("workspaces", tuple()))
        output_checker = user.get("output_checker", False)
        return cls(user["id"], user["username"], workspaces, output_checker)

    def has_permission(self, workspace_name):
        return (
            # Output checkers can view all workspaces
            self.output_checker or workspace_name in self.workspaces
        )

    def is_authenticated(self):
        return True
