"""Shared service-layer exceptions."""


class ServiceNotFoundError(Exception):
    """Raised when a requested user-owned service resource is not found."""


class ServiceStateError(Exception):
    """Raised when a resource is not in a valid state for the requested action."""
