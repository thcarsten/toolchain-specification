"""Compiler-side utilities that encode knowledge of the semantic model.

Anything in here understands ``tcs:`` predicates and the shapes they
imply — as opposed to :mod:`rdfine.utils`, which stays strictly at
the graph-handling layer. Helpers are shared across compilers so
that any refinement to the compilation semantics happens in one
place.
"""

import json
import re
import textwrap
from copy import deepcopy
from typing import Union

import pandas as pd
import yaml
from rdflib import Literal, URIRef
from rdfine import GraphDict, GraphReader, receive_first

# Recognised values for ``dct:format`` on a ``tcs:literal`` config.
# The compiler dispatches strictly on these strings — no fallback —
# so adding a new format requires editing this table and the
# dispatch in :func:`parse_config`. That is deliberate: keeping the
# set of supported formats visible in one place makes the semantic
# contract with the catalog explicit.
_YAML_FORMATS = frozenset({"text/yaml", "application/yaml", "application/json"})
_RAW_TEXT_FORMATS = frozenset({"text/plain", "text/x-dockerfile"})


def parse_config(input_dict: dict) -> dict:
    """Parse a framed ``tcs:Config`` dict.

    The input is the dict produced by ``GraphReader.traverse`` +
    ``GraphDict.frame`` for a config node. Two payload shapes exist
    in the semantic model:

    - ``tcs:literal`` — an opaque string body. Its ``dct:format``
      picks the parser (yaml/json families are ``yaml.safe_load``-ed;
      ``text/plain`` and ``text/x-dockerfile`` are returned as the
      cleaned string). ``dct:format`` is *required* here: without it
      the compiler cannot know how to interpret the string.
    - ``tcs:embedded`` — an already-structured RDF fragment. The
      semantic model guarantees this to be RDF, so no ``dct:format``
      is needed (and any that is present is ignored).

    A missing or unsupported format on a ``tcs:literal`` raises so
    the modelling error surfaces at compile time rather than as a
    downstream parse failure.

    The parsed body lands under the ``:config`` key of the returned
    dict, side by side with the other framed predicates.
    """

    dict_data = deepcopy(input_dict)
    # ``dct:format`` is meaningful only for tcs:literal payloads;
    # drop it up front so downstream branches don't have to bother.
    fmt = dict_data.pop("dct:format", None)

    if "tcs:literal" in dict_data:
        if fmt is None:
            raise LookupError(
                "tcs:literal config is missing required predicate 'dct:format'. "
                "Declare the payload format explicitly "
                "(e.g. 'text/yaml', 'text/x-dockerfile')."
            )
        literal_value = _clean_literal(dict_data.pop("tcs:literal"))
        if fmt in _YAML_FORMATS:
            dict_data[":config"] = yaml.safe_load(literal_value)
        elif fmt in _RAW_TEXT_FORMATS:
            dict_data[":config"] = literal_value
        else:
            supported = sorted(_YAML_FORMATS | _RAW_TEXT_FORMATS)
            raise ValueError(
                f"Unsupported dct:format {fmt!r} for tcs:literal payload; "
                f"expected one of {supported}."
            )
    elif "tcs:embedded" in dict_data:
        embedded_value = dict_data.pop("tcs:embedded")
        dict_data[":config"] = _normalize_newlines(embedded_value)
    else:
        raise LookupError(
            "Neither predicates 'tcs:embedded' nor 'tcs:literal' found in data."
        )

    return dict_data


def _clean_literal(text: str) -> str:
    """Normalise a ``tcs:literal`` string body.

    Strips leading/trailing double-quote artefacts, converts
    CRLF / lone CR line endings (Windows / classic-Mac) to LF so
    downstream serializers do not emit ``\\r`` escape sequences,
    drops the ``\\r`` line-terminator escape that Turtle sometimes
    leaves on a line, dedents, and trims outer whitespace so
    downstream parsers (yaml, raw-text consumers) get a clean
    payload regardless of how the literal was written in the
    Turtle source.
    """
    text = text.removeprefix('""').removesuffix('""')
    # CRLF-saved Turtle sources leave real \r bytes in the literal;
    # yaml / json serializers escape those as ``\r`` which is ugly
    # and Docker-Compose-parsing-hostile. Normalise to LF first.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.removesuffix("\\r") for line in text.splitlines()]
    text = "\n".join(lines)
    return textwrap.dedent(text).strip()


