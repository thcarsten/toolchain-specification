from collections.abc import Iterable

import yaml
from boltons.iterutils import remap


def load_yaml(filename):
    """
    Load a YAML file and return its parsed contents.

    Any ``yaml.YAMLError`` raised during parsing is propagated to the caller.
    """
    with open(filename, encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def drop_empty(obj, empty_values=None):
    """
    Recursively traverse the tree and drop any key-value pairs,
    where the value is empty (either None, '', [] or {}).
    """
    if empty_values is None:
        empty_values = [None, [], {}, ""]

    def visit(path, key, value):
        if value in empty_values:
            return False
        else:
            return True

    return remap(obj, visit=visit)


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
