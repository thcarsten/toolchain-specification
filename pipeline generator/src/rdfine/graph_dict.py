from .prefix_store import PrefixStore
from .utils import merge_graphs
from rdflib import Graph
from boltons.iterutils import remap
import copy  # For deep copies
from glom import glom
from validators import url as is_url
import json
from pyld import jsonld
import yaml
import pandas as pd
import re


class GraphDict:
    """
    A Data Tree is an enhanced Python dict.
    It is called data tree because it assumes that the data it holds can be represented as a hierarchical, nested dictionary.
    All transformation methods are inplace. This is by design to support compilation logic by restructuring DataTrees.
    Also ALL transformations are recursive.
    So make sure to create a copy before performing destructive transformations

    Properties:
            - self.dict_data
            needed for rdf / graph serialization, but otherwise optional:
            - self.prefix_store (optional)
    Methods:
        Transformation (all recursive and inplace)
            - rename_key
            - drop_empty
            - _provide_prefixes
        Other
            - validate
            - copy
            - get
    """

    ###########################
    # I/O
    ###########################

    def __init__(
        self,
        source: Graph | dict,
        id: str | None = None,
        prefix_store: PrefixStore | None = None,
        embed: str = "@never",
        explicit: bool = False,
    ) -> None:
        """
        Exposes the graph as Python dictionary. Needs a node_id as starting point to build the nested structure.
        Used a Json-Ld frame to build the dictionary structure. Prefixes in dictionary keys are auto-compacted.
        Requires a reference node to a proper URI to work
        """

        # Input checks
        if isinstance(source, Graph):
            if not id:
                raise ReferenceError(
                    "A id is needed as reference when initiating a GraphDict from a Graph"
                )
        elif isinstance(source, dict):
            if "@id" not in source and not id:
                raise ReferenceError(
                    "Cannot initiate a GraphDict from a dict without an @id of the reference node."
                )
            elif "@context" not in source and not prefix_store:
                raise ReferenceError(
                    "Cannot initiate a GraphDict from a dict without a @context or prefix_store."
                )

        # Initiating some properties
        self.dict_data = {}
        self._placeholder_prefix = {"na_": "https://no_prefix_available.com/"}

        # Initiating from a graph:
        if isinstance(source, Graph):
            self.prefix_store = PrefixStore(source)

            # Return result as dict
            self.dict_data = self._graph_to_dict(
                graph=source, id=id, embed=embed, explicit=explicit
            )

        # Initiating from a dict:
        elif isinstance(source, dict):
            # Copying the dict
            self.dict_data = source.copy()

            # Making sure there is an id
            if id and "@id" not in self.dict_data:
                self.dict_data["@id"] = id

            # Making sure there is a prefix store
            if prefix_store:
                self.prefix_store = prefix_store
            else:
                self.prefix_store = PrefixStore(source.get("@context"))
                del self.dict_data["@context"]

    def _graph_to_dict(
        self, graph: Graph, id: str, embed: str = "@never", explicit: bool = False
    ) -> dict:
        """
        Turns a graph to dict through json framing
        """
        # Turning the node_id to a proper UriRef
        expanded_id = self.prefix_store.expand(id)
        # Define the frame
        frame = {
            "@id": expanded_id,  # reference node (statting point)
            "@embed": embed,  # Whether references to other uri's should be resolved and nested in the dict or only be referred to
            "@explicit": explicit,  # whether only explicitly named properties should be included in the frame
        }
        # Serialize RDF graph as JSON-LD
        json_expanded = json.loads(graph.serialize(format="json-ld", indent=4))
        # Apply the frame
        json_framed = jsonld.frame(json_expanded, frame)
        # Compact the dictionary
        json_compacted = self.prefix_store.compact(json_framed)

        return json_compacted

    ###########################
    # RECURSIVE TRANSFORMATIONS
    ###########################

    def rename_key(self, old_key: str, new_key: str) -> None:
        """
        Recursively replaces an old_key with a new_key
        """

        def visit(path, key, value):
            if key == old_key:
                return new_key, value
            else:
                return True

        self.dict_data = remap(self.dict_data, visit=visit)

    def drop_empty(self) -> None:
        """
        Recursively traverse the tree and drop any key-value pairs,
        where the value is empty (either None, [] or {}).
        """
        empty_values = [None, [], {}]

        def visit(path, key, value):
            if value in empty_values:
                return False
            else:
                return True

        self.dict_data = remap(self.dict_data, visit=visit)

    def prune_ids(self) -> None:  # drop or keep
        """
        Will not recursively follow and named node that occurs in an id list. Allows to specify
        Which ids should be included in this pruning (if action is drop) or
        which ids should be ignored for this pruning (if action is keep) .
        """

        def visit(path, key, value):
            # If value is a dict and has @id in prune list → prune it
            if isinstance(value, dict) and "@id" in value:
                return key, {"@id": value["@id"]}
            else:
                return key, value

        self.dict_data = remap(self.dict_data, visit=visit)

    ###########################
    # OTHER
    ###########################

    def copy(self):
        return copy.deepcopy(self)

    def validate(self, json_schema: dict) -> None:
        """
        Validate the DataTree with a json schema.
        Will throw an exception if validation fails, otherwise returns true
        """
        pass

    def get(self, path) -> dict:
        """
        Get is a straightforward glom-wrapper
        """
        return glom(self.dict_data, path)

    def get_branch(self, id) -> dict:
        """
        Returns the branch of the graph_dict which corresponds to the provided node id.
        """
        # Finding the corresponding path (takes the shortest path by default)
        df_paths = self.find("@id$", id)
        df_paths["path_length"] = df_paths["path"].str.len()
        shortest_row = df_paths.nsmallest(1, "path_length")
        branch_path = shortest_row["path"].to_list()[0]

        if branch_path == "@id":
            branch_path_normalized = "@id"
        elif branch_path.endswith(".@id"):
            branch_path_normalized = branch_path.removesuffix(".@id")
        else:
            raise Exception(f"path {branch_path} could not be resolved.")

        return self.get(branch_path_normalized)

    def to_graph(self, path: str | None = None) -> Graph:
        """
        Returns GraphDict as graph.
        Optionally allows to provide a path, if only a subset of the GraphDict needs to be returned as graph.
        """

        output_graph = Graph()

        # Extract data via path if provided
        if not path:
            graph_obj = self.dict_data
        else:
            graph_obj = self.get(path=path)

        # Safe typing to make subsequent conversion smooth
        if isinstance(graph_obj, dict):
            graph_list = [graph_obj]
        elif isinstance(graph_obj, list):
            graph_list = graph_obj
        else:
            raise TypeError(f"Cannot convert data of type {type(graph_obj)} to graph.")

        # Now looping through each graph dict and adding their triples to graph
        for graph_dict in graph_list:
            if not isinstance(graph_dict, dict):
                raise TypeError(
                    f"Expected dict to extract triples, received {type(graph_dict)} instead."
                )
            elif graph_dict.get("@id") is None or not self.prefix_store:
                raise LookupError("Cannot turn to jsonld, @id or PrefixStore missing.")
            else:
                # Here the actual parsing of triples takes place
                jsonld_dict = self.prefix_store.expand(graph_dict)
                jsonld_dict["@context"] = dict(self.prefix_store.prefixes)
                jsonld_string = json.dumps(jsonld_dict)
                output_graph.parse(data=jsonld_string, format="json-ld")

        self.prefix_store.bind_to_namespace(output_graph)
        return output_graph

    def provide_prefixes(self, only_keys: bool = True) -> None:
        """
        The opposite of drop prefixes:
        Any key without a known prefix gets a placeholder prefix prepended.
        Skips any keys starting with '@'. By default values remain unchanged.
        """

        missing_prefix = self._placeholder_prefix

        placeholder_prefix = list(missing_prefix.keys())[0]
        self.prefix_store.load(missing_prefix, replace=False)

        def visit(path, key, value):
            # Default: no change
            new_key = key
            new_value = value

            key_is_special_flag = False  # Need this to propagate the logic to the value
            # Only append a prefix to the key if some conditions are met
            if isinstance(key, str):
                if not key.startswith("@"):
                    key_prefix = self.prefix_store.fetch_prefix(key)
                    if not key_prefix and not is_url(key):
                        new_key = placeholder_prefix + ":" + key
                else:
                    key_is_special_flag = True

            if not only_keys:
                # Only append a prefix to the value if some conditions are met
                if isinstance(value, str):
                    if (
                        not key_is_special_flag
                    ):  # If the key starts with '@', I want the value unchanged
                        value_prefix = self.prefix_store.fetch_prefix(value)
                        if not value_prefix and not is_url(value):
                            new_value = placeholder_prefix + ":" + value

            return new_key, new_value

        self.dict_data = remap(self.dict_data, visit=visit)

    # Ensures that multilines are printed in pretty multiline format
    def _pretty_multiline(dumper, data):
        if "\n" in data:  # detect multiline
            data = data.replace("\r\n", "\n")
            return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
        return dumper.represent_scalar("tag:yaml.org,2002:str", data)

    yaml.add_representer(str, _pretty_multiline)

    def serialize(self, format: str, prefixes: str | None = "compact"):
        """
        Interface serialization function that can convert a graph into various formats.
        Serialization is allowed to be destructive, i.e. the graph may not be recovered from this.
        formats:
            - ttl
            - dict
            - json
            - yaml
        prefixes:
            - expand
            - compact
            - drop

        """

        # Inventory of the function arguments
        graph_dict_formats = ["dict", "json", "yaml", "yml"]
        supported_formats = [
            "dict",
            "json",
            "yaml",
            "yml",
        ]
        supported_prefix_actions = ["expand", "compact", "drop"]

        # Checking for proper function arguments
        if format not in supported_formats:
            raise ValueError(f"format not in {supported_formats}")
        elif prefixes is not None and prefixes not in supported_prefix_actions:
            raise ValueError(f"prefixes not in {supported_prefix_actions}")

        dict_data = self.dict_data
        dict_data = self.prefix_store.apply_prefixes(dict_data, prefixes)

        match format:
            case "dict":
                return dict_data
            case "json":
                return json.dumps(dict_data)
            case "yaml" | "yml":
                return yaml.dump(dict_data, default_flow_style=False, sort_keys=False)

        # Converts a json-type dictionary into a flattened dataframe

    def _create_index(self, sep=".") -> pd.DataFrame:
        """
        Flattens a deeply nested structure (dicts + lists) into a DataFrame
        with columns: 'path' and 'value'.

        - Dict keys are used as path segments
        - List indices are used as numeric path segments
        - Only leaf values (non-dict, non-list) are returned
        """

        rows = []

        def walk(node, path):
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, path + [str(k)])
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, path + [str(i)])
            else:
                # Leaf node
                rows.append({"path": sep.join(path), "value": node})

        walk(self.dict_data, [])
        return pd.DataFrame(rows)

    def find(self, path_pattern=None, value_pattern=None):
        """
        Filter a DataFrame with columns ['path', 'value'] using regex.
        Always uses AND logic and is case-insensitive.

        Parameters:
        - path_pattern: regex applied to 'path' (optional)
        - value_pattern: regex applied to 'value' (optional)

        Returns:
        - Filtered DataFrame
        """

        df = self._create_index()

        if path_pattern is None and value_pattern is None:
            return df.copy()

        mask = pd.Series(True, index=df.index)

        if path_pattern is not None:
            mask &= df["path"].str.contains(
                path_pattern, case=False, regex=True, na=False
            )

        if value_pattern is not None:
            mask &= (
                df["value"]
                .astype(str)
                .str.contains(value_pattern, case=False, regex=True, na=False)
            )

        output_df = df[mask].copy()
        output_df = output_df.reset_index(drop=True)
        return output_df

    def set(self, path: str, new_value, sep=".") -> None:
        """
        Strict in-place update of a nested dict/list structure using a path.

        Parameters:
        - path: string path (e.g. ":a.b.0.c")
        - new_value: The value that should be set to the path
        - sep: path separator (default ".")

        Rules:
        - Path must exist (no creation)
        - Numeric segments = list indices
        - String segments = dict keys
        - Update happens at the final resolved node
        """

        data = self.dict_data

        parts = path.split(sep)
        current = data

        # Traverse to parent of target
        for part in parts[:-1]:
            if part.isdigit():
                idx = int(part)
                if not isinstance(current, list):
                    raise TypeError(
                        f"Expected list at '{part}', got {type(current).__name__}"
                    )
                if idx >= len(current):
                    raise IndexError(f"Index {idx} out of range")
                current = current[idx]
            else:
                if not isinstance(current, dict):
                    raise TypeError(
                        f"Expected dict at '{part}', got {type(current).__name__}"
                    )
                if part not in current:
                    raise KeyError(f"Key '{part}' not found")
                current = current[part]

        # Resolve final step
        last = parts[-1]

        if last.isdigit():
            idx = int(last)
            if not isinstance(current, list):
                raise TypeError(
                    f"Expected list at final segment, got {type(current).__name__}"
                )
            if idx >= len(current):
                raise IndexError(f"Index {idx} out of range")

            current[idx] = new_value

        else:
            if not isinstance(current, dict):
                raise TypeError(
                    f"Expected dict at final segment, got {type(current).__name__}"
                )
            if last not in current:
                raise KeyError(f"Key '{last}' not found")

            current[last] = new_value

    def add_triples(self, new_triples: Graph, path_to_id: str = "@id") -> None:
        """
        Adds triples from a graph to the Graph dict. Optionally only adds it to a branch,
        by providing the path to the id of the branch.
        """

        id = self.get(path_to_id)
        if not isinstance(id, str):
            raise Exception(
                f"path to id has to point to an id of type str, got {type(id)} instead."
            )

        # Turning the branch to a graph
        if path_to_id == "@id":
            branch_graph = self.to_graph()
        elif path_to_id.endswith(".@id"):
            branch_graph = self.to_graph(path_to_id.removesuffix(".@id"))
        else:
            raise Exception("path to id has to end on '@id'")

        # Creating a dict with the new triples
        branch_graph = merge_graphs([branch_graph, new_triples])
        branch_dict = self._graph_to_dict(
            branch_graph, id=id, embed="@always"
        )  # You always want to fully embed all triples of branch (i.e. full recovery of the triples that were already there)

        # Overwriting th existing branch with the new triples
        if path_to_id == "@id":
            self.dict_data = branch_dict
        elif path_to_id.endswith(".@id"):
            self.set(path_to_id.removesuffix(".@id"), branch_dict)

    ###########################
    # DUNDER METHODS
    ###########################

    # String shown upon print
    def __repr__(self):
        return f"GraphDict(\n{str(self.dict_data)})"

    # Indexing prefix_store returns indexed self.dict_data
    def __getitem__(self, key):
        return self.dict_data[key]

    def __setitem__(self, key, newvalue):
        self.dict_data[key] = newvalue

    def __delitem__(self, key):
        del self.dict_data[key]
