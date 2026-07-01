"""Internal utilities shared across compilers."""

from collections.abc import Iterable


def receive_first(values: Iterable):
    """
    Return the first element of ``values``, raising :class:`LookupError`
    if it is empty.

    Use when an upstream filter / SPARQL is expected to produce at least
    one row and the caller takes the first. Converts the bare
    ``IndexError`` that used to leak through ``.to_list()[0]`` into a
    meaningful, actionable error.
    """
    seq = list(values)
    if not seq:
        raise LookupError("expected at least one result, got none")
    return seq[0]
