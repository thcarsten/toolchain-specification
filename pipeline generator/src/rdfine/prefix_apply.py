"""
Polymorphic prefix-application helpers.

These free functions take a :class:`~rdfine.prefix_store.PrefixStore` plus a
carrier (string, dict, list, or DataFrame) and apply ``compact`` / ``expand`` /
``drop`` to every string they find. ``include_in_query`` is grouped here as
well because a SPARQL query is just another string carrier that needs the
registry baked into it.

Implementations delegate the actual string transformations to the store's
``compact_string`` / ``expand_string`` / ``drop_string`` methods, so the
store remains the single source of truth for the registry.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from .prefix_store import PrefixStore


# Matches a SPARQL/Turtle ``PREFIX foo:`` line (case-insensitive) and captures
# the prefix name. Used by :func:`include_in_query` to skip duplicates.
_PREFIX_LINE_RE = re.compile(r"^\s*PREFIX\s+([^\s:]+)\s*:", re.IGNORECASE)


_SUPPORTED_ACTIONS = ("compact", "expand", "drop")


def apply_prefixes(data, store: "PrefixStore", action: str):
    """
    Dispatcher: apply ``action`` to every string inside ``data``.

    Accepted actions: ``"compact"``, ``"expand"``, ``"drop"``.
    Accepted carriers: ``str``, ``pd.DataFrame``, ``list``, ``dict``.
    Anything else is returned unchanged.
    """
    if action not in _SUPPORTED_ACTIONS:
        raise ValueError(f"action not in {_SUPPORTED_ACTIONS}")

    str_op = _str_op_for(store, action)

    if isinstance(data, str):
        return str_op(data)
    if isinstance(data, pd.DataFrame):
        return data.map(lambda v: str_op(v) if isinstance(v, str) else v)
    if isinstance(data, (list, dict)):
        return _apply_to_object(data, str_op)
    return data


def _str_op_for(store: "PrefixStore", action: str):
    return {
        "compact": store.compact_string,
        "expand": store.expand_string,
        "drop": store.drop_string,
    }[action]


def _apply_to_object(obj, str_op):
    """Recursively apply ``str_op`` to every string inside dicts/lists."""
    if isinstance(obj, dict):
        return {
            (str_op(k) if isinstance(k, str) else k): _apply_to_object(v, str_op)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_apply_to_object(item, str_op) for item in obj]
    if isinstance(obj, str):
        return str_op(obj)
    return obj


def include_in_query(query: str, store: "PrefixStore") -> str:
    """
    Prepend ``PREFIX`` declarations to ``query`` for every prefix in ``store``
    that isn't already declared inline. Returns the augmented query string.
    """
    existing: set[str] = set()
    for line in query.splitlines():
        match = _PREFIX_LINE_RE.match(line)
        if match:
            existing.add(match.group(1))

    prefix_lines = [
        f"PREFIX {prefix}: <{url}>"
        for prefix, url in store.prefixes.items()
        if prefix not in existing
    ]
    if not prefix_lines:
        return query
    return "\n".join(prefix_lines) + "\n" + query
