"""Shared fixtures for rdfine's own unit tests."""

import pytest
from rdflib import Graph, Literal, URIRef

from rdfine import PrefixStore

EX = "http://example.org/"


@pytest.fixture
def store() -> PrefixStore:
    return PrefixStore({"ex": EX})


@pytest.fixture
def sample_graph() -> Graph:
    g = Graph()
    g.bind("ex", EX)
    g.add((URIRef(EX + "a"), URIRef(EX + "knows"), URIRef(EX + "b")))
    g.add((URIRef(EX + "b"), URIRef(EX + "knows"), URIRef(EX + "c")))
    g.add((URIRef(EX + "a"), URIRef(EX + "name"), Literal("Alice")))
    return g
