"""Unit tests for rdfine.prefix_store.PrefixStore.

Tier 1 (compact/expand/drop family) gets the most thorough coverage since
it's the only part of PrefixStore called directly by ``compilers/``. Tier 2
(load, apply_prefixes, bind_to_namespace, include_in_query, node/python
conversions) is used internally by GraphReader/GraphDict and is covered
here directly rather than only transitively. Tier 3
(``replace_prefix_in_store``) is supported-but-unused elsewhere, so it gets
minimal coverage.
"""

import pandas as pd
import pytest
from rdflib import BNode, Graph, Literal, URIRef

from rdfine import PrefixStore
from rdfine.prefix_store import PrefixConflictError

EX = "http://example.org/"


# --- construction / load ---------------------------------------------------


def test_init_from_dict():
    s = PrefixStore({"ex": EX})
    assert s.prefixes == {"ex": EX}


def test_init_from_graph():
    g = Graph()
    g.bind("ex", EX)
    s = PrefixStore(g)
    assert s.prefixes.get("ex") == EX


def test_init_rejects_unsupported_source_type():
    with pytest.raises(TypeError):
        PrefixStore(["not", "a", "dict"])


def test_load_replace_true_wipes_prior_state(store):
    store.load({"other": "http://other.org/"})
    assert store.prefixes == {"other": "http://other.org/"}


def test_load_replace_false_upserts(store):
    store.load({"other": "http://other.org/"}, replace=False)
    assert store.prefixes == {"ex": EX, "other": "http://other.org/"}


def test_load_prefix_collision_raises(store):
    with pytest.raises(PrefixConflictError) as exc_info:
        store.load({"ex": "http://different.org/"}, replace=False)
    assert exc_info.value.kind == "prefix_collision"


def test_load_url_collision_raises(store):
    with pytest.raises(PrefixConflictError) as exc_info:
        store.load({"other": EX}, replace=False)
    assert exc_info.value.kind == "url_collision"


def test_load_identical_pair_is_noop(store):
    store.load({"ex": EX}, replace=False)
    assert store.prefixes == {"ex": EX}


# --- compact_string / expand_string / drop_string ---------------------------


def test_compact_string_longest_match_wins():
    s = PrefixStore({"ex": EX, "exlong": EX + "long/"})
    assert s.compact_string(EX + "long/thing") == "exlong:thing"


def test_compact_string_no_match_returns_input(store):
    assert store.compact_string("http://unrelated.org/x") == "http://unrelated.org/x"


def test_expand_string_roundtrips_compact_string(store):
    assert store.expand_string("ex:foo") == EX + "foo"


def test_expand_string_unknown_prefix_returns_input(store):
    assert store.expand_string("unknown:foo") == "unknown:foo"


def test_expand_string_no_colon_returns_input(store):
    assert store.expand_string("nocolon") == "nocolon"


def test_drop_string_strips_known_prefix(store):
    assert store.drop_string(EX + "foo") == "foo"


def test_drop_string_unknown_returns_input(store):
    assert store.drop_string("http://unrelated.org/x") == "http://unrelated.org/x"


# --- apply_prefixes dispatcher + compact/expand/drop aliases ----------------


def test_apply_prefixes_rejects_unsupported_action(store):
    with pytest.raises(ValueError):
        store.apply_prefixes("x", "not-an-action")


def test_apply_prefixes_string_carrier(store):
    assert store.apply_prefixes(EX + "foo", "compact") == "ex:foo"


def test_apply_prefixes_non_string_carrier_passthrough(store):
    assert store.apply_prefixes(42, "compact") == 42


def test_compact_dict_and_list():
    s = PrefixStore({"ex": EX})
    assert s.compact({"a": EX + "foo", "b": [EX + "bar"]}) == {
        "a": "ex:foo",
        "b": ["ex:bar"],
    }


def test_compact_dataframe():
    s = PrefixStore({"ex": EX})
    df = pd.DataFrame({"col": [EX + "foo"]})
    assert s.compact(df)["col"].iloc[0] == "ex:foo"


def test_expand_dict_roundtrip(store):
    assert store.expand({"a": "ex:foo"}) == {"a": EX + "foo"}


def test_drop_dict_strips_prefixes(store):
    assert store.drop({"a": "ex:foo"}) == {"a": "foo"}


# --- fetch_prefix ------------------------------------------------------------


def test_fetch_prefix_known(store):
    assert store.fetch_prefix(EX + "foo") == "ex"


def test_fetch_prefix_unknown_returns_none(store):
    assert store.fetch_prefix("http://unrelated.org/x") is None


# --- bind_to_namespace / include_in_query -----------------------------------


def test_bind_to_namespace_registers_prefixes_on_graph(store):
    g = Graph()
    store.bind_to_namespace(g)
    assert dict(g.namespaces())["ex"] == URIRef(EX)


def test_include_in_query_prepends_missing_prefixes(store):
    query = "SELECT * WHERE { ?s ex:knows ?o }"
    augmented = store.include_in_query(query)
    assert "PREFIX ex: <http://example.org/>" in augmented


def test_include_in_query_skips_already_declared_prefixes(store):
    query = "PREFIX ex: <http://example.org/>\nSELECT * WHERE { ?s ex:knows ?o }"
    augmented = store.include_in_query(query)
    assert augmented.count("PREFIX ex:") == 1


# --- node_to_python / python_to_node -----------------------------------------


def test_node_to_python_uriref_compacts(store):
    assert store.node_to_python(URIRef(EX + "foo")) == "ex:foo"


def test_node_to_python_bnode_returns_n3_form(store):
    bnode = BNode("x")
    assert store.node_to_python(bnode) == bnode.n3()


def test_node_to_python_literal_returns_native_value(store):
    assert store.node_to_python(Literal(42)) == 42


def test_python_to_node_uriref_expands(store):
    assert store.python_to_node("ex:foo", URIRef) == URIRef(EX + "foo")


def test_python_to_node_uriref_rejects_non_string(store):
    with pytest.raises(TypeError):
        store.python_to_node(42, URIRef)


def test_python_to_node_bnode(store):
    assert store.python_to_node("_:x", BNode) == BNode("x")


def test_python_to_node_bnode_rejects_known_prefix(store):
    with pytest.raises(TypeError):
        store.python_to_node("ex:foo", BNode)


def test_python_to_node_literal(store):
    assert store.python_to_node(42, Literal) == Literal(42)


def test_python_to_node_rejects_unknown_class(store):
    with pytest.raises(TypeError):
        store.python_to_node("x", str)


# --- replace_prefix_in_store (Tier 3 — supported but unused elsewhere) ------


def test_replace_prefix_in_store_renames(store):
    store.replace_prefix_in_store("ex", "example")
    assert store.prefixes == {"example": EX}


def test_replace_prefix_in_store_missing_original_raises_keyerror(store):
    with pytest.raises(KeyError):
        store.replace_prefix_in_store("missing", "new")


def test_replace_prefix_in_store_conflicting_replacement_raises():
    s = PrefixStore({"ex": EX, "other": "http://other.org/"})
    with pytest.raises(PrefixConflictError):
        s.replace_prefix_in_store("ex", "other")


# --- dunder methods -----------------------------------------------------


def test_getitem_returns_url_for_prefix(store):
    assert store["ex"] == EX


def test_repr_contains_prefixes(store):
    assert "ex" in repr(store)
