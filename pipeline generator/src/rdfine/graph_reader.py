from .prefix_store import PrefixStore
from rdflib import Graph, URIRef, BNode
import pandas as pd


class GraphReader:
    """
    Class to read data from an RDF graph into Python.
    Properties:
            - graph: the raw graph as RDFlib Graph type
            - prefix_store: a PrefixStore with the prefixes loaded from the graph
            - _basepath: Used for resolving relative filepaths
    Methods:
            - load: Load a graph, can either replace or append the existing graph
            - execute_query:  Querying the graph by SELECT or CONSTRUCT SPARQL statements, returns results as dataframe with native types
            - get_triples: Get triples that match a pattern of sub pred and obj (you can set one or more elements of the triple)
            - keep_subgraph: Allows trimming the graph by follsowing certain predicates from a starting point
            - to_dict: Returns the graph as dictionary
            - check_node_exists: Check whether a node_id is present in the graph
            - rename_node_in_graph: Allows to target nodes with a matching pattern and replace their url. Useful to rename blank nodes for example
            - to_graph: Exposes a deep copy of the graph
    """

    def __init__(self, graph: Graph):
        self.graph = graph  # Initiate an empty graph
        self.prefix_store = PrefixStore(graph)
        self._basepath = "file:///workspace/pipeline/"  # Has to match working directory path in docker file. TODO: THis has to be read out dynamically in the future
        self._blanknode_prefix = {
            "bn_": "https://materialized_blanknode.com/"
        }  # The prefix being used to indicate blanknodes
        self.prefix_store.load(self._blanknode_prefix, replace=False)
        self.prefix_store.bind_to_namespace(self.graph)
        self.materialize_blank_nodes()

    ###########################
    # I/O
    ###########################

    def add_triples(self, source: Graph) -> None:
        """
        Adds triples from a graph to self.graph
        """

        """LOADING THE GRAPH"""
        # Loading the graph
        loaded_graph = source

        # In order to make sure that blank nodes don't conflict, I need to
        # serialize and re-parse the graph first. I also need to restore the
        # blank nodes of the existing graph first.
        self.restore_blank_nodes()
        serialized_graph = loaded_graph.serialize(format="turtle")
        self.graph.parse(
            data=serialized_graph, format="turtle", publicID=self._basepath
        )
        # After new triples have been added, turning blank nodes into URIrefs
        self.materialize_blank_nodes()

        """LOADING THE PREFIXES"""
        self.prefix_store.load(source, replace=False)

        # Prefix for materialized blank nodes has to be added to the prefix_store as well
        self.prefix_store.load(self._blanknode_prefix, replace=False)
        # also append prefixes to the RDFlib namespace of the graph
        self.prefix_store.bind_to_namespace(self.graph)

    def to_graph(self) -> Graph:
        """
        Returns a copy of the graph, making sure it is a 'deep copy'
        Also reverts the blanknodes back to proper blank nodes
        """
        return self.graph

    def materialize_blank_nodes(self) -> None:
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

        # Replacing the existing graph
        self.prefix_store.bind_to_namespace(new_graph)
        self.graph = new_graph

    def restore_blank_nodes(self) -> None:
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

    ###########################
    # EXECUTE QUERY
    ###########################

    # Executes a SPARQL query against the graph
    # TODO: There is still a bug that it returns the entire graph in case the query should return nothing
    # TODO: Split up based on query type (so detect query type first)
    def execute_query(
        self,
        query: str,
        initBindings: dict | None = None,
    ) -> pd.DataFrame | Graph:
        """
        executes a select or construct query on a graph and returning results as dataFrame
        """

        # Provide default values if no argument is provided
        initBindings = initBindings or {}

        # Execute query
        query = self.prefix_store.include_in_query(query)
        results = self.graph.query(query, initBindings=initBindings)

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
                return results.graph
            else:  # if results.graph is empty return empty dataframe
                raise Exception("Constructed graph returned empty.")
        else:
            raise TypeError(
                "SELECT or CONSTRUCT query expected, {results.type} received."
            )

    ###########################
    # EXTRACT SUBGRAPH
    ###########################

    def extract_subgraph(
        self,
        node_id: str,
        direction: str = "along",  # ["along", "against", "both"]
        exclude: list[str] | None | str = None,
        along: list[str] | None | str = None,
        against: list[str] | None | str = None,
        prune: list[str] | None | str = None,
    ) -> Graph:
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
        if not self.check_node_exists(root_node):
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
        return subgraph

    ###########################
    # OTHER
    ###########################

    def check_node_exists(self, node_id: str | URIRef) -> bool:
        """
        Checks whether a node_id occurs in a graph, either as subject or object
        Returns boolean
        """

        # If node_id is not yet a URIRef, do so now
        if isinstance(node_id, str):
            node_id = self.prefix_store.python_to_node(node_id, URIRef)
        bindings = {"target": node_id}

        # Query checking whether node_id occurs as subject
        is_subject_query = f"""        
                SELECT ?s
                WHERE {{
                    # Start at the config root
                    ?s ?p ?o .
                    FILTER (sameTerm(?s, ?target))
                }}
                """
        # Query checking whether node_id occurs as object
        is_object_query = f"""        
                SELECT ?o
                WHERE {{
                    # Start at the config root
                    ?s ?p ?o .
                    FILTER (sameTerm(?o, ?target))
                }}
                """

        # Counting the occurances as subject or object
        df_subject = self.execute_query(is_subject_query, initBindings=bindings)
        df_object = self.execute_query(is_object_query, initBindings=bindings)

        bool_node_is_in_graph = (len(df_subject) + len(df_object)) > 0
        return bool_node_is_in_graph

    def get_triples(
        self,
        sub: str = "?sub",
        pred: str = "?pred",
        obj: str = "?obj",
    ) -> pd.DataFrame:
        """
        TODO: Does not yet work with full URI's, because these require additional <brackets>
        Allows to receive any triples that match the provided pattern of sub, pred, obj
        Undefined parts of a triple are turned into variables
        """

        if sub.startswith("_:") or pred.startswith("_:") or obj.startswith("_:"):
            raise NotImplementedError(
                "Returning triples for blank nodes is currently not implemented."
            )

        # Creating the SELECT line
        variable_list = [
            triplet for triplet in [sub, pred, obj] if triplet.startswith("?")
        ]
        select_line = " ".join(variable_list)

        # Executing the query
        query = f"""        
            SELECT {select_line}
            WHERE {{
                {sub} {pred} {obj} .
            }}
            """

        df_result = self.execute_query(query)

        # To display df_result always as sub, pred, obj
        triples = {"sub": sub, "pred": pred, "obj": obj}
        for key in triples.keys():
            triplet = triples[key]
            if triplet in variable_list:
                continue
            else:
                df_result[key] = triplet
        # Reorder columns
        df_result = df_result[["sub", "pred", "obj"]]

        return df_result

    def rename(self, old: str, new: str) -> None:
        """
        Replaces a node with a name. Input can be one of two formats.
        - "old_name", "new_name"
        - "match_pattern", "new_name"

        If match_pattern is used, the target node has to be specified via triples.
        The targeted node has to be assigned the special variable ?target.
        All nodes that mach ?target will be assigned the new name.
        This makes it easier to rename blank nodes, which do not have a persistent name.
        Example for match_pattern "?source tc:embedded ?target .". Every ?target that matches gets renamed.
        Match_patterns are identified as such if the key contains the special variable ?target.
        """

        # For correct behavior is necessary to expand the url of the new_node_name
        new_node_name = self.prefix_store.expand(new)
        # Make the new_node_name a proper uri if it is not already one:
        if not new_node_name.startswith("<"):
            new_node_name = f"<{new_node_name}>"

        # Behavior if match_pattern is passed
        if "?target" in old:
            match_pattern = old

            # Reserved variable names may not be used
            for var in ["?oldS", "?oldP", "?oldO", "?newS", "?newP", "?newO"]:
                if var in match_pattern:
                    raise (
                        NameError(
                            f"match_pattern must not contain variable named {var}."
                        )
                    )

            # Constructing the binding block that renames nodes matched by this pattern
            binding_block = f"""
                    BIND(IF(?oldS = ?target, {new_node_name}, ?oldS) AS ?newS)
                    BIND(IF(?oldP = ?target, {new_node_name}, ?oldP) AS ?newP)
                    BIND(IF(?oldO = ?target, {new_node_name}, ?oldO) AS ?newO)

                    FILTER(?oldS = ?target || ?oldP = ?target || ?oldO = ?target)
                """

        else:
            # Turning the old_node_name to a proper url reference
            old_node_name = old
            match_pattern = ""
            old_node_name = self.prefix_store.expand(old_node_name)
            if not old_node_name.startswith("<"):
                old_node_name = f"<{old_node_name}>"

            binding_block = f"""
                    BIND(IF(?oldS = {old_node_name}, {new_node_name}, ?oldS) AS ?newS)
                    BIND(IF(?oldP = {old_node_name}, {new_node_name}, ?oldP) AS ?newP)
                    BIND(IF(?oldO = {old_node_name}, {new_node_name}, ?oldO) AS ?newO)

                    FILTER(?oldS = {old_node_name} || ?oldP = {old_node_name} || ?oldO = {old_node_name})
                """

        rename_query = f"""
                    DELETE {{
                    ?oldS ?oldP ?oldO .
                    }}
                    INSERT {{
                    ?newS ?newP ?newO .
                    }}
                    WHERE {{
                    ?oldS ?oldP ?oldO .
                    {match_pattern}

                    {binding_block}
                    }}
                """
        # Updating the graph according to the query
        rename_query = self.prefix_store.include_in_query(rename_query)
        self.graph.update(rename_query)
