from rdflib import Graph, URIRef, BNode
from .utils import node_to_str
from bidict import OrderedBidict
import pandas as pd
from boltons.iterutils import remap


class PrefixStore:
    """
    TODO: Add dunder method set item
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
        Apply functions
            - compact_string
            - expand_string
            - remove_from_string
            - fetch_prefix
            - compact_dataframe
            - compact_object
        Interface function: drop
        Other:
            - replace prefix_in_store
            - include_in_query
            - bind_to_namespace
            - _order_prefixes
            - str_to_node: Save way to turn a string to a UriRef or BNode
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
            prefixes[prefix] = node_to_str(url)

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

    def compact_string(self, url: str) -> str:
        """
        Compacts prefixes in a string to a compacted url
        """
        if isinstance(url, str):
            for index, row in self.df_ordered_prefixes.iterrows():
                if url.startswith(row["url"]):
                    url = row["prefix"] + ":" + url.removeprefix(row["url"])
                    break
        return url

    def compact_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        # Applying the prefixes in the priority as specified above
        df = df.map(lambda cell_url: self.compact_string(cell_url))
        return df

    def compact_object(self, obj: list | dict) -> list | dict:
        """
        Recursively compact prefixes in objects, i.e. nested lists and dicts.
        """
        if isinstance(obj, dict):
            new_dict = {}
            for key, value in obj.items():
                # Replace in key if it's a string
                new_key = self.compact_string(key) if isinstance(key, str) else key
                # Recursively process value
                new_dict[new_key] = self.compact_object(value)
            return new_dict

        elif isinstance(obj, list):
            return [self.compact_object(item) for item in obj]

        elif isinstance(obj, str):
            return self.compact_string(obj)

        else:
            return obj

    def expand_string(self, url: str) -> str:
        """
        Expands a compacted url based on the prefixes provided to a full url of type string
        """
        expanded_url = url
        if ":" in url:
            prefix, local = url.split(":", 1)
            if prefix in self.prefixes:
                expanded_url = self.prefixes[prefix] + local

        return expanded_url

    def remove_from_string(self, url: str) -> str:
        """
        If the url contains a known prefix (or corresponding url), it is removed.
        """
        prefix_dict = self.fetch_prefix(url)
        if prefix_dict:
            prefix = list(prefix_dict.keys())[0]
            shortened_url = self.compact_string(url)
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

        compacted_node_id = self.compact_string(node_id)

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

    def str_to_node(self, node_id: str) -> URIRef | BNode:
        # If node_id is a blind node
        if node_id.startswith("_:"):
            return BNode(node_id.removeprefix("_:"))
        else:
            return URIRef(self.expand_string(node_id))

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
