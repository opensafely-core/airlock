class ActionDenied(Exception): ...


class APIException(Exception): ...


class RequestTimeout(Exception): ...


class WorkspaceNotFound(APIException): ...


class WorkspacePermissionDenied(APIException): ...


class ReleaseRequestNotFound(APIException): ...


class FileNotFound(APIException): ...


class FileReviewNotFound(APIException): ...


class InvalidStateTransition(APIException): ...


class RequestPermissionDenied(APIException): ...


class RequestReviewDenied(APIException): ...


class ManifestFileError(APIException): ...
