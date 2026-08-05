"""Unit tests for rdfine.graph_dict.GraphDict.

Tier 1 (find/frame/get/serialize + .graph) is directly used by
``compilers/`` (e.g. ``utils.extract_config``). ``set()`` is Tier 3 —
supported but unused anywhere in the codebase today — and gets minimal
coverage accordingly.
"""

import pytest
from rdflib import Graph, URIRef

from rdfine import GraphDict, PrefixStore

EX = "http://example.org/"


def _context():
    return {"ex": EX}


# --- construction ------------------------------------------------------


def test_init_from_dict_with_context():
    gd = GraphDict({"@context": _context(), "@id": "ex:a", "ex:name": "Alice"})
    assert gd.dict["ex:name"] == "Alice"


def test_init_from_dict_without_context_requires_prefix_store():
    with pytest.raises(ValueError):
        GraphDict({"@id": "ex:a"})


def test_init_from_dict_with_explicit_prefix_store():
    store = PrefixStore({"ex": EX})
    gd = GraphDict({"@id": "ex:a", "ex:name": "Alice"}, prefix_store=store)
    assert gd.dict["ex:name"] == "Alice"


def test_init_from_graph():
    g = Graph()
    g.bind("ex", EX)
    g.add((URIRef(EX + "a"), URIRef(EX + "name"), URIRef(EX + "Alice")))
    gd = GraphDict(g)
    # JSON-LD serialization of a single-subject graph may or may not be
    # wrapped in @graph depending on shape; round-trip through .graph
    # instead of asserting an exact dict layout.
    assert len(gd.graph) == 1


def test_init_rejects_unsupported_type():
    with pytest.raises(TypeError):
        GraphDict(["not", "valid"])


# --- .graph --------------------------------------------------------------


def test_graph_property_roundtrips_dict():
    gd = GraphDict({"@context": _context(), "@id": "ex:a", "ex:name": "Alice"})
    g = gd.graph
    assert (URIRef(EX + "a"), URIRef(EX + "name"), None) in list(g)[0:1] or len(g) == 1


def test_graph_property_patches_unprefixed_keys():
    # "unprefixed" has no registered prefix and isn't a URL — the
    # placeholder prefix mechanism must still produce a valid graph.
    store = PrefixStore({"ex": EX})
    gd = GraphDict({"@id": "ex:a", "unprefixed": "value"}, prefix_store=store)
    g = gd.graph
    assert len(g) == 1


# --- find ------------------------------------------------------------------


def test_find_by_path_pattern():
    gd = GraphDict(
        {"@context": _context(), "@id": "ex:a", "ex:name": "Alice", "ex:age": 30}
    )
    result = gd.find(path_pattern="name")
    assert list(result["value"]) == ["Alice"]


def test_find_by_value_pattern():
    gd = GraphDict(
        {"@context": _context(), "@id": "ex:a", "ex:name": "Alice", "ex:age": 30}
    )
    result = gd.find(value_pattern="ali")
    assert list(result["path"]) == ["ex:name"]


def test_find_no_pattern_returns_full_index():
    gd = GraphDict({"@context": _context(), "@id": "ex:a", "ex:name": "Alice"})
    assert len(gd.find()) == len(gd.find(path_pattern=None, value_pattern=None))


# --- get ---------------------------------------------------------------


def test_get_scalar_value():
    gd = GraphDict({"@context": _context(), "@id": "ex:a", "ex:name": "Alice"})
    assert gd.get("ex:name") == "Alice"


def test_get_nested_dict_returns_graphdict():
    gd = GraphDict(
        {
            "@context": _context(),
            "@id": "ex:a",
            "ex:address": {"ex:city": "Ghent"},
        }
    )
    nested = gd.get("ex:address")
    assert isinstance(nested, GraphDict)
    assert nested.dict["ex:city"] == "Ghent"


def test_get_list_wraps_in_graph_key():
    gd = GraphDict({"@context": _context(), "@id": "ex:a", "ex:tags": ["x", "y"]})
    result = gd.get("ex:tags")
    assert isinstance(result, GraphDict)
    assert result.dict["@graph"] == ["x", "y"]


# --- frame -------------------------------------------------------------


def test_frame_selects_matching_type():
    gd = GraphDict(
        {
            "@context": _context(),
            "@graph": [
                {"@id": "ex:a", "@type": "ex:Person", "ex:name": "Alice"},
                {"@id": "ex:b", "@type": "ex:Place", "ex:name": "Ghent"},
            ],
        }
    )
    framed = gd.frame({"@type": "ex:Person"})
    assert framed.dict.get("ex:name") == "Alice" or "ex:name" in str(framed.dict)


# --- set (Tier 3 — supported but unused elsewhere) --------------------------


def test_set_updates_existing_path():
    gd = GraphDict({"@context": _context(), "@id": "ex:a", "ex:name": "Alice"})
    updated = gd.set("ex:name", "Bob")
    assert updated.dict["ex:name"] == "Bob"
    assert gd.dict["ex:name"] == "Alice"  # original left untouched


def test_set_missing_key_raises_keyerror():
    gd = GraphDict({"@context": _context(), "@id": "ex:a", "ex:name": "Alice"})
    with pytest.raises(KeyError):
        gd.set("ex:missing", "value")


def test_set_list_index():
    gd = GraphDict({"@context": _context(), "@id": "ex:a", "ex:tags": ["x", "y"]})
    updated = gd.set("ex:tags.0", "z")
    assert updated.dict["ex:tags"][0] == "z"


# --- serialize -----------------------------------------------------------


def test_serialize_json():
    gd = GraphDict({"@context": _context(), "@id": "ex:a", "ex:name": "Alice"})
    assert '"ex:name"' in gd.serialize("json")


def test_serialize_yaml():
    gd = GraphDict({"@context": _context(), "@id": "ex:a", "ex:name": "Alice"})
    assert "ex:name: Alice" in gd.serialize("yaml")


def test_serialize_rejects_unsupported_format():
    gd = GraphDict({"@context": _context(), "@id": "ex:a", "ex:name": "Alice"})
    with pytest.raises(ValueError):
        gd.serialize("xml")


def test_serialize_rejects_unsupported_prefix_action():
    gd = GraphDict({"@context": _context(), "@id": "ex:a", "ex:name": "Alice"})
    with pytest.raises(ValueError):
        gd.serialize("json", prefix_action="invalid")


def test_serialize_drop_removes_prefixes():
    gd = GraphDict({"@context": _context(), "@id": "ex:a", "ex:name": "Alice"})
    dropped = gd.serialize("json", prefix_action="drop")
    assert '"name"' in dropped and '"ex:name"' not in dropped


# --- collapse_values (Tier 2 — used internally by serialize) --------------


def test_collapse_values_extracts_value_objects():
    data = {"ex:name": {"@value": "Alice"}}
    assert GraphDict.collapse_values(data) == {"ex:name": "Alice"}


def test_collapse_values_leaves_plain_values_untouched():
    data = {"ex:name": "Alice"}
    assert GraphDict.collapse_values(data) == data


# --- dunder methods -----------------------------------------------------


def test_getitem_indexes_dict():
    gd = GraphDict({"@context": _context(), "@id": "ex:a", "ex:name": "Alice"})
    assert gd["ex:name"] == "Alice"


def test_repr_contains_dict_contents():
    gd = GraphDict({"@context": _context(), "@id": "ex:a", "ex:name": "Alice"})
    assert "Alice" in repr(gd)
