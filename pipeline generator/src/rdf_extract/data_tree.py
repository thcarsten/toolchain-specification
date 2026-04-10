from .prefix_store import PrefixStore
from rdflib import Graph
import yaml
import json
from boltons.iterutils import remap
from boltons.dictutils import subdict
import copy  # For deep copies
from glom import glom
from validators import url as is_url


# Ensures that multilines are printed in pretty multiline format
def _pretty_multiline(dumper, data):
    if "\n" in data:  # detect multiline
        data = data.replace("\r\n", "\n")
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


yaml.add_representer(str, _pretty_multiline)


class DataTree:
    # TODO: Will break if expanded urls of tc:literal and tc:embedded are used
    # TODO: Serialization to graph / turtle does not work without keys having prefixes, due to RDFlib graph not being able to parse
    # triples without prefixes. Good to have dummy prefixes like na_ (not available)
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
            - subset
            - rename_key
            - collapse_ids
            - drop_prefixes
            - drop_empty
            - provide_prefixes
            - expand
            - compact
        Serialization
            - to_dict
            - to_yaml
            - to_json
            - to_turtlef
            - to_graph
        Other
            - validate
            - copy
            - get
            - add_to_graph
    """

    ###########################
    # I/O
    ###########################

    def __init__(self, dictionary: dict) -> None:
        """
        A Data Tree is initialized by passing a dictionary. If the dictionary is a json-ld, it will likely contain the keys
        @id, @type and @context. In this case, these are used as properties of the instance and removed from the dict.
        These properties allow serialization to rdf and graph, however are not mandatory. That means also regular dictionaries (not jsonlds)
        can be turned into data trees.
        """
        self.dict_data = {}
        self.prefix_store: PrefixStore | None = None
        self._basepath = "file:///workspace/pipeline/"  # Has to match working directory path in docker file. TODO: THis has to be read out dynamically in the future
        self._placeholder_prefix = {"na_": "https://no_prefix_available.com/"}

        # Ensures deep copy
        input_dict = (
            dictionary.copy()
        )  # deep copy, prevents tranformation on the input dictionary

        # Move these properties
        if "@context" in input_dict:
            self.prefix_store = PrefixStore(input_dict.get("@context"))
            del input_dict["@context"]

        self.dict_data.update(input_dict)

    ###########################
    # RECURSIVE TRANSFORMATIONS
    ###########################

    def subset(
        self, keep: str | list[str] | None = None, drop: str | list[str] | None = None
    ) -> None:

        # Save typing
        if isinstance(keep, str):
            keep = [keep]
        if isinstance(drop, str):
            drop = [drop]

        def visit(path, key, value):
            if isinstance(value, dict):
                return key, subdict(value, keep=keep, drop=drop)
            else:
                return True

        self.dict_data = remap(self.dict_data, visit=visit)

    def drop_prefixes(self) -> None:
        """
        Drops all known prefixes from keys and values
        """
        if not self.prefix_store:
            raise LookupError("Prefix store not found, cannot drop prefixes.")

        def visit(path, key, value):
            if isinstance(value, str):
                new_value = self.prefix_store.remove_from_string(value)
            else:
                new_value = value
            if isinstance(key, str):
                new_key = self.prefix_store.remove_from_string(key)
            else:
                new_key = key
            return new_key, new_value

        self.dict_data = remap(self.dict_data, visit=visit)

    def provide_prefixes(self, only_keys: bool = True) -> None:
        """
        The opposite of drop prefixes:
        Any key without a known prefix gets a placeholder prefix prepended.
        Skips any keys starting with '@'. By default values remain unchanged.
        """

        missing_prefix = self._placeholder_prefix

        placeholder_prefix = list(missing_prefix.keys())[0]
        self.prefix_store.load(missing_prefix, replace=False)
        self.compact()

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

    def expand(self) -> None:
        # Using boltons remap to expand prefixes
        if not self.prefix_store:
            raise LookupError("Cannot expand prefixes, PrefixStore not found.")

        def visit(path, key, value):
            if isinstance(value, str):
                new_value = self.prefix_store.expand_string(value)
            else:
                new_value = value
            if isinstance(key, str):
                new_key = self.prefix_store.expand_string(key)
            else:
                new_key = key
            return new_key, new_value

        self.dict_data = remap(self.dict_data, visit=visit)

    def compact(self) -> None:
        # Using boltons remap to compact prefixes
        if not self.prefix_store:
            raise LookupError("Cannot compact prefixes, PrefixStore not found.")

        def visit(path, key, value):
            if isinstance(value, str):
                new_value = self.prefix_store.compact_string(value)
            else:
                new_value = value
            if isinstance(key, str):
                new_key = self.prefix_store.compact_string(key)
            else:
                new_key = key
            return new_key, new_value

        self.dict_data = remap(self.dict_data, visit=visit)

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

    def collapse_ids(self) -> None:
        """
        Recursively traverse nested dicts/lists and replace any dict of the exact form
        {'@id': '...'} with just the string value.
        """

        def visit(path, key, value):
            if isinstance(value, dict) and set(value.keys()) == {"@id"}:
                # Replace dicts that contain exactly one key: '@id'
                return key, value["@id"]
            return True  # continue traversal

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

    ###########################
    # SERIALIZATION
    ###########################

    def to_dict(self) -> dict:
        return self.dict_data.copy()

    def to_yaml(self) -> str:
        return yaml.dump(self.dict_data, default_flow_style=False, sort_keys=False)

    def to_json(self) -> str:
        return json.dumps(self.dict_data)

    def to_graph(self) -> Graph:
        if self.dict_data.get("@id") is None or not self.prefix_store:
            raise LookupError("Cannot turn to jsonld, @id or PrefixStore missing.")

        config_graph = Graph()
        self.add_to_graph(config_graph)
        return config_graph

    def to_turtle(self) -> str:
        graph = self.to_graph()
        return graph.serialize(format="turtle", base=self._basepath)

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

    def get(self, path) -> "DataTree":
        """
        Get is a straightforward glom-wrapper
        """
        glom_dict = glom(self.dict_data, path)
        glom_dict["@context"] = dict(self.prefix_store.prefixes)
        glom_tree = DataTree(glom_dict)
        return glom_tree

    def add_to_graph(self, external_graph: Graph) -> None:
        """
        Standardized way to add the contents of the data tree to an external graph.
        """

        if self.dict_data.get("@id") is None or not self.prefix_store:
            raise LookupError("Cannot turn to jsonld, @id or PrefixStore missing.")

        self.expand()
        jsonld_dict = self.to_dict()
        jsonld_dict["@context"] = dict(self.prefix_store.prefixes)
        jsonld_string = json.dumps(jsonld_dict)
        external_graph.parse(data=jsonld_string, format="json-ld")
        self.prefix_store.bind_to_namespace(external_graph)

    ###########################
    # DUNDER METHODS
    ###########################

    # String shown upon print
    def __repr__(self):
        return f"DataTree(\n{str(self.to_yaml())})"

    # Indexing prefix_store returns indexed self.dict_data
    def __getitem__(self, key):
        return self.dict_data[key]

    def __setitem__(self, key, newvalue):
        self.dict_data[key] = newvalue

    def __delitem__(self, key):
        del self.dict_data[key]
