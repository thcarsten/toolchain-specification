from .prefix_store import PrefixStore
from rdflib import Graph, URIRef
import pandas as pd


class GraphTable:
    """
    GraphTable provides a table view of a RDFlib graph. As such, it is build around a pandas.dataframe

    Properties:
            - self.df
            - self.prefix_store
    Methods:
        - load(graph, TODO: replace)
        - subset(keep, drop)
        _ to_graph
    """

    def __init__(
        self, source: Graph | pd.DataFrame, prefix_store: PrefixStore | None = None
    ) -> None:
        """
        Creates the df and initializes the prefix store based on a Graph.
        Can also initialize based on a dataframe, if a prefix_store is provided.
        """

        # Initializing the prefix store
        if prefix_store:
            self.prefix_store = prefix_store
        elif isinstance(source, Graph):
            self.prefix_store = PrefixStore(source)
        else:
            raise ReferenceError(
                f"{type(source)} requires a PrefixStore for initialization."
            )

        if isinstance(source, Graph):
            self.df = self._graph_to_df(source)

        elif isinstance(source, pd.DataFrame):
            expected_columns = ["sub", "pred", "obj", "sub_type", "obj_type"]
            if not all(col in source.columns for col in expected_columns):
                raise ReferenceError(
                    f"Initializing with {type(source)} requires the columns {expected_columns}."
                )
            else:
                self.df = source

    def add_triples(self, graph) -> None:
        # TODO: This will break if self.df does have extra columns, this should not be the case
        # turning the to-be-added graph into a df
        df = self._graph_to_df(graph)
        # Merging the new df with the existing df
        self.df = pd.concat([self.df, df])
        # Removing duplicates bc RDF does not know the concept of duplicates
        self.df = self.df.drop_duplicates()

    def remove_triples(self, graph) -> None:
        input_df = self._graph_to_df(graph)
        # Going through each row of input_df and removing it from the graph table
        for index, row in input_df.iterrows():
            filter_dict = {
                "sub": row["sub"],
                "pred": row["pred"],
                "obj": row["obj"],
                "sub_type": row["sub_type"],
                "obj_type": row["obj_type"],
            }
            trimmed_df = self.subset(filter_dict, action="drop")
            self.df = trimmed_df

    def _graph_to_df(self, graph) -> pd.DataFrame:
        """
        Helper-function which turns a graph into a df
        """

        df = pd.DataFrame.from_records(
            [{"sub": s, "pred": p, "obj": o} for s, p, o in graph]
        )
        df["sub_type"] = df.apply(lambda row: type(row["sub"]), axis=1)
        df["obj_type"] = df.apply(lambda row: type(row["obj"]), axis=1)
        prefix_store = PrefixStore(graph)
        df = df.map(prefix_store.node_to_python)
        return df

    def to_graph(self) -> Graph:
        """
        Collects the triples in a graph and returns them
        """

        output_graph = Graph()

        for index, row in self.df.iterrows():
            sub = self.prefix_store.python_to_node(row["sub"], row["sub_type"])
            pred = self.prefix_store.python_to_node(row["pred"], URIRef)
            obj = self.prefix_store.python_to_node(row["obj"], row["obj_type"])
            output_graph.add((sub, pred, obj))

        self.prefix_store.bind_to_namespace(output_graph)

        return output_graph

    def subset(self, filters, action="keep") -> pd.DataFrame:
        """
        Selects triples based on a matching pattern.
        Matching triples are either kept or dropped, depending on the action.
        filters is a dict, typically of format sub, pred, obj.
        Values are a singleton or a list. In case of a list, several matches can be provided.
        But you can also filter on sub_type or any other columns that you added
        """

        df_subset = self.df

        mask = pd.Series(True, index=df_subset.index)

        for col, value in filters.items():
            if value is not None:
                if isinstance(value, list):
                    mask &= df_subset[col].isin(value)
                else:
                    mask &= df_subset[col] == value

        if action == "keep":
            df_subset = df_subset.loc[mask]
        elif action == "drop":
            df_subset = df_subset.loc[~mask]
        else:
            raise ValueError("action has to be 'keep' or 'drop'")

        df_subset = df_subset.reset_index(drop=True)
        return df_subset

    # String shown upon print
    def __repr__(self):
        return f"GraphTable({self.df[["sub", "pred", "obj"]]})"

    # Indexing prefix_store returns indexed self.prefixes
    def __getitem__(self, key):
        return self.df[key]

    def __delitem__(self, key):
        del self.df[key]
