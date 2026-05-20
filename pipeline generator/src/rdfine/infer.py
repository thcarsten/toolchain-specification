from .graph_reader import GraphReader
from .prefix_store import PrefixStore
from .utils import merge_graphs, load_yaml
from rdflib import Graph


def infer(graph, filepath, max_repetitions: int = 10) -> Graph:
    """
    Infers new triples based on inference rules contained in file.
    Stops when no new triples are added (fixed point) or max_repetitions is reached.
    """

    dict_inference = load_yaml(filepath)

    prefix_store = PrefixStore(dict_inference["context"])
    inference_rules = dict_inference["rules"]

    complete_graph = graph
    prefix_store.bind_to_namespace(complete_graph)

    current_repetition = 0

    while current_repetition < max_repetitions:
        current_repetition += 1

        # Count before applying rules
        reader = GraphReader(complete_graph)
        number_of_triples_before = len(reader.get_triples())

        # Apply all rules
        for rule in inference_rules:
            if "construct" not in rule or "where" not in rule:
                raise ValueError("Invalid rule format: missing 'construct' or 'where'")

            enriched_graph_reader = GraphReader(complete_graph)
            enriched_graph_reader.prefix_store.load(
                dict(prefix_store.prefixes), replace=False
            )

            query = f"""
            CONSTRUCT {{
                {rule["construct"]}
            }}
            WHERE {{
                {rule["where"]}
            }}
            """

            query = prefix_store.include_in_query(query)

            query_results = complete_graph.query(query)
            inferred_graph = query_results.graph

            prefix_store.bind_to_namespace(inferred_graph)

            # Merge inferred triples
            complete_graph = merge_graphs([complete_graph, inferred_graph])

        # Count after applying all rules
        reader = GraphReader(complete_graph)
        number_of_triples_after = len(reader.get_triples())

        # Stop if no new triples were added (fixed point reached)
        if number_of_triples_after <= number_of_triples_before:
            break

    else:
        # Only triggered if while loop didn't break
        raise RuntimeError(
            f"Inference did not converge after {max_repetitions} repetitions."
        )

    return complete_graph
