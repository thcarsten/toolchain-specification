from .prefix_store import PrefixStore
from .utils import load_yaml
from rdflib import Graph, URIRef, BNode
import pandas as pd
from copy import deepcopy
from typing import Self
from pyld import jsonld
import json


class GraphReader:
    """
    Class to read data from an RDF graph into Python.
    The graph data itself is immutable, transformations return a new instance of GraphReader.
    Properties:
            - graph: the raw graph as RDFlib Graph type
            - prefix_store: a PrefixStore with the prefixes loaded from the graph
            - df: Dataframe view on the graph
            - _basepath: Used for resolving relative filepaths
            - _blanknode_prefix: # The prefix being used to indicate materialized blanknodes
    Public methods:
            - add
            - check_exists
            - filter
            - infer
            - query
            - remove
            - rename
            - serialize
            - sparql
            - traverse
            - validate (TBA)
    Private methods:
            - _df_to_graph
            - _graph_to_df
            - _materialize_blank_nodes
            - _restore_blank_nodes

    """

    ###########################
    # Class variables
    ###########################

    _basepath = "file:///workspace/pipeline/"  # Has to match working directory path in docker file. TODO: THis has to be read out dynamically in the future
    _blanknode_prefix = {"bn_": "https://materialized_blanknode.com/"}
    _placeholder_prefix = {"na_": "https://no_prefix_available.com/"}

    ###########################
    # Constructors
    ###########################

    def __init__(self, graph: Graph):
        self.graph = graph  # Initiate an empty graph
        self._df: pd.DataFrame | None = None  # The cache for the graph to df conversion
        self.prefix_store = PrefixStore(graph)
        # The prefix being used to indicate blanknodes
        self.prefix_store.bind_to_namespace(self.graph)
        # self._materialize_blank_nodes()

    @classmethod
    def from_df(cls, df: pd.DataFrame, prefix_store: PrefixStore) -> Self:
        new_graph = cls._df_to_graph(df, prefix_store)
        return cls(new_graph)

    ###########################
    # Public
    ###########################

    @property
    def df(self) -> pd.DataFrame:
        # This implements caching so that the conversion does not need to take place every time
        if self._df is None:
            self._df = self._graph_to_df(self.graph)
        return self._df

    def add(self, graph: Graph) -> Self:
        """
        Return a new GraphReader with triples from `graph` added.
        """

        new_graph = self.graph + graph

        # ensure prefixes are applied to new graph
        self.prefix_store.bind_to_namespace(new_graph)
        PrefixStore(graph).bind_to_namespace(new_graph)

        return type(self)(new_graph)

    def check_exists(self, node_id: str) -> bool:
        """
        Checks whether a node_id occurs in a graph, either as subject, predicate or object
        Returns boolean
        """

        # Making sure node_id is compacted
        node_id = self.prefix_store._compact_string(node_id)

        # Query checking whether node_id occurs as subject
        is_subject_query = f"""
            ASK WHERE {{
                {node_id} ?p ?o .
            }}
            """

        is_predicate_query = f"""
            ASK WHERE {{
                ?s {node_id} ?o .
            }}
            """

        # Query checking whether node_id occurs as object
        is_object_query = f"""
            ASK WHERE {{
                ?s ?p {node_id} .
            }}
            """

        bool_node_is_in_graph = (
            self.sparql(is_subject_query)
            or self.sparql(is_predicate_query)
            or self.sparql(is_object_query)
        )
        return bool_node_is_in_graph

    def filter(
        self,
        sub=None,
        pred=None,
        obj=None,
        sub_type=None,
        obj_type=None,
        action="keep",
        regex=False,
    ) -> Self:
        """
        Selects triples based on a matching pattern.

        Values may be a singleton or a list. In case of a list, matching one
        element is sufficient.

        Parameters
        ----------
        regex : bool, default False
            If True, string values are interpreted as regular expressions.
            If False, exact matching is used.
        """

        dict_filters = {
            "sub": sub,
            "pred": pred,
            "obj": obj,
            "sub_type": sub_type,
            "obj_type": obj_type,
        }

        dict_filters = {k: v for k, v in dict_filters.items() if v is not None}

        df_subset = self.df
        mask = pd.Series(True, index=df_subset.index)

        for col, value in dict_filters.items():

            if regex:

                if isinstance(value, (list, tuple, set)):
                    pattern = "|".join(f"(?:{v})" for v in value)
                    col_mask = (
                        df_subset[col]
                        .astype(str)
                        .str.contains(pattern, regex=True, na=False)
                    )

                else:
                    col_mask = (
                        df_subset[col]
                        .astype(str)
                        .str.contains(value, regex=True, na=False)
                    )

            else:

                if isinstance(value, (list, tuple, set)):
                    col_mask = df_subset[col].isin(value)
                else:
                    col_mask = df_subset[col] == value

            mask &= col_mask

        if action == "keep":
            df_subset = df_subset.loc[mask]
        elif action == "drop":
            df_subset = df_subset.loc[~mask]
        else:
            raise ValueError("action must be 'keep' or 'drop'")

        df_subset = df_subset.reset_index(drop=True)

        return type(self)(self._df_to_graph(df_subset, self.prefix_store))

    def infer(self, filepath, max_repetitions: int = 10) -> Self:
        """
        Infers new triples based on inference rules contained in file.
        Stops when no new triples are added (fixed point) or max_repetitions is reached.
        """

        # Initializing

        dict_inference = load_yaml(filepath)

        prefix_store = PrefixStore(dict_inference["context"])
        inference_rules = dict_inference["rules"]

        working_graph = Graph()
        working_graph += self.graph

        self.prefix_store.bind_to_namespace(working_graph)
        prefix_store.bind_to_namespace(working_graph)

        prev_size = len(working_graph)

        # Startinf inference loop
        for _ in range(max_repetitions):

            for rule in inference_rules:

                if "construct" not in rule or "where" not in rule:
                    raise ValueError("Invalid rule format")

                query = prefix_store.include_in_query(f"""
                CONSTRUCT {{
                    {rule["construct"]}
                }}
                WHERE {{
                    {rule["where"]}
                }}
                """)

                results = working_graph.query(query)

                inferred = Graph()
                for t in results:
                    inferred.add(t)

                prefix_store.bind_to_namespace(inferred)

                working_graph += inferred

            new_size = len(working_graph)

            if new_size == prev_size:
                break

            prev_size = new_size
        else:
            raise RuntimeError("Inference did not converge")

        return type(self)(working_graph)

    def query(
        self,
        where: str,
        select: str | None = None,
        construct: str | None = None,
        ask: bool | None = None,
    ):
        """
        Returns the results of a SPARQL query

        Args:
            - where (str) : where-section of a query, specifies the pattern that triples need to match.
            - select (str)
            - construct (str)
            - ask (bool)

        TODO: Add optional argument 'OPTIONAL'
        """

        # Checking that only one header_statement is provided
        header_statements = [select, construct, ask]
        header_statements = [
            statement for statement in header_statements if statement is not None
        ]
        if len(header_statements) != 1:
            raise Exception(
                "One of the following needs to be provided: select, construct or ask"
            )

        if select:
            query = f"SELECT {select} WHERE {{{where}}}"
        elif construct:
            query = f"CONSTRUCT {{{construct}}} WHERE {{{where}}}"
        elif ask:
            query = f"ASK {{{where}}}"

        return self.sparql(query=query)

    def remove(self, graph: Graph) -> Self:
        """
        Return a new GraphReader with triples from 'graph' removed.
        """
        new_graph = self.graph - graph
        # ensure prefixes are applied to new graph
        self.prefix_store.bind_to_namespace(new_graph)
        PrefixStore(graph).bind_to_namespace(new_graph)
        return type(self)(new_graph)

    def rename(self, old: str, new: str) -> Self:
        """
        Replaces a node with a name.
        - "old_name", "new_name"
        """

        # I do this in the dataframe space because it is easier and avoids some issues
        df_graph = self.df

        # Renaming in sub and obj
        df_graph.loc[df_graph["sub"] == old, "sub"] = new
        df_graph.loc[df_graph["obj"] == old, "obj"] = new

        # Updating types (assuming that renaming a term makes it a URI)
        df_graph.loc[df_graph["sub"] == new, "sub_type"] = URIRef
        df_graph.loc[df_graph["obj"] == new, "obj_type"] = URIRef

        # Returning the updated graph
        return type(self)(self._df_to_graph(df_graph, self.prefix_store))

    def serialize(self, output_format: str) -> str:
        """
        Serialize the graph to a string in the requested format. Simple wrapper around graph.serialize
        """
        return self.graph.serialize(
            format=output_format,
            base=self._basepath,
        )

    # Executes a SPARQL query against the graph
    def sparql(
        self,
        query: str,
    ) -> pd.DataFrame | Self | bool:
        """
        Executes a query on the graph and returns results in a native data type.

        Args:
            - query: The query in string format.

        Returns:
            - dataframe for select
            - Graph for construct
            - bool for ask
        """

        # Execute query
        query = self.prefix_store.include_in_query(query)
        results = self.graph.query(query)

        # Preparing the output df
        if results.type == "SELECT":
            # Construct the output dataframe
            rows = list(results)
            df_output = pd.DataFrame(rows, columns=[str(var) for var in results.vars])
            # Cleanup the output dataframe
            df_output = df_output.map(self.prefix_store.node_to_python)
            df_output = self.prefix_store.compact(df_output)
            return df_output
        elif results.type == "CONSTRUCT":
            if results.graph:  # if results.graph is not falsly (empty)
                self.prefix_store.bind_to_namespace(results.graph)
                return type(self)(results.graph)
            else:  # if results.graph is empty return empty dataframe
                raise Exception("Constructed graph returned empty.")
        elif results.type == "ASK":
            return bool(results)
        else:
            raise TypeError(
                "SELECT or CONSTRUCT query expected, {results.type} received."
            )

    def traverse(
        self,
        node_id: str,
        direction: str = "along",  # ["along", "against", "both"]
        exclude: list[str] | None | str = None,
        along: list[str] | None | str = None,
        against: list[str] | None | str = None,
        prune: list[str] | None | str = None,
    ) -> Self:
        """
        Recursively extract the subgraph starting at root_node.

        Parameters
        ----------
        - node_id : The starting node of the subgraph.
        - direction: str of "along", "against" or "both". Default is both. Set whether you want to follow edges only in their regular direction (along), inverse direction (against) or both.
        - exclude: List of predicates to ignore.
        - along: Allows you to provide a list of predicates that should be exclusivelu followed in direction 'along'. Overwrites direction for that predicate
        - against: Allows you to provide a list of predicates that should be exclusivelu followed in direction 'against'. Overwrites direction for that predicate
        - prune: Prevents to continue search on the neighbors. Allows for example to fetch dependencies of a Processor via osw:hasDependency, without fetching further info on that dependency
        """

        # Making sure the function argument always comes out as list of expanded urls
        def create_predicate_list(function_argument: str | list[str] | None):

            # Save typing
            if not function_argument:
                function_argument = []
            elif isinstance(function_argument, str):
                function_argument = [function_argument]

            # Turning function arguments to proper uri's
            function_argument = [
                URIRef(self.prefix_store.expand(argument))
                for argument in function_argument
            ]
            return function_argument

        # Provide full URIs for predicates in method arguments
        exclude = create_predicate_list(exclude)
        along = create_predicate_list(along)
        against = create_predicate_list(against)
        prune = create_predicate_list(prune)

        visited_nodes = set()  # prevent cycles
        subgraph = Graph()  # store triples of the subgraph

        # Converting the node_id string to a RDFlib node reference
        root_node = self.prefix_store.python_to_node(node_id, URIRef)

        # Raise exception if root_node is not found
        if not self.check_exists(root_node):
            raise NameError(f"{root_node} not found in graph.")

        def _dfs(node):
            """
            Add each triple that has node as subject to the subgraph.
            To traverse the graph along the edges in the regular direction,
            Follow each triple that has the added objects as subjects and do the same.
            """

            if node in visited_nodes:
                return
            visited_nodes.add(node)

            # Stores the ids to be followed recursively
            neighbors = set()

            # following ALONG the edge's direction: Add all triples with node as subject
            for p, o in self.graph.predicate_objects(subject=node):
                if (direction in ["along", "both"]) or (p in along):
                    # Skip blacklisted predicates or predicates that overwrite direction
                    if (p in exclude) or (p in against and p not in along):
                        continue
                    subgraph.add((node, p, o))
                    # Recurse for objects that are URIRef or BNode
                    if isinstance(o, (URIRef, BNode)) and (p not in prune):
                        neighbors.add(o)

            # following AGAINST the edge's direction: Add all tripls with node as object
            for s, p in self.graph.subject_predicates(object=node):
                if (direction in ["against", "both"]) or (p in against):
                    # Skip blacklisted predicates
                    if (p in exclude) or (p in along and p not in against):
                        continue
                    subgraph.add((s, p, node))
                    # Recurse for objects that are URIRef or BNode
                    if isinstance(s, (URIRef, BNode)) and (p not in prune):
                        neighbors.add(s)

            for neighbor in neighbors:
                _dfs(neighbor)

        _dfs(root_node)

        self.prefix_store.bind_to_namespace(subgraph)
        return type(self)(subgraph)

    def validate(self):
        """
        SHACL validation
        """
        print("not implemented")

    ###########################
    # Private
    ###########################

    def _materialize_blank_nodes(self) -> None:
        """
        Turns all blank nodes into proper URI's.
        This allows me to use any functions on blank nodes that were designed with URIs in mind.
        """
        # Replacing blank nodes with URIs
        new_graph = Graph()
        blanknode_prefix = list(self._blanknode_prefix.keys())[0]
        blanknode_url = self._blanknode_prefix.get(blanknode_prefix)

        for sub, pred, obj in self.graph:
            if isinstance(sub, BNode):
                sub = URIRef(blanknode_url + sub.n3().split(":")[1])
            if isinstance(obj, BNode):
                obj = URIRef(blanknode_url + obj.n3().split(":")[1])
            new_graph.add((sub, pred, obj))

        # Prefix for materialized blank nodes has to be added to the prefix_store as well
        self.prefix_store.load(self._blanknode_prefix, replace=False)

        # Replacing the existing graph
        self.prefix_store.bind_to_namespace(new_graph)
        self.graph = new_graph

    def _restore_blank_nodes(self) -> None:
        newgraph = Graph()

        blanknode_prefix = list(self._blanknode_prefix.keys())[0]
        blanknode_url = self._blanknode_prefix.get(blanknode_prefix)

        for sub, pred, obj in self.graph:
            if isinstance(sub, URIRef):
                sub_string = self.prefix_store.node_to_python(sub)
                if sub_string.startswith(blanknode_prefix + ":"):
                    sub_string = sub_string.removeprefix(blanknode_prefix + ":")
                    sub = BNode(sub_string)
            if isinstance(obj, URIRef):
                obj_string = self.prefix_store.node_to_python(obj)
                if obj_string.startswith(blanknode_prefix + ":"):
                    obj_string = obj_string.removeprefix(blanknode_prefix + ":")
                    obj = BNode(obj_string)
            newgraph.add((sub, pred, obj))

        # Merging namespaces of graphs
        self.prefix_store.bind_to_namespace(newgraph)
        self.graph = newgraph

    @staticmethod
    def _graph_to_df(graph: Graph) -> pd.DataFrame:
        """
        Helper-function which turns a graph into a df
        """

        df = pd.DataFrame.from_records(
            [{"sub": s, "pred": p, "obj": o} for s, p, o in graph]
        )
        if len(df) == 0:
            raise Exception("Cannot turn empty graph into df.")

        df["sub_type"] = df.apply(lambda row: type(row["sub"]), axis=1)
        df["obj_type"] = df.apply(lambda row: type(row["obj"]), axis=1)
        prefix_store = PrefixStore(graph)
        df = df.map(prefix_store.node_to_python)
        return df

    @staticmethod
    def _df_to_graph(df: pd.DataFrame, prefix_store: PrefixStore) -> Graph:
        """
        Convert a triple DataFrame into an rdflib Graph.
        """

        # Check whether required columns are present
        required_columns = {"sub", "pred", "obj", "sub_type", "obj_type"}
        missing_columns = required_columns - set(df.columns)
        if missing_columns:
            raise ValueError(
                f"DataFrame is missing required columns: {sorted(missing_columns)}"
            )

        output_graph = Graph()

        for index, row in df.iterrows():
            sub = prefix_store.python_to_node(row["sub"], row["sub_type"])
            pred = prefix_store.python_to_node(row["pred"], URIRef)
            obj = prefix_store.python_to_node(row["obj"], row["obj_type"])
            output_graph.add((sub, pred, obj))

        prefix_store.bind_to_namespace(output_graph)

        return output_graph
