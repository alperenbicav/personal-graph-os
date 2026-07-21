"""Domain-level errors raised when an invariant is violated."""

from __future__ import annotations


class DomainError(Exception):
    """Base type for every error raised by the domain or application layer."""


class InvariantViolationError(DomainError):
    """Raised when an entity would be constructed or mutated into an invalid state."""


class UnknownSchemaReferenceError(DomainError):
    """Raised when an entity references a node type, field, status, or edge type that
    does not exist in the owning workspace's schema."""


class FieldValueTypeError(DomainError):
    """Raised when a field value does not match its field definition's declared type."""


class SchemaEditConflictError(DomainError):
    """Raised when a schema edit or removal would invalidate existing node/edge data."""
