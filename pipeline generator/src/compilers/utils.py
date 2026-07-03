"""Compiler-side utilities that encode knowledge of the semantic model.

Anything in here understands ``tcs:`` predicates and the shapes they
imply — as opposed to :mod:`rdfine.utils`, which stays strictly at
the graph-handling layer. Helpers are shared across compilers so
that any refinement to the compilation semantics happens in one
place.
"""

import textwrap
from copy import deepcopy

import yaml
from rdfine import GraphDict, GraphReader


def parse_config(input_dict: dict) -> dict:
    """
    Parses a dict containing a "tcs:literal" or "tcs:embedded"
    """

    dict_data = deepcopy(input_dict)

    if "tcs:literal" in dict_data:
        literal_value = dict_data["tcs:literal"]
        del dict_data["tcs:literal"]

        # Clean up trailing double_quotes
        literal_value = literal_value.removeprefix('""')
        literal_value = literal_value.removesuffix('""')

        # Removing '\\r' at the end of a line
        lines = literal_value.splitlines()
        lines = [line.removesuffix("\\r") for line in lines]
        literal_value = "\n".join(lines)
        literal_value = textwrap.dedent(literal_value).strip()

        dict_data[":config"] = yaml.safe_load(literal_value)
    elif "tcs:embedded" in dict_data:
        embedded_value = dict_data["tcs:embedded"]
        del dict_data["tcs:embedded"]
        dict_data[":config"] = embedded_value
    else:
        raise LookupError(
            "Neither predicates 'tcs:embedded' nor 'tcs:literal' found in data."
        )

    return dict_data


def extract_config(reader: GraphReader, config_id: str) -> dict:
    """Return the parsed body of a ``tcs:Config`` node as a plain dict.

    Combines the ``traverse`` + ``frame`` + :func:`parse_config` +
    ``[":config"]`` dance that every compiler runs when it reads a
    ``tcs:Config``-typed node. The returned dict is the user-facing
    config body (``:config`` sub-key of the parsed structure); wrap
    it in a :class:`GraphDict` if path queries or further path-based
    edits are needed.

    Args:
        reader: A :class:`GraphReader` over a graph that contains the
            config node and everything it traverses to.
        config_id: The IRI of the ``tcs:Config`` to extract (compact
            form, e.g. ``":ldio_config_0"``).
    """
    config_gd = GraphDict(reader.traverse(config_id).graph)
    config_gd = config_gd.frame({"@id": config_id})
    return parse_config(config_gd.dict)[":config"]


def parse_docker_compose_config(
    reader: GraphReader,
    config_id: str,
) -> dict:
    """Parse a ``tcs:DockerComposeConfig`` into a normalized compose-shaped dict.

    Compilers that read or edit a ``tcs:DockerComposeConfig`` face
    the same problem: the underlying config can be shaped as a bare
    service body, a ``<name>: {body}`` map, or an already-wrapped
    ``services: {<name>: {body}}`` document (optionally with sibling
    top-level keys like ``volumes:`` or ``networks:``).

    This function normalizes every one of those shapes into the
    canonical Compose-file layout::

        {
            "services": {<name>: <body>, ...},
            # optional siblings preserved from the source config:
            "volumes":  {...},
            "networks": {...},
            ...
        }

    which is what the Docker Compose Specification (Compose v2+)
    requires and what lets downstream compilers merge multiple
    configs by top-level key without special-casing.

    Shape detection uses the ``image`` field as a landmark. Three
    canonical source shapes are supported (illustrated with yaml for
    readability):

    Case A — bare service body::

        image: nginx
        ports: ["80:80"]

    Normalizes to ``{"services": {<config_local_name>: {image, ports}}}``.

    Case B — named service at the root::

        webapp:
          image: nginx

    Normalizes to ``{"services": {"webapp": {image}}}``.

    Case C — fully wrapped, optionally with sibling top-level keys::

        services:
          webapp:
            image: nginx
        volumes:
          data: {}

    Normalizes to ``{"services": {"webapp": {image}}, "volumes": {"data": {}}}``.

    Multiple ``image`` occurrences in one config are supported — each
    contributes its own entry under ``services:``.

    Args:
        reader: A :class:`GraphReader` over a graph that contains the
            config node and everything it traverses to.
        config_id: The IRI of the ``tcs:DockerComposeConfig`` to
            parse (compact form, e.g. ``":dc_config_0"``).

    Raises:
        LookupError: The config graph contains no ``image`` field, so
            the service body cannot be located.
    """

    raw = extract_config(reader, config_id)

    # Re-wrap so we can use path queries to locate ``image``.
    parsed = GraphDict(raw, prefix_store=reader.prefix_store)

    image_paths = parsed.find(path_pattern="image$")["path"].to_list()
    if not image_paths:
        raise LookupError(
            f"tcs:DockerComposeConfig {config_id!s} has no ``image`` key; "
            "cannot determine the service body level."
        )

    normalized: dict = {"services": {}}
    service_root_segments: set[str] = set()
    bare_body_present = False

    for image_path in image_paths:
        segments = image_path.split(".")
        if len(segments) == 1:
            # Case A: bare body. The whole raw dict is the service
            # body; fall back to the config's local name (segment
            # after the last ``:``) as the service key.
            name = config_id.rsplit(":", 1)[-1]
            body = raw
            bare_body_present = True
        else:
            # Case B / C (and deeper): the service body is one level
            # above ``image``; the segment that owns the body is the
            # service name.
            body_path = ".".join(segments[:-1])
            name = segments[-2]
            body = parsed.get(body_path).dict
            service_root_segments.add(segments[0])

        normalized["services"][name] = body

    # Preserve any top-level siblings that no service claimed
    # (typical: ``volumes``, ``networks``, ``configs``, ``secrets``).
    # A bare-body case has no sensible siblings — the root *is* the
    # body — so nothing extra is copied over.
    if not bare_body_present:
        for key, val in raw.items():
            if key in service_root_segments:
                continue
            normalized[key] = val

    return normalized
