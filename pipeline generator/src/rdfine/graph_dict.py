from .prefix_store import PrefixStore
from rdflib import Graph
from boltons.iterutils import remap
from copy import deepcopy  # For deep copies
from glom import glom
from validators import url as is_url
import json
from pyld import jsonld
import yaml
import pandas as pd
import re
from typing import Self


class GraphDict:
    """
    A GraphDict that represents a JsonLd. It is always a dict bc even lists are embedded in a dict (via @graph)

    Properties:
            - dict
            - prefix_store
            - graph (Graph view of the GraphDict)
            - _placeholder_prefix
            - _basepath
    Public methods:
            - add (TBA)
            - describe (TBA)
            - extract_frame (TBA)
            - find
            - frame
            - get
            - rename (TBA)
            - serialize
            - set
    Private methods:
            - _provide_prefixes
            - _create_index
            - _find_path_to_id (TBA)
    """

    ###########################
    # Class variables
    ###########################

    _placeholder_prefix = {"na_": "https://no_prefix_available.com/"}
    _basepath = "file:///workspace/pipeline/"

    ###########################
    # Constructors
    ###########################

    def __init__(
        self, input_dict: dict, prefix_store: PrefixStore | None = None
    ) -> None:

        if isinstance(input_dict, dict) == False:
            raise Exception("Init expects dict unless GraphDict.from_graph is used.")

        # Assigning a prefix store
        if prefix_store:
            self.prefix_store = prefix_store
        elif "@context" in input_dict.keys():
            self.prefix_store = PrefixStore(input_dict["@context"])
        else:
            raise Exception(
                "To construct a GraphDict from a dict, either a PrefixStore or @context is required"
            )

        # Assigning the dictionary
        self.dict = deepcopy(input_dict)

        # Doing some cleanup
        if "@context" in self.dict.keys():
            del self.dict["@context"]
        # The dict is always compacted by default
        self.dict = self.prefix_store.compact(self.dict)
        # Any keys without proper prefixes are assigned placeholder prefixes
        self._provide_prefixes()

    @classmethod
    def from_graph(cls, graph: Graph) -> Self:

        # Convert graph to a generic jsonld object
        input_dict = json.loads(graph.serialize(format="json-ld", base=cls._basepath))
        if isinstance(input_dict, list):
            input_dict = {"@graph": input_dict}

        input_prefix_store = PrefixStore(graph)

        return cls(input_dict, input_prefix_store)

    ###########################
    # Public
    ###########################

    @property
    def graph(self) -> Graph:

        output_graph = Graph()

        # Here the actual parsing of triples takes place
        jsonld_dict = deepcopy(self.dict)
        jsonld_dict = self.prefix_store.expand(jsonld_dict)
        # jsonld_dict["@context"] = dict(self.prefix_store.prefixes)
        jsonld_string = json.dumps(jsonld_dict)
        output_graph.parse(data=jsonld_string, format="json-ld")
        self.prefix_store.bind_to_namespace(output_graph)

        return output_graph

    def add(self) -> Self | None:
        """
        Add triples of a Graph to the GraphDict

        Here is the concept:
        - Find each distinct sub in the input graph
        - Per sub:
            - Use a _find_path_to_id-function to find the path sub-related triples should be added to.
            - The _find_path_to_id-function should find the single path that
                - is closest to the root node
                - has already other keys in it
            - Add all triples that have sub as sub to the path. This can be done in the following way:
                - use .get to get the subdict of the sub-specific path
                - use a extract_frame function to extract a frame corresponding to the subdict
                - Convert the subdict to a graph.
                - Add the sub-related triples to the graph
                - Convert the extended graph back to the sub graph using the frame you just created with @explicit = false
                - Replace the sub-specific path with .set
            - Repeat this process iteratively until no more subs can be removed from the remaining pool of triples
        """
        print("not implemented")

    def describe(self, id: str) -> Self | None:
        """
        Collects all triples with id as sub and returns them as dict

        Concept:
        Easiest way to implement this is to use _find_path_to_id-function to extract subdict.
        Then find any other remaining paths that have id as sub via .find.
        These remaining paths should be converted to graphs and be addd to the subdict via .add.
        In 99 % of the cases it will just mean tha the subdict corresponding to _find_path_to_id is extracted.
        """
        print("not implemented")

    def find(self, path_pattern=None, value_pattern=None):
        """
        Filter a DataFrame with columns ['path', 'value'] using regex.
        Always uses AND logic and is case-insensitive.

        Args:
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

    def frame(self, jsonld_frame: dict) -> Self:
        """
        Applies a json-ld frame.
        """

        # if "@context" not in jsonld_frame.keys():
        #    jsonld_frame["@context"] = dict(self.prefix_store.prefixes)

        jsonld_frame_expanded = self.prefix_store.expand(jsonld_frame)

        # Serialize RDF graph as JSON-LD
        json_expanded = self.prefix_store.expand(self.dict)
        # Apply the frame
        json_framed = jsonld.frame(json_expanded, jsonld_frame_expanded)
        # Compact the dictionary
        json_compacted = self.prefix_store.compact(json_framed)

        return type(self)(json_compacted, self.prefix_store)

    def get(self, path):
        """
        Get allows to extract a value via a path. Returns a new GraphDict if appropriate.
        """
        output_obj = glom(self.dict, path)
        # Wrapping list in dict
        if isinstance(output_obj, list):
            output_obj = {"@graph": output_obj}
        # dicts are returned as GraphDicts, otherwise return object itself
        if isinstance(output_obj, dict):
            return type(self)(output_obj, self.prefix_store)
        else:
            return output_obj

    def rename(self, old_name, new_name) -> Self | None:
        """
        Renames any term from old_name to new name.

        Replaces both in pred but also in sub or obj (by checking @id and @type).
        """
        print("not implemented")

    def serialize(
        self, output_format: str, prefix_action: str | None = "compact"
    ) -> str:
        """
        Serialize the data to a string in the requested format.

        formats:
            - json
            - yaml
        prefixes:
            - expand
            - compact
            - drop
        """

        # Inventory of the function arguments
        supported_formats = ["json", "yaml", "yml"]
        supported_prefix_actions = ["expand", "compact", "drop"]

        # Checking for proper function arguments
        if output_format not in supported_formats:
            raise ValueError(f"format not in {supported_formats}")
        elif (
            prefix_action is not None and prefix_action not in supported_prefix_actions
        ):
            raise ValueError(f"prefixes not in {supported_prefix_actions}")

        dict_data = self.dict
        dict_data = self.prefix_store.apply_prefixes(dict_data, prefix_action)
        dict_data = self.collapse_values(dict_data)

        match output_format:
            case "json":
                return json.dumps(dict_data)
            case "yaml" | "yml":
                return yaml.dump(dict_data, default_flow_style=False, sort_keys=False)

    def set(self, path: str, new_value, sep=".") -> Self:
        """
        Strict insert of a nested dict/list structure using a path.

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

        data = deepcopy(self.dict)

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

        return type(self)(data, self.prefix_store)

    ###########################
    # Private
    ###########################

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

        walk(self.dict, [])
        return pd.DataFrame(rows)

    def _provide_prefixes(self, only_keys: bool = True) -> None:
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

        self.dict = remap(self.dict, visit=visit)

    @staticmethod
    def collapse_values(obj):
        """
        Recursively traverse nested dicts/lists and replace any JSON-LD value object
        (i.e. a dict containing '@value') with its native value.
        """

        def visit(path, key, value):
            if isinstance(value, dict) and "@value" in value:
                return key, value["@value"]

            return True  # continue traversal

        return remap(obj, visit=visit)

    ###########################
    # DUNDER METHODS
    ###########################

    # String shown upon print
    def __repr__(self):
        return f"GraphDict(\n{str(self.dict)})"

    # Indexing prefix_store returns indexed self.dict
    def __getitem__(self, key):
        return self.dict[key]

    def __setitem__(self, key, newvalue):
        self.dict[key] = newvalue

    def __delitem__(self, key):
        del self.dict[key]


# Ensures that multilines are printed in pretty multiline format
def _pretty_multiline(dumper, data):
    if "\n" in data:  # detect multiline
        data = data.replace("\r\n", "\n")
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


yaml.add_representer(str, _pretty_multiline)
