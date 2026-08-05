"""Unit tests for rdfine.graph_reader.GraphReader.

Tier 1 (add/ask/check_exists/construct/filter/remove/rename/select/
serialize/traverse + .df/.graph) is the surface directly called by
``compilers/`` and gets the most thorough coverage. Tier 2 (sparql,
infer, validate) is used internally by rdfine itself / the demo
notebook / the pipeline-generator's own test suite, not called by name
from a compiler, but is just as load-bearing.
"""

import pandas as pd
import pytest
from rdflib import Graph, Literal, URIRef

from rdfine import GraphReader, PrefixStore

EX = "http://example.org/"


# --- construction ------------------------------------------------------


def test_init_from_graph_derives_prefix_store(sample_graph):
    reader = GraphReader(sample_graph)
    assert reader.prefix_store.fetch_prefix(EX + "a") == "ex"


def test_init_from_dataframe_requires_prefix_store():
    df = pd.DataFrame(columns=["sub", "pred", "obj", "sub_type", "obj_type"])
    with pytest.raises(ValueError):
        GraphReader(df)


def test_init_from_dataframe_with_prefix_store():
    store = PrefixStore({"ex": EX})
    df = pd.DataFrame.from_records(
        [
            {
                "sub": "ex:a",
                "pred": "ex:knows",
                "obj": "ex:b",
                "sub_type": URIRef,
                "obj_type": URIRef,
            }
        ]
    )
    reader = GraphReader(df, prefix_store=store)
    assert len(reader.graph) == 1


def test_init_rejects_unsupported_type():
    with pytest.raises(TypeError):
        GraphReader(["not", "valid"])


# --- .df / .graph --------------------------------------------------------


def test_df_has_expected_columns(sample_graph):
    reader = GraphReader(sample_graph)
    assert set(reader.df.columns) == {"sub", "pred", "obj", "sub_type", "obj_type"}


def test_df_compacts_known_prefixes(sample_graph):
    reader = GraphReader(sample_graph)
    assert (reader.df["sub"] == "ex:a").any()


def test_df_empty_graph_yields_empty_dataframe_with_columns():
    reader = GraphReader(Graph())
    assert reader.df.empty
    assert set(reader.df.columns) == {"sub", "pred", "obj", "sub_type", "obj_type"}


# --- filter --------------------------------------------------------------


def test_filter_keep_by_subject(sample_graph):
    reader = GraphReader(sample_graph)
    result = reader.filter(sub="ex:a")
    assert set(result.df["pred"]) == {"ex:knows", "ex:name"}


def test_filter_drop_action(sample_graph):
    reader = GraphReader(sample_graph)
    result = reader.filter(sub="ex:a", action="drop")
    assert not (result.df["sub"] == "ex:a").any()


def test_filter_list_valued_pattern_matches_any(sample_graph):
    reader = GraphReader(sample_graph)
    result = reader.filter(sub=["ex:a", "ex:b"])
    assert set(result.df["sub"]) == {"ex:a", "ex:b"}


def test_filter_regex_mode(sample_graph):
    reader = GraphReader(sample_graph)
    result = reader.filter(pred="^ex:kn", regex=True)
    assert (result.df["pred"] == "ex:knows").all()


def test_filter_invalid_action_raises(sample_graph):
    reader = GraphReader(sample_graph)
    with pytest.raises(ValueError):
        reader.filter(sub="ex:a", action="invalid")


# --- check_exists ----------------------------------------------------------


def test_check_exists_true_for_subject(sample_graph):
    reader = GraphReader(sample_graph)
    assert reader.check_exists("ex:a")


def test_check_exists_false_for_unknown_node(sample_graph):
    reader = GraphReader(sample_graph)
    assert not reader.check_exists("ex:doesnotexist")


# --- select / ask / construct --------------------------------------------


def test_select_returns_dataframe(sample_graph):
    reader = GraphReader(sample_graph)
    df = reader.select("?s ?o", "?s ex:knows ?o .")
    assert len(df) == 2


def test_ask_true(sample_graph):
    reader = GraphReader(sample_graph)
    assert reader.ask("ex:a ex:knows ex:b .")


def test_ask_false(sample_graph):
    reader = GraphReader(sample_graph)
    assert not reader.ask("ex:a ex:knows ex:z .")


def test_construct_returns_matching_triples(sample_graph):
    reader = GraphReader(sample_graph)
    result = reader.construct("?s ex:derivedKnows ?o .", "?s ex:knows ?o .")
    assert len(result.df) == 2


def test_construct_empty_result_returns_empty_reader_not_raise(sample_graph):
    """Regression test: construct() used to raise ValueError on an empty
    result; it must now return a usable empty GraphReader instead."""
    reader = GraphReader(sample_graph)
    result = reader.construct("?s ex:nope ?o .", "?s ex:doesnotexist ?o .")
    assert result.df.empty


# --- add / remove ----------------------------------------------------------


