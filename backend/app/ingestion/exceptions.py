"""Errors raised while discovering or registering source files."""


class IngestionError(Exception):
    """Base error. The message is safe to return to a local caller."""


class IngestionPathError(IngestionError):
    """The requested directory is missing or leaves the configured input root."""


class IngestionIOError(IngestionError):
    """A file could not be read."""


class IngestionRegistrationError(IngestionError):
    """PostgreSQL rejected a bid or document registration."""


class UnsupportedDocumentTypeError(IngestionError):
    """The registered document type has no parser."""
