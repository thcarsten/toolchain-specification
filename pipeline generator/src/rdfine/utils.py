from rdflib import Graph
import yaml
from boltons.iterutils import remap
from copy import deepcopy
import textwrap


def load_yaml(filename):
    with open(filename) as stream:
        try:
            return yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            print(exc)


def collapse_ids(obj):
    """
    Recursively traverse nested dicts/lists and replace any dict of the exact form
    {'@id': '...'} with just the string value.
    """

    def visit(path, key, value):
        if isinstance(value, dict) and set(value.keys()) == {"@id"}:
            # Replace dicts that contain exactly one key: '@id'
            return key, value["@id"]
        return True  # continue traversal

    return remap(obj, visit=visit)


def drop_empty(obj, empty_values=[None, [], {}, ""]):
    """
    Recursively traverse the tree and drop any key-value pairs,
    where the value is empty (either None, '', [] or {}).
    """

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

        dict_data[":config"] = yaml.load(literal_value, Loader=yaml.FullLoader)
    elif "tcs:embedded" in dict_data:
        embedded_value = dict_data["tcs:embedded"]
        del dict_data["tcs:embedded"]
        dict_data[":config"] = embedded_value
    else:
        raise LookupError(
            "Neither predicates 'tcs:embedded' nor 'tcs:literal' found in data."
        )

    return dict_data