def test_add_is_set_union(sample_graph):
    reader = GraphReader(sample_graph)
    extra = Graph()
    extra.add((URIRef(EX + "d"), URIRef(EX + "knows"), URIRef(EX + "e")))
    result = reader.add(extra)
    assert len(result.graph) == len(sample_graph) + 1


def test_add_identical_triple_is_noop(sample_graph):
    reader = GraphReader(sample_graph)
    result = reader.add(sample_graph)
    assert len(result.graph) == len(sample_graph)


def test_remove_drops_matching_triples(sample_graph):
    reader = GraphReader(sample_graph)
    to_remove = Graph()
    to_remove.add((URIRef(EX + "a"), URIRef(EX + "knows"), URIRef(EX + "b")))
    result = reader.remove(to_remove)
    assert not result.ask("ex:a ex:knows ex:b .")


# --- rename ------------------------------------------------------------


def test_rename_updates_subject_and_object_occurrences(sample_graph):
    reader = GraphReader(sample_graph)
    result = reader.rename("ex:b", "ex:renamed")
    assert result.ask("ex:a ex:knows ex:renamed .")
    assert result.ask("ex:renamed ex:knows ex:c .")
    assert not result.check_exists("ex:b")


# --- traverse ------------------------------------------------------------


def test_traverse_along_direction_follows_forward_edges(sample_graph):
    reader = GraphReader(sample_graph)
    result = reader.traverse("ex:a")
    # a -> knows -> b -> knows -> c, plus a's name literal.
    assert result.ask("ex:a ex:knows ex:b .")
    assert result.ask("ex:b ex:knows ex:c .")


def test_traverse_missing_root_raises_nameerror(sample_graph):
    reader = GraphReader(sample_graph)
    with pytest.raises(NameError):
        reader.traverse("ex:doesnotexist")


def test_traverse_against_direction(sample_graph):
    reader = GraphReader(sample_graph)
    result = reader.traverse("ex:c", direction="against")
    assert result.ask("ex:b ex:knows ex:c .")
    assert result.ask("ex:a ex:knows ex:b .")


def test_traverse_exclude_predicate(sample_graph):
    reader = GraphReader(sample_graph)
    result = reader.traverse("ex:a", exclude=["ex:name"])
    assert not result.ask('ex:a ex:name "Alice" .')


# --- serialize -------------------------------------------------------------


def test_serialize_ttl_roundtrips(sample_graph):
    reader = GraphReader(sample_graph)
    ttl = reader.serialize("ttl")
    reparsed = Graph().parse(data=ttl, format="ttl")
    assert len(reparsed) == len(sample_graph)


# --- sparql (Tier 2 — used internally by select/ask/construct) -------------


def test_sparql_select(sample_graph):
    reader = GraphReader(sample_graph)
    df = reader.sparql("SELECT ?s WHERE { ?s ex:knows ?o . }")
    assert len(df) == 2


def test_sparql_unsupported_query_type_raises(sample_graph):
    reader = GraphReader(sample_graph)
    with pytest.raises(TypeError):
        reader.sparql("DESCRIBE ex:a")


# --- infer (Tier 2) --------------------------------------------------------


def test_infer_applies_construct_rule_to_fixpoint(sample_graph, tmp_path):
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text(
        "context:\n"
        "  ex: http://example.org/\n"
        "rules:\n"
        "  - construct: |\n"
        "      ?s ex:reachesTransitively ?o .\n"
        "    where: |\n"
        "      ?s ex:knows ?o .\n"
    )
    reader = GraphReader(sample_graph).infer(str(rules_file))
    assert reader.ask("ex:a ex:reachesTransitively ex:b .")


def test_infer_raises_if_not_converged(sample_graph, tmp_path):
    # Each pass mints a fresh blank node, so this never reaches a fixpoint.
    rules_file = tmp_path / "rules.yaml"
    rules_file.write_text(
        "context:\n"
        "  ex: http://example.org/\n"
        "rules:\n"
        "  - construct: |\n"
        '      ?s ex:hasCounter [ ex:value "x" ] .\n'
        "    where: |\n"
        "      ?s ex:knows ?o .\n"
    )
    with pytest.raises(RuntimeError):
        GraphReader(sample_graph).infer(str(rules_file), max_repetitions=2)


# --- validate (Tier 2) ------------------------------------------------------


def test_validate_conforms_when_no_shapes_violated(sample_graph):
    reader = GraphReader(sample_graph)
    report = reader.validate()
    assert report.ask("?r sh:conforms true .")


def test_validate_reports_violation():
    g = Graph()
    g.parse(
        data="""
        @prefix ex: <http://example.org/> .
        @prefix sh: <http://www.w3.org/ns/shacl#> .

        ex:Alice a ex:Person .

        ex:PersonShape a sh:NodeShape ;
            sh:targetClass ex:Person ;
            sh:property [
                sh:path ex:name ;
                sh:minCount 1 ;
            ] .
        """,
        format="turtle",
    )
    reader = GraphReader(g)
    report = reader.validate()
    assert not report.ask("?r sh:conforms true .")
