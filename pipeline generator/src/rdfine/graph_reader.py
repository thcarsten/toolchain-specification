from .prefix_store import PrefixStore
from .utils import load_yaml
from rdflib import Graph, URIRef, BNode, Literal
import pandas as pd
import pyshacl
from typing import Self


class GraphReader:
    """
    Class to read data from an RDF graph into Python.
    The graph data itself is immutable, transformations return a new instance of GraphReader.
    Properties:
            - graph: the raw graph as RDFlib Graph type
            - prefix_store: a PrefixStore with the prefixes loaded from the graph
            - df: Dataframe view on the graph
            - _basepath: Used for resolving relative filepaths
    Public methods:
            - add
            - ask
            - check_exists
            - construct
            - filter
            - infer
            - remove
            - rename
            - select
            - serialize
            - sparql
            - traverse
            - validate
    Private methods:
            - _df_to_graph
            - _filter_via_dataframe
            - _filter_via_triples
            - _graph_to_df

    """

    ###########################
    # Class variables
    ###########################

    _basepath = "file:///workspace/pipeline/"  # Has to match working directory path in docker file. TODO: THis has to be read out dynamically in the future

    ###########################
    # Constructors
    ###########################

    def __init__(
        self,
        data: Graph | pd.DataFrame,
        prefix_store: PrefixStore | None = None,
    ) -> None:
        # Normalize ``data`` to an rdflib.Graph and pick a default
        # prefix_store source.
        if isinstance(data, Graph):
            graph = data
            if prefix_store is None:
                prefix_store = PrefixStore(graph)
        elif isinstance(data, pd.DataFrame):
            if prefix_store is None:
                raise ValueError(
                    "To construct a GraphReader from a DataFrame, a PrefixStore is required."
                )
            graph = self._df_to_graph(data, prefix_store)
        else:
            raise TypeError(
                "GraphReader expects an rdflib.Graph or pandas.DataFrame; got "
                f"{type(data).__name__}."
            )

        self.graph = graph
        self._df: pd.DataFrame | None = None  # The cache for the graph to df conversion
        self.prefix_store = prefix_store
        self.prefix_store.bind_to_namespace(self.graph)

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

        # ensure prefixes are applied to new graph — self.prefix_store
        # applies last so it always wins over graph's own bindings
        PrefixStore(graph).bind_to_namespace(new_graph)
        self.prefix_store.bind_to_namespace(new_graph)

        return type(self)(new_graph)

    def check_exists(self, node_id: str) -> bool:
        """
        Checks whether a node_id occurs in a graph, either as subject, predicate or object
        Returns boolean
        """

        # Making sure node_id is compacted
        node_id = self.prefix_store.compact_string(node_id)

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

        if action not in ("keep", "drop"):
            raise ValueError("action must be 'keep' or 'drop'")

        # The fast path operates directly on the rdflib graph via
        # ``graph.triples`` and avoids the round-trip through the DataFrame
        # view. It only handles plain sub/pred/obj patterns; regex matching and
        # sub_type/obj_type filters require the DataFrame path.
        needs_dataframe = regex or sub_type is not None or obj_type is not None
        if needs_dataframe:
            return self._filter_via_dataframe(
                sub=sub,
                pred=pred,
                obj=obj,
                sub_type=sub_type,
                obj_type=obj_type,
                action=action,
                regex=regex,
            )

        return self._filter_via_triples(sub=sub, pred=pred, obj=obj, action=action)

    def _filter_via_triples(self, sub, pred, obj, action) -> Self:
        """
        Fast filter path using ``rdflib.Graph.triples`` for exact pattern
        matching. String values are expanded with the prefix store; values in
        the object position are also matched as ``Literal`` so that primitive
        literal triples are not lost.
        """

        def _to_pattern_nodes(value, allow_literal: bool):
            if value is None:
                return [None]
            values = value if isinstance(value, (list, tuple, set)) else [value]
            nodes = []
            for v in values:
                if isinstance(v, (URIRef, BNode, Literal)):
                    nodes.append(v)
                    continue
                if isinstance(v, str):
                    # Blank-node identifiers in N3 form (``_:xxx``) must be
                    # wrapped as ``BNode`` — wrapping them as ``URIRef`` would
                    # never match the actual blank node in the graph. They
                    # also can't be Literals, so skip the literal alternative.
                    if v.startswith("_:"):
                        nodes.append(BNode(v[2:]))
                        continue
                    nodes.append(URIRef(self.prefix_store.expand_string(v)))
                    if allow_literal:
                        nodes.append(Literal(v))
                    continue
                # Non-string scalar (int, float, bool, ...) — only meaningful
                # in the object position; wrap as Literal.
                nodes.append(Literal(v))
            return nodes

        sub_patterns = _to_pattern_nodes(sub, allow_literal=False)
        pred_patterns = _to_pattern_nodes(pred, allow_literal=False)
        obj_patterns = _to_pattern_nodes(obj, allow_literal=True)

        matched = Graph()
        for s_pat in sub_patterns:
            for p_pat in pred_patterns:
                for o_pat in obj_patterns:
                    for triple in self.graph.triples((s_pat, p_pat, o_pat)):
                        matched.add(triple)

        result = matched if action == "keep" else self.graph - matched

        self.prefix_store.bind_to_namespace(result)
        return type(self)(result, prefix_store=self.prefix_store)

    def _filter_via_dataframe(
        self, sub, pred, obj, sub_type, obj_type, action, regex
    ) -> Self:
        """
        DataFrame-based filter path. Required when regex matching or
        sub_type/obj_type filtering is requested.
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
        else:  # "drop"
            df_subset = df_subset.loc[~mask]

        df_subset = df_subset.reset_index(drop=True)

        return type(self)(
            self._df_to_graph(df_subset, self.prefix_store),
            prefix_store=self.prefix_store,
        )

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
        converged = False

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
                converged = True
                break

            prev_size = new_size

        if not converged:
            raise RuntimeError("Inference did not converge")

        return type(self)(working_graph)

    def select(self, select_clause: str, where: str) -> pd.DataFrame:
        """
        Run a SPARQL ``SELECT`` query and return the result as a DataFrame.

        Args:
            select_clause: variables / projection of the SELECT (e.g.
                ``"?s ?p"`` or ``"DISTINCT ?s"``).
            where: body of the ``WHERE`` clause (without the surrounding
                braces).
        """
        return self.sparql(f"SELECT {select_clause} WHERE {{{where}}}")

    def construct(self, construct_clause: str, where: str) -> Self:
        """
        Run a SPARQL ``CONSTRUCT`` query and return the constructed triples
        wrapped in a new ``GraphReader``.

        Args:
            construct_clause: body of the ``CONSTRUCT`` clause (without the
                surrounding braces).
            where: body of the ``WHERE`` clause (without the surrounding
                braces).
        """
        return self.sparql(f"CONSTRUCT {{{construct_clause}}} WHERE {{{where}}}")

    def ask(self, where: str) -> bool:
        """
        Run a SPARQL ``ASK`` query and return the boolean result.

        Args:
            where: body of the ``WHERE`` clause (without the surrounding
                braces).
        """
        return self.sparql(f"ASK {{{where}}}")

    def remove(self, graph: Graph) -> Self:
        """
        Return a new GraphReader with triples from 'graph' removed.
        """
        new_graph = self.graph - graph
        # ensure prefixes are applied to new graph — self.prefix_store
        # applies last so it always wins over graph's own bindings
        PrefixStore(graph).bind_to_namespace(new_graph)
        self.prefix_store.bind_to_namespace(new_graph)
        return type(self)(new_graph)

    def rename(self, old: str, new: str) -> Self:
        """
        Return a new ``GraphReader`` in which every occurrence of ``old``
        (as subject or object) has been replaced by ``new``. ``new`` is
        treated as a URI IRI; ``old`` may be a blank-node label
        (``_:xxx``) or a compact URI.
        """
        old_node = (
            BNode(old[2:])
            if isinstance(old, str) and old.startswith("_:")
            else URIRef(self.prefix_store.expand_string(str(old)))
        )
        new_node = URIRef(self.prefix_store.expand_string(str(new)))

        new_graph = Graph()
        for s, p, o in self.graph:
            new_graph.add(
                (
                    new_node if s == old_node else s,
                    p,
                    new_node if o == old_node else o,
                )
            )
        self.prefix_store.bind_to_namespace(new_graph)
        return type(self)(new_graph, prefix_store=self.prefix_store)

    def serialize(self, output_format: str) -> str:
        """
        Serialize the graph to a string in the requested format. Simple wrapper around graph.serialize
        """
        return self.graph.serialize(
            format=output_format,
            base=self._basepath,
        )

    def validate(self, **pyshacl_kwargs) -> Self:
        """Validate the graph against SHACL shapes via pySHACL.

        The graph is expected to carry its own SHACL shapes — the
        data graph and the shapes graph are one and the same. This
        matches how the toolchain catalog is authored (shapes live
        next to the components they constrain) and keeps the method
        signature minimal.

        Returns the SHACL validation report as a new
        :class:`GraphReader`, preserving the graph-in / graph-out
        contract so callers can chain the usual reader operations on
        the result:

        .. code-block:: python

            report = reader.validate(advanced=True, inference='rdfs')
            if not report.ask("?r sh:conforms true"):
                raise RuntimeError(report.serialize("ttl"))

        Conformance is carried inside the returned graph as
        ``?report sh:conforms ?bool`` — there is no separate return
        type.

        Args:
            **pyshacl_kwargs: Forwarded verbatim to
                :func:`pyshacl.validate`. Common options include
                ``inference='rdfs'`` (materialize subclass triples
                before validation) and ``advanced=True`` (enable
                SHACL-AF SPARQL targets and constraints).

        Returns:
            :class:`GraphReader` wrapping pySHACL's
            ``results_graph``. The caller's prefix bindings are
            copied onto the report so focus-node / value-node IRIs
            compact naturally in ``serialize`` output.
        """
        # pySHACL returns (conforms, results_graph, results_text). We
        # only keep the results graph — conformance lives inside it
        # as ?r sh:conforms ?bool and the text form is redundant
        # with the graph, which callers can serialize themselves.
        _conforms, results_graph, _results_text = pyshacl.validate(
            self.graph,
            shacl_graph=self.graph,
            **pyshacl_kwargs,
        )

        # Copy the caller's prefix bindings onto the report so
        # violation IRIs (focus / value / source-shape nodes) compact
        # nicely when the caller serializes or filters the report.
        self.prefix_store.bind_to_namespace(results_graph)
        return type(self)(results_graph)

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
            self.prefix_store.bind_to_namespace(results.graph)
            return type(self)(results.graph)
        elif results.type == "ASK":
            return bool(results)
        else:
            raise TypeError(
                f"SELECT or CONSTRUCT query expected, {results.type} received."
            )

    def traverse(
        self,
        node_id: str,
        direction: str = "along",  # ["along", "against", "both"]
        exclude: list[str] | None | str = None,
        along: list[str] | None | str = None,
        against: list[str] | None | str = None,
        prune: list[str] | None | str = None,
        stop_at_named_nodes: bool = False,
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
        - stop_at_named_nodes: Concise Bounded Description mode
          (https://www.w3.org/submissions/CBD/). Every matched triple is
          still added regardless, but recursion never continues past a
          named node (URIRef) — only blank-node neighbors are followed
          further. Use this when a node's own description may reference
          other named resources (e.g. a config value pointing at a
          channel IRI) that should be named in the result but not have
          their own description pulled in too. Combines with
          along/against/exclude/prune rather than replacing them — a
          neighbor must clear both checks to be followed.
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

        def visit_node(node):
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
                    # Recurse for objects that are URIRef or BNode, unless
                    # stop_at_named_nodes restricts recursion to BNodes only.
                    if (
                        isinstance(o, (URIRef, BNode))
                        and (p not in prune)
                        and (not stop_at_named_nodes or isinstance(o, BNode))
                    ):
                        neighbors.add(o)

            # following AGAINST the edge's direction: Add all tripls with node as object
            for s, p in self.graph.subject_predicates(object=node):
                if (direction in ["against", "both"]) or (p in against):
                    # Skip blacklisted predicates
                    if (p in exclude) or (p in along and p not in against):
                        continue
                    subgraph.add((s, p, node))
                    # Recurse for objects that are URIRef or BNode, unless
                    # stop_at_named_nodes restricts recursion to BNodes only.
                    if (
                        isinstance(s, (URIRef, BNode))
                        and (p not in prune)
                        and (not stop_at_named_nodes or isinstance(s, BNode))
                    ):
                        neighbors.add(s)

            for neighbor in neighbors:
                visit_node(neighbor)

        visit_node(root_node)

        self.prefix_store.bind_to_namespace(subgraph)
        return type(self)(subgraph)

    ###########################
    # Private
    ###########################

    @staticmethod
    def _graph_to_df(graph: Graph) -> pd.DataFrame:
        """
        Helper-function which turns a graph into a df.

        An empty graph yields an empty DataFrame with the expected columns
        instead of raising.
        """

        columns = ["sub", "pred", "obj", "sub_type", "obj_type"]

        if len(graph) == 0:
            return pd.DataFrame(columns=columns)

        df = pd.DataFrame.from_records(
            [{"sub": s, "pred": p, "obj": o} for s, p, o in graph]
        )

        df["sub_type"] = [type(s) for s in df["sub"]]
        df["obj_type"] = [type(o) for o in df["obj"]]
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