def _normalize_newlines(value):
    """Recursively normalise CR/CRLF line endings inside a parsed value.

    ``tcs:embedded`` payloads reach :func:`parse_config` as already
    structured RDF (dicts / lists of primitives). Their string
    leaves can still carry ``\\r`` from CRLF-saved Turtle sources.
    yaml.dump / json.dumps escape those as ``\\r`` in the output;
    normalising them here keeps the generated files clean.
    """
    if isinstance(value, str):
        return value.replace("\r\n", "\n").replace("\r", "\n")
    if isinstance(value, dict):
        return {k: _normalize_newlines(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_newlines(v) for v in value]
    return value


def read_literal(reader: GraphReader, config_id: str) -> str:
    """Return the raw ``tcs:literal`` body of a ``tcs:Config`` node.

    Bypasses :func:`extract_config`'s ``dct:format``-based dispatch
    — useful when a compiler wants the bytes verbatim (e.g. to write
    a JSON file whose original whitespace / formatting matters, or
    for any config that does not need parsing).

    Callers get a plain ``str``. If the config has no ``tcs:literal``
    (only ``tcs:embedded``, or missing entirely), :func:`receive_first`
    raises.
    """
    return str(
        receive_first(reader.filter(sub=config_id, pred="tcs:literal").df["obj"])
    )


def extract_config(reader: GraphReader, config_id: str) -> Union[dict, str]:
    """Return the parsed body of a ``tcs:Config`` node.

    Combines the ``traverse`` + ``frame`` + :func:`parse_config` +
    ``[':config']`` dance that every compiler runs when it reads a
    ``tcs:Config``. The returned value is whatever ``parse_config``
    produces for the config's ``dct:format``:

    - yaml/json formats — a ``dict``.
    - raw-text formats  — a ``str``.
    - ``application/rdf`` embedded — the framed RDF fragment as a
      ``dict``.

    Callers know what shape to expect based on the config type they
    asked for; a Dockerfile config yields a string, a compose config
    yields a dict.

    Args:
        reader: A :class:`GraphReader` over a graph that contains the
            config node and everything it traverses to.
        config_id: The IRI of the ``tcs:Config`` to extract (compact
            form, e.g. ``':ldio_config_0'``).
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


def attach_file(
    reader: GraphReader,
    *,
    filename: str,
    filepath: str,
    content: str,
) -> GraphReader:
    """Return ``reader`` extended with an ``spdx:File`` attached to the build.

    Adds the triples::

        :build tcs:compiledFile :file_<slug>
        :file_<slug> a spdx:File ;
            tcs:filename "<filename>" ;
            tcs:filepath "<filepath>" ;
            tcs:literal  "<content>" .

    The file IRI is derived from ``filepath`` + ``filename`` to be
    stable within the build. ``content`` is stored verbatim as an
    rdflib ``Literal`` — no prefix expansion is applied to it, so
    arbitrary string bodies (yaml / ttl / json) are safe.

    The input ``reader`` is not mutated; callers typically write::

        self.output_reader = attach_file(self.output_reader, ...)
    """
    build_id = receive_first(
        reader.filter(pred="rdf:type", obj="tcs:PipelineBuild").df["sub"],
    )

    # Stable, IRI-safe local name derived from path + filename.
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", f"{filepath}_{filename}").strip("_")
    file_id = f":file_{slug}"

    # Guard against two compilers (or a re-run) targeting the same output
    # path — without this, a second call would silently add a second
    # tcs:literal value on the same file node, and downstream readers
    # picking one via ``receive_first`` would do so arbitrarily.
    if reader.ask(f"{file_id} tcs:literal ?existing ."):
        raise ValueError(
            f"spdx:File {file_id!s} ({filepath}/{filename}) already has a "
            "tcs:literal body — refusing to silently overwrite it with a "
            "second compiler's output."
        )

    rows = [
        {
            "sub": build_id,
            "pred": "tcs:compiledFile",
            "obj": file_id,
            "sub_type": URIRef,
            "obj_type": URIRef,
        },
        {
            "sub": file_id,
            "pred": "rdf:type",
            "obj": "spdx:File",
            "sub_type": URIRef,
            "obj_type": URIRef,
        },
        {
            "sub": file_id,
            "pred": "tcs:filename",
            "obj": filename,
            "sub_type": URIRef,
            "obj_type": Literal,
        },
        {
            "sub": file_id,
            "pred": "tcs:filepath",
            "obj": filepath,
            "sub_type": URIRef,
            "obj_type": Literal,
        },
        {
            "sub": file_id,
            "pred": "tcs:literal",
            "obj": content,
            "sub_type": URIRef,
            "obj_type": Literal,
        },
    ]

    new_graph = GraphReader(
        pd.DataFrame.from_records(rows),
        prefix_store=reader.prefix_store,
    ).graph
    return reader.add(new_graph)


def rewrite_compose_volume_host_path(
    reader: GraphReader,
    *,
    component_iri: str,
    container_path: str,
    host_path: str,
) -> GraphReader:
    """Rewrite the host-side of a volume mount on a component's compose config.

    Locates the ``tcs:DockerComposeConfig`` attached to ``component_iri``
    via ``tcs:config``, walks every service body's ``volumes:`` list,
    and rewrites any short-form volume string whose container-side
    path equals ``container_path`` so its host-side path becomes
    ``host_path``. The rest of the volume string (mode flag, if
    present) is preserved.

    This is the "each framework compiler owns its own compose
    fragment" pattern: a file-producing compiler that knows *where*
    it emits its output uses this to keep the paired compose config
    pointing at the right host location, so
    :class:`DockerComposeCompiler` can stay a generic aggregator.

    Only Compose's short (string) volume form is handled; the long
    (dict) form is left untouched \u2014 none of the current catalog uses
    it and silently rewriting an unfamiliar shape would be worse
    than a no-op.

    The rewrite follows the same idiom as
    :class:`SemanticWorksEnvVarCompiler`: strip the existing
    ``tcs:literal`` / ``tcs:embedded`` triples off the config node
    and re-attach a fresh ``tcs:literal`` carrying the normalised
    JSON body. :func:`parse_docker_compose_config` re-parses that
    shape without special-casing.

    The input ``reader`` is not mutated. Returns the reader unchanged
    if:

    - ``component_iri`` has no ``tcs:DockerComposeConfig`` attached,
    - the config has no ``services`` section, or
    - no volume in the config references ``container_path``.

    Args:
        reader: A :class:`GraphReader` over a graph that contains
            the component and its compose config.
        component_iri: Compact IRI of the component whose compose
            config carries the volume mount (e.g.
            ``\"rdfc:Orchestrator\"``).
        container_path: The container-side mount point to match on
            (stable framework convention, e.g.
            ``\"/workspace/pipeline/pipeline.ttl\"``).
        host_path: The host-side path to rewrite matching volumes
            to (e.g. ``\"./rdfc/pipeline.ttl\"``).
    """
    compose_ids = reader.select(
        "?compose_config",
        f"""
            {component_iri} tcs:config ?compose_config .
            ?compose_config a tcs:DockerComposeConfig .
        """,
    )["compose_config"].to_list()
    if not compose_ids:
        return reader
    compose_config_id = compose_ids[0]

    normalized = parse_docker_compose_config(reader, compose_config_id)
    if not normalized.get("services"):
        return reader

    changed = False
    for service_body in normalized["services"].values():
        volumes = service_body.get("volumes")
        if not isinstance(volumes, list):
            continue
        new_volumes: list = []
        for volume in volumes:
            if isinstance(volume, str):
                parts = volume.split(":", 2)
                # ``HOST:CONTAINER[:MODE]`` \u2014 only rewrite when the
                # container path matches the requested mount point.
                if len(parts) >= 2 and parts[1] == container_path:
                    new_volumes.append(":".join([host_path, *parts[1:]]))
                    changed = True
                    continue
            new_volumes.append(volume)
        service_body["volumes"] = new_volumes

    if not changed:
        return reader

    new_body = json.dumps(reader.prefix_store.drop(normalized))

    remove_triples = reader.filter(
        sub=compose_config_id, pred=["tcs:literal", "tcs:embedded"]
    ).graph
    updated = reader.remove(remove_triples)

    add_triples = GraphReader(
        pd.DataFrame.from_records(
            [
                {
                    "sub": compose_config_id,
                    "pred": "tcs:literal",
                    "obj": new_body,
                    "sub_type": URIRef,
                    "obj_type": Literal,
                }
            ]
        ),
        updated.prefix_store,
    ).graph
    return updated.add(add_triples)
