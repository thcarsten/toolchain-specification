from rdflib import Graph, Literal, URIRef, BNode, Namespace
from pyld import jsonld
import json
import pandas as pd

# mapping of XSD types to Python conversion functions
XSD_TO_PYTHON = {
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


class GraphReader:
    """
    Class to read data from an RDF graph into Python.
    Properties:
            - graph: the raw graph as RDFlib Graph type
            - prefixes: a Python dictionary with prefixes and urls as key-value pairs
    Methods:
            - read_graph: provide a (list of) ttl-files containing the graph to initialize the object properties
            - execute_query:  Querying the graph by SELECT or CONSTRUCT SPARQL statements
            - get_triples: Get triples that match a pattern of sub pred and obj (you can set one or more elements of the triple)
            - extract subgraph: Extracts a subgraph from a bigger graph, can be returned as dict if as_dict = True
            - rename_node_in_graph: Returns original graph with nodes in a matching pattern being renamed
    """

    def __init__(self):
        self.graph = Graph()
        self.prefixes = {}
        self._basepath = "http://myproject.local/"  # This is just a dummy path needed for clean serializing of owl import paths to turtle

    ###########################
    # READ IN GRAPH
    ###########################

    def read_graph(self, input_folder: str, filelist: list[str] | str):
        """
        Reads a graph into memory by returning a RDFlib graph and a dictionary of prefixes
        TODO: Support different file formats
        """

        # Save typing
        if isinstance(filelist, str):
            filelist = [filelist]

        # Loading the graph
        g = Graph()
        dict_prefixes = {}
        for filename in filelist:
            g.parse(input_folder + filename, format="turtle", publicID=self._basepath)
            dict_prefixes_from_file = self._read_prefixes_from_ttl_file(
                input_folder + filename
            )
            dict_prefixes.update(dict_prefixes_from_file)

        # store results in the instance
        self.graph = g
        self.prefixes = dict_prefixes

    def _read_prefixes_from_ttl_file(self, filepath: str) -> dict:
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

    ###########################
    # EXECUTE QUERY
    ###########################

    # Executes a SPARQL query against the graph
    # TODO: There is still a bug that it returns the entire graph in case the query should return nothing
    def execute_query(
        self,
        query: str,
        graph: Graph | None = None,  # defaults to self.graph
        initBindings: dict | None = None,
        simplify: bool = True,
    ) -> pd.DataFrame | Graph:
        """
        executes a select query on a graph and returning results as dataFrame
        Arguments:
            - simplify: Checks whether cell content is still an RDFLibType, and if so, simplifies to a primitive type
        """

        # Provide default values if no argument is provided
        graph = graph or self.graph
        initBindings = initBindings or {}

        # Automatically append prefixes to query
        query = self._append_prefixes_to_query(query)

        results = graph.query(query, initBindings=initBindings)
        if results.type == "SELECT":
            return self._tabulate_select_results(results, simplify=simplify)
        elif results.type == "CONSTRUCT":
            if simplify:
                if results.graph:  # if results.graph is not falsly (empty)
                    return self.get_triples(graph=results.graph)
                else:  # if results.graph is empty return empty dataframe
                    return pd.DataFrame(columns=["sub", "pred", "obj"])
            else:
                # Bind prefixes to namespaces of graph
                self._bind_prefixes(results.graph, self.prefixes)
                return results.graph

    def _tabulate_select_results(self, results, simplify=True) -> pd.DataFrame:
        """Turn the results of a SPARQL SELECT query into a DataFrame."""
        rows = list(results)
        df = pd.DataFrame(rows, columns=[str(var) for var in results.vars])
        if simplify:
            df = self._cleanup_tabulated_select_results(df)
        return df

    def _cleanup_tabulated_select_results(
        self, tabulated_results: pd.DataFrame
    ) -> pd.DataFrame:
        """Checks whether cell content is still an RDFLibType, and if so, simplifies to a primitive type
        Also compacts prefixes where possible
        """
        # Apply to entire DataFrame
        tabulated_results.map(self._node_to_str)

        # Compact the output
        compacted_results = self._apply_prefixes_to_dataframe(tabulated_results)

        return compacted_results

    def _apply_prefixes_to_dataframe(
        self, df: pd.DataFrame, prefixes: dict | None = None
    ) -> pd.DataFrame:
        # Provide default values if no argument is provided
        prefixes = prefixes or self.prefixes
        if not prefixes:
            raise (LookupError("tried to append prefixes, but prefixes not found."))

        # I always need to try to match the longest prefixes first, so I need to define the order first
        df_prefixes_order = pd.DataFrame.from_records(
            [{"prefix": prefix, "url": prefixes[prefix]} for prefix in prefixes]
        )
        df_prefixes_order["length"] = [len(url) for url in df_prefixes_order["url"]]
        df_prefixes_order = df_prefixes_order.sort_values("length", ascending=False)
        df_prefixes_order = df_prefixes_order.reset_index(drop=True)

        # Applying the prefixes in the priority as specified above
        df = df.map(
            lambda cell_url: self._apply_prefix_to_string(
                cell_url, df_prefixes_order=df_prefixes_order
            )
        )
        return df

    def _apply_prefix_to_string(
        self, cell_url: str, df_prefixes_order: pd.DataFrame
    ) -> str:
        if isinstance(cell_url, str):
            for index, row in df_prefixes_order.iterrows():
                if cell_url.startswith(row["url"]):
                    cell_url = row["prefix"] + ":" + cell_url.removeprefix(row["url"])
                    break

        return cell_url

    def _append_prefixes_to_query(
        self, query: str, prefixes: dict | None = None
    ) -> str:
        # Provide default values if no argument is provided
        prefixes = prefixes or self.prefixes

        if not prefixes:
            raise (LookupError("tried to append prefixes, but prefixes not found."))

        # Preparing the prefixes
        prefix_textlines = []
        for prefix in prefixes:
            prefix_textline = f"PREFIX {prefix}: <{prefixes[prefix]}>"
            prefix_textlines.append(prefix_textline)
        prefix_textblock = "\n".join(prefix_textlines)

        # Prepending the prefixes to the query
        query = "\n".join([prefix_textblock, query])

        return query

    ###########################
    # EXTRACT SUBGRAPH
    ###########################

    def extract_subgraph(
        self,
        node_id: str,
        graph: Graph | None = None,
        direction: str = "along",  # ["along", "against", "both"]
        exclude: list[str] | None | str = None,
        along: list[str] | None | str = None,
        against: list[str] | None | str = None,
        prune: list[str] | None | str = None,
        prefixes: dict | None = None,
        as_dict: bool = False,  # By default outputs as graph, unless as_dict is set to true
    ) -> Graph | dict:
        """
        Recursively extract the subgraph starting at root_node.

        Parameters
        ----------
        - node_id : The starting node of the subgraph.
        - graph : The RDF graph to traverse.
        - direction: str of "along", "against" or "both". Default is both. Set whether you want to follow edges only in their regular direction (along), inverse direction (against) or both.
        - exclude: List of predicates to ignore. Can use prefixes if 'prefixes' is provided.
        - along: Allows you to provide a list of predicates that should be exclusivelu followed in direction 'along'. Overwrites direction for that predicate
        - against: Allows you to provide a list of predicates that should be exclusivelu followed in direction 'against'. Overwrites direction for that predicate
        - prune: Prevents to continue search on the neighbors. Allows for example to fetch dependencies of a Processor via osw:hasDependency, without fetching further info on that dependency
        - prefixes : Mapping from prefix to URI string, e.g. {"ex": "http://example.org/ldio#"}
        """

        # Provide default values if no argument is provided
        prefixes = prefixes or self.prefixes
        graph = graph or self.graph

        # Provide full URIs for predicates in method arguments
        exclude = self._normalize_url_list(exclude)
        along = self._normalize_url_list(along)
        against = self._normalize_url_list(against)
        prune = self._normalize_url_list(prune)

        visited_nodes = set()  # prevent cycles
        subgraph_triples = set()  # store triples of the subgraph

        # Converting the node_id string to a RDFlib node reference
        root_node = self._expand_url(node_id)

        # Raise exception if root_node is not found
        if not self._check_node_in_graph(root_node):
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
            for p, o in graph.predicate_objects(subject=node):
                if (direction in ["along", "both"]) or (p in along):
                    # Skip blacklisted predicates or predicates that overwrite direction
                    if (p in exclude) or (p in against and p not in along):
                        continue
                    subgraph_triples.add((node, p, o))
                    # Recurse for objects that are URIRef or BNode
                    if isinstance(o, (URIRef, BNode)) and (p not in prune):
                        neighbors.add(o)

            # following AGAINST the edge's direction: Add all tripls with node as object
            for s, p in graph.subject_predicates(object=node):
                if (direction in ["against", "both"]) or (p in against):
                    # Skip blacklisted predicates
                    if (p in exclude) or (p in along and p not in against):
                        continue
                    subgraph_triples.add((s, p, node))
                    # Recurse for objects that are URIRef or BNode
                    if isinstance(s, (URIRef, BNode)) and (p not in prune):
                        neighbors.add(s)

            for neighbor in neighbors:
                _dfs(neighbor)

        _dfs(root_node)

        # Turn the list of triples to a proper RDF graph before returning
        # Create a new graph
        new_graph = Graph()
        for s, p, o in subgraph_triples:
            new_graph.add((s, p, o))
        # Bind prefixes to namespaces of graph
        self._bind_prefixes(new_graph, prefixes)

        # output as graph or as dict
        if not as_dict:
            return new_graph
        else:
            return self._graph_to_dict(new_graph, node_id=node_id, prefixes=prefixes)

    def _expand_url(self, url: str, prefixes: dict | None = None, as_uri=True) -> str:
        """
        Expands a compacted url based on the prefixes provided,
        optionally also convert uri
        """
        # Provide default values if no argument is provided
        prefixes = prefixes or self.prefixes
        if not prefixes:
            raise (LookupError("tried to append prefixes, but prefixes not found."))

        expanded_url = url
        if ":" in url:
            prefix, local = url.split(":", 1)
            if prefix in prefixes:
                expanded_url = prefixes[prefix] + local

        if as_uri:
            return URIRef(expanded_url)
        else:
            return expanded_url

    def _normalize_url_list(self, url_list: list[str] | str | None) -> list:
        """
        For each url in a list: expands a compacted url based on the prefixes provided
        """
        if not url_list:
            return []

        if isinstance(url_list, str):
            url_list = [url_list]

        if not isinstance(url_list, list):
            raise TypeError(f"Expected list or str, got {type(url_list)}")

        return [
            (self._expand_url(pred, as_uri=True) if ":" in pred else pred)
            for pred in url_list
        ]

    def _check_node_in_graph(self, node_id: str | URIRef) -> bool:
        """
        Checks whether a node_id occurs in a graph, either as subject or object
        Returns boolean
        """

        # If node_id is not yet a URIRef, do so now
        if isinstance(node_id, str):
            node_id = self._expand_url(node_id)
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
        df_object = self.execute_query(is_subject_query, initBindings=bindings)

        bool_node_is_in_graph = (len(df_subject) + len(df_object)) > 0
        return bool_node_is_in_graph

    ###########################
    # GRAPH TO DICT
    ###########################

    # Based on a subgraph, I want to create a JSON LD with a frame
    def _graph_to_dict(
        self, graph: Graph, node_id: str, prefixes: dict | None = None
    ) -> dict:
        """
        Converts a graph to a Python dictionary. Needs a node_id as starting point to build the nested structure.
        Dictionary keys can be compacted by supplying prefixes.
        """

        # Provide default values if no argument is provided
        prefixes = prefixes or self.prefixes
        if not prefixes:
            raise (LookupError("tried to append prefixes, but prefixes not found."))

        # Serialize RDF graph as JSON-LD
        json_expanded = json.loads(graph.serialize(format="json-ld", indent=4))

        # filter out the default empty prefix, because pyLD cannot handle these
        filtered_prefixes = self._replace_keys_in_dict(prefixes, {"": "default_prefix"})
        # if node_id contains default_prefix, it neds to be converted as well for the frame to work
        if node_id[0] == ":":
            node_id = "default_prefix" + node_id

        # For the frame to work with prefixed node_id's, you have to make sure the node_id is first expanded properly
        expanded_node_id = self._expand_url(node_id, filtered_prefixes, as_uri=False)

        # Define the frame (this could be parameterized as well in case I ever want to use a different frame)
        frame = {
            "@context": filtered_prefixes,
            "@embed": "@always",
            "@explicit": False,
            "@id": expanded_node_id,  # root node
        }

        # Apply the frame
        json_framed = jsonld.frame(json_expanded, frame)

        # Compact the frame (handle prefixes)
        json_compacted = jsonld.compact(json_framed, filtered_prefixes)

        # Ideally here I would want another step that replaces the placeholder prefix "default_prefix" back with ""
        json_compacted = self._replace_prefix(json_compacted, "default_prefix", "")

        # I convert the xsd values to primitive according to the mapping in the config
        json_clean = self._convert_xsd_literals(json_compacted)

        # Return result as dict
        return json_clean

    def _replace_keys_in_dict(self, original_dict: dict, replace_dict: dict) -> dict:
        """Return a copy of original_dict with keys replaced according to replace_dict"""

        return {
            replace_dict.get(key, key): value for key, value in original_dict.items()
        }

    def _replace_prefix(self, obj, prefix, replacement):
        """
        Recursively replace prefix with a replacement in dict keys and string values.
        TODO: Does not consider whether prefix stands at beginning or not, so may lead to bugs
        """
        if isinstance(obj, dict):
            new_dict = {}
            for key, value in obj.items():
                # Replace in key if it's a string
                new_key = (
                    key.replace(prefix, replacement) if isinstance(key, str) else key
                )
                # Recursively process value
                new_dict[new_key] = self._replace_prefix(value, prefix, replacement)
            return new_dict

        elif isinstance(obj, list):
            return [self._replace_prefix(item, prefix, replacement) for item in obj]

        elif isinstance(obj, str):
            return obj.replace(prefix, replacement)

        else:
            return obj

    def _convert_xsd_literals(self, obj):
        """
        Recursively convert JSON-LD xsd-typed literals to native Python types.
        Returns a deep copy of obj with converted values.
        """

        if isinstance(obj, dict):
            # handle xsd value objects
            if "@value" in obj and "@type" in obj:
                xsd_type = obj["@type"]
                value = obj["@value"]
                converter = XSD_TO_PYTHON.get(xsd_type, lambda x: x)
                try:
                    return converter(value)
                except Exception:
                    # fallback to original value if conversion fails
                    return value
            # otherwise recursively process all dict items
            return {k: self._convert_xsd_literals(v) for k, v in obj.items()}

        elif isinstance(obj, list):
            # recursively process list elements
            return [self._convert_xsd_literals(item) for item in obj]

        else:
            # primitive types stay as-is
            return obj

    ###########################
    # UTILITY FUNCTIONS
    ###########################

    def get_triples(
        self,
        sub: str | None = None,
        pred: str | None = None,
        obj: str | None = None,
        simplify: bool = True,
        graph: Graph | None = None,
    ) -> pd.DataFrame | list[str]:
        """
        TODO: Does not yet work with full URI's, because these require additional <brackets>
        Allows to receive any triples that match the provided pattern of sub, pred, obj
        Undefined parts of a triple are turned into variables
        Only returns the variables
        Optionally turns the results into a list if simplify = True and only one part of a triple is missing
        """

        # Provide default values if no argument is provided
        graph = graph or self.graph
        sub = sub or "?sub"
        pred = pred or "?pred"
        obj = obj or "?obj"
        triples = [sub, pred, obj]

        # Creating the SELECT line
        variable_list = [entry for entry in triples if entry.startswith("?")]
        select_line = " ".join(variable_list)

        # Executing the query
        query = f"""        
            SELECT {select_line}
            WHERE {{
                {sub} {pred} {obj} .
            }}
            """
        df_result = self.execute_query(query, graph=graph)

        # Simplify to list for simple queries
        if simplify and len(variable_list) == 1:
            return list(df_result.iloc[:, 0])
        else:
            return df_result

    def rename_node_in_graph(
        self,
        match_pattern: str,
        new_node_name: str,
        graph: Graph | None = None,
        simplify=False,
    ):
        """
        Replaces a node with a new_node_name.
        Match_pattern has to point to a ?target, to identify the node that should be renamed.
        Has to provide graph and graph_reader to execute the query.
        Simplify outputs as graph of df, respectively.

        TODO: If match_pattern currently not matches, it returns an empty graph. This is clearly a bug,
        because it should return the original unaltered graph instead.
        """

        # Provide default values if no argument is provided
        graph = graph or self.graph

        # For correct behavior is necessary to expand the url of the new_node_name
        new_node_name = self._expand_url(new_node_name)
        # Make the new_node_name a proper uri if it is not already one:
        if not new_node_name.startswith("<"):
            new_node_name = f"<{new_node_name}>"

        # Raise exception if the match_pattern does not follow the right constraints
        if "?target" not in match_pattern:
            raise (
                NameError(
                    "match_pattern must contain ?target to identify the node to be renamed."
                )
            )
        elif "?s " in match_pattern or "?p " in match_pattern or "?o " in match_pattern:
            raise (
                NameError("match_pattern must not contain variables named ?s ?p or ?o.")
            )

        rename_node_query = f"""
                CONSTRUCT {{
                ?newS ?p ?newO .
                }}
                WHERE {{

                # First identify the node to rename
                {match_pattern}.

                # Then copy whole graph
                ?s ?p ?o .

                BIND(IF(?s = ?target, {new_node_name}, ?s) AS ?newS)
                BIND(IF(?o = ?target, {new_node_name}, ?o) AS ?newO)
                }}
            """

        return self.execute_query(rename_node_query, graph=graph, simplify=simplify)

    def _bind_prefixes(
        self, graph: Graph | None = None, prefixes: dict | None = None
    ) -> None:
        """
        Binds namespaces to the Graph object so that serialization can utilize these namespaces
        """
        # Provide default values if no argument is provided
        graph = graph or self.graph
        prefixes = prefixes or self.prefixes

        for key in prefixes:
            graph.bind(key, prefixes[key], override=True)

    def merge_graphs(self, graphs=list[Graph]) -> Graph:
        """
        Adds all triples of one graph to another graph
        Makes sure that the name spaces of both graphs are merged
        """

        output_graph = Graph()

        for graph_to_be_added in graphs:
            # Adding triples to graph
            for s, p, o in graph_to_be_added:
                output_graph.add((s, p, o))
            # Adding namespaces to graph
            prefixes = self._extract_prefixes_from_namespace(graph_to_be_added)
            self._bind_prefixes(graph=output_graph, prefixes=prefixes)

        return output_graph

    def _node_to_str(self, cell) -> str:
        """
        Converts a RDFlibType to a simple string type
        Does some cleaning to remove trailing symbols
        """
        if hasattr(cell, "n3") and callable(cell.n3):
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
        else:
            return cell

    def _extract_prefixes_from_namespace(self, graph: Graph) -> dict:
        """
        Takes the native namespaces from the rdflib library and
        turns them into a dict
        """
        prefixes = {}
        for namespace in graph.namespaces():
            prefix = list(namespace)[0]
            url = str(list(namespace)[1])
            prefixes[prefix] = self._node_to_str(url)
        return prefixes
