"""Business errors raised by commerce services."""


class RetailError(Exception):
    """Base class for retail business errors."""


class NotFoundError(RetailError):
    """A requested resource does not exist or is not owned by the user."""


class ConflictError(RetailError):
    """The resource state does not permit the requested action."""


class ValidationError(RetailError):
    """The request violates a retail business rule."""


class InsufficientBalanceError(ValidationError):
    """A gift card cannot cover the price difference."""
