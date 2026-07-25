from enum import Enum

class ErrorKind(str, Enum):
    auth = "auth"; quota = "quota"; billing = "billing"; rate_limit = "rate_limit"
    model_missing = "model_missing"; content_filter = "content_filter"
    network = "network"; timeout = "timeout"; resource = "resource"
    dependency = "dependency"; bug = "bug"

class AvgError(Exception):
    def __init__(self, kind: ErrorKind, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.kind, self.message, self.detail = kind, message, detail
