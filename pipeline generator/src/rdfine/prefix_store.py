from rdflib import Graph, URIRef, BNode, Literal, Node
from bidict import OrderedBidict
import pandas as pd


class PrefixStore:
    """
    Class to store prefixes and to apply them to various other types

    Properties:
            - prefixes: an OrderedBidict with prefix:url as key:value-pairs (printed in alphabetical order)
            - df_ordered_prefixes: a dataframe that ordered the prefixes from longest to shortest url:
              Prefixes with longer urls are more specific and henc take precedence.

    Methods:
        Interface function: load
            - _load_from_ttl
            - _load_from_namespace
            - _update
        Apply functions (support primitive, dataframe, list, dict)
            - compact
            - expand
            - drop
        Hidden functions
            - _compact_string
            - _expand_string
            - _drop_string
            - apply_prefixes(data, action)
            - _apply_action_to_object
        Other functions
            - fetch_prefix
            - replace_prefix_in_store
            - include_in_query
            - bind_to_namespace
            - _order_prefixes
        Type conversions
            - node_to_python
            - python_to_node
    """

    def __init__(self, source):
        self.prefixes = {}
        self.df_ordered_prefixes: pd.DataFrame
        self.load(source)

    ###########################
    # LOAD PREFIXES
    ###########################

    def load(self, source: str | Graph | dict, replace=True) -> None:
        """
        Loads prefixes from a source. Detects the type of source and calls the corresponding function.
        If replace is set to false, performs an upsert instead of wiping and replacing stored prefixes.
        source: str = loads via filepath
        source: Graph = loads via namespace
        source: dict = Simple copy of the dict into the class
        """
        if isinstance(source, str):
            prefixes = self._load_from_ttl(source)
        elif isinstance(source, Graph):
            prefixes = self._load_from_namespace(source)
        elif isinstance(source, dict):
            prefixes = source
        elif isinstance(source, OrderedBidict):
            prefixes = dict(source)
        else:
            raise TypeError(f"Source of type {type(source)} not supported")

        self._update(prefixes, replace=replace)

    def _load_from_ttl(self, filepath: str) -> dict:
        """
        Function to extract the prefixes from a ttl file
        """
        dict_prefixes = {}
        with open(filepath, "r", encoding="utf-8") as file:
            for line in file:
                if line.startswith("@prefix"):
                    prefix = (
                        line.removeprefix("@prefix")
                        .split("<")[0]
                        .strip()
                        .removesuffix(":")
                    )
                    url = line.split("<")[1].split(">")[0]
                    dict_prefixes[prefix] = url

        return dict_prefixes

    def _load_from_namespace(self, graph: Graph) -> dict:
        """
        Takes the native namespaces from the rdflib library and
        turns them into a dict
        """
        prefixes = {}
        for namespace in graph.namespaces():
            prefix = list(namespace)[0]
            url = str(list(namespace)[1])
            prefixes[prefix] = self._expand_string(
                self.node_to_python(url)
            )  # Url is expanded string version

        return prefixes

    def _update(self, dict_prefixes, replace=True) -> None:
        """
        Updates the prefixes stored as attributes through an upsert.
        if replace is true, wipes and replaces all stored prefixes instead
        """
        # Update attributes with new prefixes
        if replace:
            self.prefixes = OrderedBidict(dict_prefixes)
        else:
            self.prefixes.update(OrderedBidict(dict_prefixes))

        # Storing order in which expand should be applied
        self._order_prefixes()

    ###########################
    # APPLY PREFIXES
    ###########################

    def compact(self, data):
        """
        Interface function for compacting prefixes.
        """
        return self.apply_prefixes(data, action="compact")

    def expand(self, data):
        """
        Interface function for expanding prefixes.
        """
        return self.apply_prefixes(data, action="expand")

    def drop(self, data):
        """
        Interface function for dropping prefixes.
        """
        return self.apply_prefixes(data, action="drop")

    def apply_prefixes(self, data, action: str):
        """
        Routing function for compact, expand and drop actions
        """

        # Function argument check
        supported_actions = ["compact", "expand", "drop"]
        if action not in supported_actions:
            raise ValueError(f"action not in {supported_actions}")

        if isinstance(data, str):
            match action:
                case "compact":
                    return self._compact_string(data)
                case "expand":
                    return self._expand_string(data)
                case "drop":
                    return self._drop_string(data)

        if isinstance(data, pd.DataFrame):
            match action:
                case "compact":
                    return data.map(
                        lambda cell_url: (
                            self._compact_string(cell_url)
                            if isinstance(cell_url, str)
                            else cell_url
                        )
                    )
                case "expand":
                    return data.map(
                        lambda cell_url: (
                            self._expand_string(cell_url)
                            if isinstance(cell_url, str)
                            else cell_url
                        )
                    )
                case "drop":
                    return data.map(
                        lambda cell_url: (
                            self._drop_string(cell_url)
                            if isinstance(cell_url, str)
                            else cell_url
                        )
                    )

        if isinstance(data, list) or isinstance(data, dict):
            return self._apply_action_to_object(data, action)

    def _apply_action_to_object(self, obj: list | dict, action: str) -> list | dict:
        """
        Recursively apply prefixes in objects, i.e. nested lists and dicts.
        """
        if isinstance(obj, dict):
            new_dict = {}
            for key, value in obj.items():
                # Replace in key if it's a string
                match action:
                    case "compact":
                        new_key = (
                            self._compact_string(key) if isinstance(key, str) else key
                        )
                    case "expand":
                        new_key = (
                            self._expand_string(key) if isinstance(key, str) else key
                        )
                    case "drop":
                        new_key = (
                            self._drop_string(key) if isinstance(key, str) else key
                        )
                # Recursively process value
                new_dict[new_key] = self._apply_action_to_object(value, action)
            return new_dict

        elif isinstance(obj, list):
            return [self._apply_action_to_object(item, action) for item in obj]

        elif isinstance(obj, str):
            match action:
                case "compact":
                    return self._compact_string(obj)
                case "expand":
                    return self._expand_string(obj)
                case "drop":
                    return self._drop_string(obj)
        else:
            return obj

    def _compact_string(self, url: str) -> str:
        """
        Compacts prefixes in a string to a compacted url
        """
        if isinstance(url, str):
            for index, row in self.df_ordered_prefixes.iterrows():
                if url.startswith(row["url"]):
                    url = row["prefix"] + ":" + url.removeprefix(row["url"])
                    break
        return url

    def _expand_string(self, url: str) -> str:
        """
        Expands a compacted url based on the prefixes provided to a full url of type string
        """
        expanded_url = url
        if ":" in url:
            prefix, local = url.split(":", 1)
            if prefix in self.prefixes:
                expanded_url = self.prefixes[prefix] + local

        return expanded_url

    def _drop_string(self, url: str) -> str:
        """
        If the url contains a known prefix (or corresponding url), it is removed.
        """
        prefix_dict = self.fetch_prefix(url)
        if prefix_dict:
            prefix = list(prefix_dict.keys())[0]
            shortened_url = self._compact_string(url)
            shortened_url = shortened_url.removeprefix(prefix + ":")
            return shortened_url
        else:
            return url  # Return as-is if no known prefix is found

    ###########################
    # OTHER
    ###########################

    def fetch_prefix(self, node_id: str) -> dict:
        """
        Looks up if 'node_id' uses a known prefix.
        Returns the corresponding entry as dict.
        """

        compacted_node_id = self._compact_string(node_id)

        match_dict = {}
        # Testing for the case of a compacted node_id
        for prefix in self.prefixes:
            if compacted_node_id.startswith(prefix + ":"):
                match_dict[prefix] = self.prefixes.get(prefix)

        return match_dict

    def bind_to_namespace(self, graph: Graph) -> None:
        """
        Binds prefixes to namespaces of a RDFlib Graph object so that serialization can utilize these namespaces
        TODO: Resolve conflicts if different prefixes are bound to same url
        """
        for key in self.prefixes:
            graph.bind(key, self.prefixes[key], override=True)

    def include_in_query(self, query: str) -> str:
        """
        Prepands a SPARQL query with the prefixes, so that queries do not need to make use of full urls.s
        """
        # Preparing the prefixes
        prefix_textlines = []
        for prefix in self.prefixes:
            prefix_textline = f"PREFIX {prefix}: <{self.prefixes[prefix]}>"
            prefix_textlines.append(prefix_textline)
        prefix_textblock = "\n".join(prefix_textlines)

        # Prepending the prefixes to the query
        query = "\n".join([prefix_textblock, query])
        return query

    def _order_prefixes(self) -> None:
        """
        I always need to match the longest urls first, because they are more specific and hence take precendence in case of ties.
        So here I order by url length and add as attribute of the class.
        It is safer not to store the result of this function as attribute of the class, because the attribute will be outdated once
        the prefixes dictionary is updated.
        """

        prefixes = self.prefixes

        # Making sure that the expand is applied starting with the longest urls
        df_ordered_prefixes = pd.DataFrame.from_records(
            [{"prefix": prefix, "url": prefixes[prefix]} for prefix in prefixes]
        )
        df_ordered_prefixes["length"] = [len(url) for url in df_ordered_prefixes["url"]]
        df_ordered_prefixes = df_ordered_prefixes.sort_values("length", ascending=False)
        df_ordered_prefixes = df_ordered_prefixes.reset_index(drop=True)

        self.df_ordered_prefixes = df_ordered_prefixes

        # Making sure the print order of the dict is alphabetical
        alphabetically_sorted_dict = {}
        for prefix in sorted(prefixes.keys()):
            alphabetically_sorted_dict[prefix] = prefixes[prefix]
        self.prefixes = OrderedBidict(alphabetically_sorted_dict)

    def replace_prefix_in_store(self, original: str, replacement: str) -> None:
        """
        Replaces a prefix stored in the prefixStore with another prefix.
        Will affect compact and expand.
        """
        keys = [key for key in self.prefixes.keys()]  # deep copy
        for key in keys:
            if key == original:
                value = self.prefixes.pop(original)
                self.prefixes[replacement] = value
        self._order_prefixes()

    ###########################
    # TYPE CONVERSIONS
    ###########################
    """
    Type conversions between RDFlib terms and python native types do not really fit in the responsibilities of the prefix store,
    however this often involves prefix-knowledge, hence why I put it here.
    """

    def node_to_python(self, cell: Node):
        """
        Converts a RDFlib Node to a native Python Type.
        Literals are converted to their corresponding Python type.
        URIrefs and Bnodes are converted to strings.
        Does some cleaning to remove trailing symbols.
        """

        # If node is a UriRef, return a cleaned string
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
            if isinstance(cell, URIRef):
                return self._compact_string(simplified_cell)
            else:
                return simplified_cell
        # Convert to native python primitives if cell is literal
        elif isinstance(cell, Literal):
            return cell.toPython()
        else:
            return cell

    def python_to_node(self, cell, node_class) -> Node | None:

        # Fetching type error
        if node_class == URIRef or node_class == BNode:
            if not isinstance(cell, str):
                raise TypeError(
                    f"Cannot convert {cell} ({type(cell)} to {node_class}, str expected.)"
                )

        # Converting to URIRef
        if node_class == URIRef:
            return URIRef(self._expand_string(cell))

        # Converting to BNode, with safety checks
        elif node_class == BNode:
            match_dict = self.fetch_prefix(cell)
            if match_dict:
                prefix = list(match_dict.keys())[0]
                raise TypeError(
                    f"{cell} has known prefix {prefix}. This indicates URIRef, but you tried converting to BNode."
                )
            elif cell.startswith("_:"):
                cell = cell.removeprefix("_:")
            return BNode(cell)

        # Converting to Literal
        elif node_class == Literal:
            return Literal(
                cell
            )  # Handles type conversion automatically via constructor

        # Throw error for unknown node_class
        else:
            raise TypeError(f"node_class {node_class} is not URIRef, BNode or Literal.")

    ###########################
    # DUNDER METHODS
    ###########################

    # String shown upon print
    def __repr__(self):
        return f"PrefixStore({str(dict(self.prefixes))})"

    # Indexing prefix_store returns indexed self.prefixes
    def __getitem__(self, key):
        return self.prefixes[key]

    def __setitem__(self, key, newvalue):
        self.load({key: newvalue}, replace=False)

    def __delitem__(self, key):
        del self.prefixes[key]
        self._order_prefixes()
