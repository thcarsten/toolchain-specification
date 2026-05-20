from rdflib import Graph
import yaml
from boltons.iterutils import remap

"""
- load_yaml
- merge_graphs
- collapse_ids
"""


def load_yaml(filename):
    with open(filename) as stream:
        try:
            return yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            print(exc)


def merge_graphs(graph_list: list[Graph], base="file:///workspace/pipeline/") -> Graph:
    output_graph = Graph()

    for graph_to_be_added in graph_list:
        # To deal with blank nodes, each graph has to be serialized and reparsed
        graph_turtle = graph_to_be_added.serialize(format="turtle", base=base)
        output_graph.parse(data=graph_turtle, format="turtle", publicID=base)

    return output_graph


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
