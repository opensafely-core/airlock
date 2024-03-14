import dataclasses


@dataclasses.dataclass(frozen=True)
class User:
    """
    A datastructure to manage users.
    Information about a user is stored on the session. This
    is a convenience for passing that information around.
    """

    id: int
    username: str
    workspaces: dict = dataclasses.field(default_factory=dict)
    output_checker: bool = dataclasses.field(default=False)

    def __post_init__(self):
        assert isinstance(self.workspaces, dict)

    @classmethod
    def from_session(cls, session_data):
        user = session_data.get("user")
        if user is None:
            return
        workspaces = user.get("workspaces", dict())
        output_checker = user.get("output_checker", False)
        return cls(user["id"], user["username"], workspaces, output_checker)

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "workspaces": self.workspaces,
            "output_checker": self.output_checker,
        }

    def has_permission(self, workspace_name):
        return (
            # Output checkers can view all workspaces
            self.output_checker or self.can_create_request(workspace_name)
        )

    def can_create_request(self, workspace_name):
        # Only users with explict access to the workspace can create release
        # requests.
        return workspace_name in self.workspaces

    def is_authenticated(self):
        return True
