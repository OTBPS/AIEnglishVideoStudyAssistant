"""Errors the application layer exposes. The HTTP layer maps them to status codes."""

from __future__ import annotations


class NotFoundError(LookupError):
    """The requested video, transcript or session does not exist."""


class StorageReadOnlyError(RuntimeError):
    """The learning database belongs to a newer VideoTutor and must not be written."""


class InvalidInputError(ValueError):
    """Input that is well-formed but cannot be used (e.g. captions with no text)."""
