from rdflib import Literal, URIRef, BNode, Graph

"""
type conversion:
- xsd_to_python
- node_to_str
"""


def merge_graphs(graph_list: list[Graph]) -> Graph:
    output_graph = Graph()

    for graph_to_be_added in graph_list:
        # To deal with blank nodes, each graph has to be serialized and reparsed
        graph_turtle = graph_to_be_added.serialize(format="turtle")
        output_graph.parse(data=graph_turtle, format="turtle")

    return output_graph


###########################
# TYPE CONVERSIONS
###########################


def xsd_to_python(obj):
    """
    Recursively convert JSON-LD xsd-typed literals to native Python types.
    Returns a deep copy of obj with converted values.
    """

    xsd_to_python_mapping = {
        "xsd:boolean": lambda v: (
            bool(v) if isinstance(v, bool) else v in [True, "true", "1", "True"]
        ),
        "xsd:integer": int,
        "xsd:int": int,
        "xsd:float": float,
        "xsd:double": float,
        "xsd:string": str,
        "xsd:date": str,
        "xsd:dateTime": str,
    }

    if isinstance(obj, dict):
        # handle xsd value objects
        if "@value" in obj and "@type" in obj:
            xsd_type = obj["@type"]
            value = obj["@value"]
            mapping = xsd_to_python_mapping.get(xsd_type, lambda x: x)
            try:
                return mapping(value)
            except Exception:
                # fallback to original value if conversion fails
                return value
        # otherwise recursively process all dict items
        return {k: xsd_to_python(v) for k, v in obj.items()}

    elif isinstance(obj, list):
        # recursively process list elements
        return [xsd_to_python(item) for item in obj]
    else:
        # primitive types stay as-is
        return obj


def node_to_str(cell) -> str:
    """
    Converts a RDFlibType to a simple string type
    Does some cleaning to remove trailing symbols
    """
    # If node is a UriRef
    if isinstance(cell, URIRef) or isinstance(cell, BNode):
        simplified_cell = cell.n3()
        if isinstance(simplified_cell, str):
            if len(simplified_cell) >= 2:  # Prevents bugs
                if simplified_cell[0] == '"' and simplified_cell[-1] == '"':
                    simplified_cell = simplified_cell[1:-1]
            if len(simplified_cell) >= 2:
                if simplified_cell[0] == "<" and simplified_cell[-1] == ">":
                    simplified_cell = simplified_cell[1:-1]
            simplified_cell = simplified_cell.strip()
        return simplified_cell
    # Convert to native python primitives if cell is literal
    elif isinstance(cell, Literal):
        return cell.toPython()
    else:
        return cell
