"""Application errors with safe, user-facing semantics."""


class PingentError(Exception):
    """Base error for expected Pingent failures."""


class AuthorizationError(PingentError):
    """Raised when a Telegram user is not locally authorized."""


class BusySessionError(PingentError):
    """Raised when a session already has an active task."""


class WorkspaceNotFoundError(PingentError):
    """Raised when a requested workspace is not configured locally."""


class TaskNotFoundError(PingentError):
    """Raised when a requested pending task number is unavailable."""


class CodexError(PingentError):
    """Raised for a failed Codex app-server operation."""


class CodexSessionUnavailableError(CodexError):
    """Raised when a stored Codex thread cannot be resumed locally."""


class CodexTurnInterruptedError(CodexError):
    """Raised when Codex confirms that an active turn was interrupted."""


class ApprovalRequiredError(CodexError):
    """Raised when Codex requests approval not implemented by this slice."""


class ApprovalNotFoundError(PingentError):
    """Raised when an approval callback is expired or does not belong to the user."""


class WebUserAlreadyExistsError(PingentError):
    """Raised when a Web SPA username is already registered locally."""
