from typing import Optional

from botocore.exceptions import ConnectTimeoutError, EndpointConnectionError, ReadTimeoutError


class AwsOperationError(Exception):
    """Wrap AWS SDK errors with operation context."""

    def __init__(
        self,
        operation: str,
        error: Exception,
        region: Optional[str] = None,
        profile: Optional[str] = None,
    ) -> None:
        self.operation = operation
        self.error = error
        self.region = region
        self.profile = profile
        super().__init__(str(error))


def is_skippable_region_error(error: Exception) -> bool:
    """Return True when a per-region error should not fail the entire lookup."""
    original_error = error.error if isinstance(error, AwsOperationError) else error
    return isinstance(original_error, (ConnectTimeoutError, EndpointConnectionError, ReadTimeoutError))

