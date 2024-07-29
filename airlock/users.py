import dataclasses
import time
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

    def is_authenticated(self):
        return True
