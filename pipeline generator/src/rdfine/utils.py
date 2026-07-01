import textwrap
from copy import deepcopy

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


def parse_config(input_dict: dict) -> dict:
    """
    Parses a dict containing a "tcs:literal" or "tcs:embedded"
    """

    dict_data = deepcopy(input_dict)

    if "tcs:literal" in dict_data:
        literal_value = dict_data["tcs:literal"]
        del dict_data["tcs:literal"]

        # Clean up trailing double_quotes
        literal_value = literal_value.removeprefix('""')
        literal_value = literal_value.removesuffix('""')

        # Removing '\\r' at the end of a line
        lines = literal_value.splitlines()
        lines = [line.removesuffix("\\r") for line in lines]
        literal_value = "\n".join(lines)
        literal_value = textwrap.dedent(literal_value).strip()

        dict_data[":config"] = yaml.safe_load(literal_value)
    elif "tcs:embedded" in dict_data:
        embedded_value = dict_data["tcs:embedded"]
        del dict_data["tcs:embedded"]
        dict_data[":config"] = embedded_value
    else:
        raise LookupError(
            "Neither predicates 'tcs:embedded' nor 'tcs:literal' found in data."
        )

    return dict_data
