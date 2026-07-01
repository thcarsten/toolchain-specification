from .prefix_store import PrefixStore
from rdflib import Graph
from boltons.iterutils import remap
from copy import deepcopy  # For deep copies
from functools import cached_property
from glom import glom, Path
from validators import url as is_url
import json
from pyld import jsonld
import yaml
import pandas as pd
import re
from typing import Self


class GraphDict:
    """
    A GraphDict that represents a JsonLd. It is always a dict bc even lists are embedded in a dict (via @graph)

    Properties:
            - dict
            - prefix_store
            - graph (Graph view of the GraphDict)
            - _basepath
            - _placeholder_prefix
    Public methods:
            - find
            - frame
            - get
            - serialize
            - set
    Private methods:
            - _provide_prefixes
            - _create_index
            - _to_glom_path

    Path arguments accepted by :meth:`get` and :meth:`set` may be a
    ``glom.Path``, a list/tuple of segments, or a dot-separated string. Numeric
    string segments (e.g. ``"0"``) are auto-converted to ``int`` so they index
    into lists.
    """

    ###########################
    # Class variables
    ###########################

    _basepath = "file:///workspace/pipeline/"
    _placeholder_prefix = {"na_": "https://no_prefix_available.com/"}

    ###########################
    # Constructors
    ###########################

    def __init__(
        self,
        data: dict | Graph,
        prefix_store: PrefixStore | None = None,
    ) -> None:

        # Normalize ``data`` to a JSON-LD shaped dict and pick a default
        # prefix_store source.
        if isinstance(data, Graph):
            if prefix_store is None:
                prefix_store = PrefixStore(data)
            input_dict = json.loads(
                data.serialize(format="json-ld", base=self._basepath)
            )
            if isinstance(input_dict, list):
                input_dict = {"@graph": input_dict}
        elif isinstance(data, dict):
            input_dict = data
        else:
            raise TypeError(
                "GraphDict expects a dict or rdflib.Graph; got "
                f"{type(data).__name__}."
            )

        # Assigning a prefix store
        if prefix_store is not None:
            self.prefix_store = prefix_store
        elif "@context" in input_dict:
            self.prefix_store = PrefixStore(input_dict["@context"])
        else:
            raise ValueError(
                "To construct a GraphDict from a dict, either a PrefixStore or @context is required"
            )

        # Strip @context with a shallow copy (we don't want to mutate the
        # caller's dict). ``compact`` rebuilds the structure recursively, so
        # no deep copy is required here.
        working_dict = {k: v for k, v in input_dict.items() if k != "@context"}
        self.dict = self.prefix_store.compact(working_dict)
        # Note: any keys without a registered prefix are patched with the
        # placeholder prefix (``na_:``) inside :meth:`_provide_prefixes`,
        # which is invoked only when ``.graph`` is accessed. The dict view
        # itself stays unaltered and may freely contain bare keys.

    ###########################
    # Public
    ###########################

    @property
    def graph(self) -> Graph:

        # Build graph-ready local copies: any unprefixed keys get the
        # placeholder prefix prepended, and the placeholder is registered on
        # a fresh PrefixStore so rdflib can expand the resulting IRIs.
        # ``self.dict`` and ``self.prefix_store`` are not touched.
        safe_dict, safe_store = self._provide_prefixes()

        output_graph = Graph()

        # ``expand`` rebuilds the structure recursively, so we can pass it
        # ``safe_dict`` directly without copying.
        jsonld_dict = safe_store.expand(safe_dict)
        jsonld_string = json.dumps(jsonld_dict)
        output_graph.parse(data=jsonld_string, format="json-ld")
        safe_store.bind_to_namespace(output_graph)

        return output_graph

    def find(self, path_pattern=None, value_pattern=None):
        """
        Filter a DataFrame with columns ['path', 'value'] using regex.
        Always uses AND logic and is case-insensitive.

        Args:
        - path_pattern: regex applied to 'path' (optional)
        - value_pattern: regex applied to 'value' (optional)

        Returns:
        - Filtered DataFrame
        """

        df = self._index

        if path_pattern is None and value_pattern is None:
            return df.copy()

        mask = pd.Series(True, index=df.index)

        if path_pattern is not None:
            mask &= df["path"].str.contains(
                path_pattern, case=False, regex=True, na=False
            )

        if value_pattern is not None:
            mask &= (
                df["value"]
                .astype(str)
                .str.contains(value_pattern, case=False, regex=True, na=False)
            )

        output_df = df[mask].copy()
        output_df = output_df.reset_index(drop=True)
        return output_df

    def frame(self, jsonld_frame: dict) -> Self:
        """
        Applies a json-ld frame.
        """

        # if "@context" not in jsonld_frame.keys():
        #    jsonld_frame["@context"] = dict(self.prefix_store.prefixes)

        jsonld_frame_expanded = self.prefix_store.expand(jsonld_frame)

        # Serialize RDF graph as JSON-LD
        json_expanded = self.prefix_store.expand(self.dict)
        # Apply the frame
        json_framed = jsonld.frame(json_expanded, jsonld_frame_expanded)
        # Compact the dictionary
        json_compacted = self.prefix_store.compact(json_framed)

        return type(self)(json_compacted, self.prefix_store)

    def get(self, path):
        """
        Get allows to extract a value via a path. Returns a new GraphDict if appropriate.

        See the class docstring for accepted path types.
        """
        output_obj = glom(self.dict, self._to_glom_path(path))
        # Wrapping list in dict
        if isinstance(output_obj, list):
            output_obj = {"@graph": output_obj}
        # dicts are returned as GraphDicts, otherwise return object itself
        if isinstance(output_obj, dict):
            return type(self)(output_obj, self.prefix_store)
        else:
            return output_obj

    def serialize(
        self, output_format: str, prefix_action: str | None = "compact"
    ) -> str:
        """
        Serialize the data to a string in the requested format.

        formats:
            - json
            - yaml
        prefixes:
            - expand
            - compact
            - drop
        """

        # Inventory of the function arguments
        supported_formats = ["json", "yaml", "yml"]
        supported_prefix_actions = ["expand", "compact", "drop"]

        # Checking for proper function arguments
        if output_format not in supported_formats:
            raise ValueError(f"format not in {supported_formats}")
        elif (
            prefix_action is not None and prefix_action not in supported_prefix_actions
        ):
            raise ValueError(f"prefixes not in {supported_prefix_actions}")

        dict_data = self.dict
        dict_data = self.prefix_store.apply_prefixes(dict_data, prefix_action)
        dict_data = self.collapse_values(dict_data)

        match output_format:
            case "json":
                return json.dumps(dict_data)
            case "yaml" | "yml":
                return yaml.dump(
                    dict_data,
                    Dumper=_PrettyDumper,
                    default_flow_style=False,
                    sort_keys=False,
                )

    def set(self, path, new_value, sep: str = ".") -> Self:
        """
        Strict insert of a value at an existing path.

        Parameters:
        - path: a ``glom.Path``, list/tuple of segments, or dot-separated string
          (see the class docstring for the accepted forms).
        - new_value: the value that should be set at ``path``.
        - sep: path separator used when ``path`` is a string (default ``"."``).

        Rules:
        - The path must already exist; missing keys/indices are never created.
        - Integer segments (or numeric string segments) index into lists.
        - String segments index into dicts.
        """

        data = deepcopy(self.dict)
        segments = self._to_glom_path(path, sep=sep).values()

        if not segments:
            raise ValueError("Path must contain at least one segment.")

        current = data

        # Traverse to parent of target
        for seg in segments[:-1]:
            if isinstance(seg, int):
                if not isinstance(current, list):
                    raise TypeError(
                        f"Expected list at '{seg}', got {type(current).__name__}"
                    )
                if seg >= len(current):
                    raise IndexError(f"Index {seg} out of range")
                current = current[seg]
            else:
                if not isinstance(current, dict):
                    raise TypeError(
                        f"Expected dict at '{seg}', got {type(current).__name__}"
                    )
                if seg not in current:
                    raise KeyError(f"Key '{seg}' not found")
                current = current[seg]

        last = segments[-1]

        if isinstance(last, int):
            if not isinstance(current, list):
                raise TypeError(
                    f"Expected list at final segment, got {type(current).__name__}"
                )
            if last >= len(current):
                raise IndexError(f"Index {last} out of range")
            current[last] = new_value
        else:
            if not isinstance(current, dict):
                raise TypeError(
                    f"Expected dict at final segment, got {type(current).__name__}"
                )
            if last not in current:
                raise KeyError(f"Key '{last}' not found")
            current[last] = new_value

        return type(self)(data, self.prefix_store)

    ###########################
    # Private
    ###########################

    @cached_property
    def _index(self) -> pd.DataFrame:
        """
        Cached flattened path / value index of ``self.dict``. Built lazily on
        first access and reused on subsequent ``find`` calls.
        """
        return self._create_index()

    def _create_index(self, sep=".") -> pd.DataFrame:
        """
        Flattens a deeply nested structure (dicts + lists) into a DataFrame
        with columns: 'path' and 'value'.

        - Dict keys are used as path segments
        - List indices are used as numeric path segments
        - Only leaf values (non-dict, non-list) are returned
        """

        rows = []

        def walk(node, path):
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, path + [str(k)])
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    walk(v, path + [str(i)])
            else:
                # Leaf node
                rows.append({"path": sep.join(path), "value": node})

        walk(self.dict, [])
        return pd.DataFrame(rows)

    def _provide_prefixes(self) -> tuple[dict, PrefixStore]:
        """
        Build a graph-ready view of ``self.dict``.

        - Any string key that is not a JSON-LD reserved term (``@…``), not
          already a fully-qualified URL, and whose prefix is not registered
          in ``self.prefix_store`` gets the placeholder prefix
          (``na_:``) prepended.
        - A fresh :class:`PrefixStore`, seeded from ``self.prefix_store`` and
          extended with the placeholder mapping, is returned alongside the
          patched dict so rdflib can expand the placeholder IRIs.

        The function is pure: neither ``self.dict`` nor ``self.prefix_store``
        is modified. Called from :attr:`graph` only — the in-memory dict
        view remains permissive about unprefixed keys.
        """

        placeholder_prefix = next(iter(self._placeholder_prefix))

        # Local PrefixStore extended with the placeholder. ``load`` is a
        # no-op if ``na_`` is already registered with the same URL, and
        # raises ``PrefixConflictError`` only if a different URL was
        # previously bound to that prefix — in which case the user has
        # deliberately shadowed the placeholder and the error is correct.
        safe_store = PrefixStore(dict(self.prefix_store.prefixes))
        safe_store.load(self._placeholder_prefix, replace=False)

        def visit(path, key, value):
            if (
                isinstance(key, str)
                and not key.startswith("@")
                and not self.prefix_store.fetch_prefix(key)
                and not is_url(key)
            ):
                return placeholder_prefix + ":" + key, value
            return key, value

        safe_dict = remap(self.dict, visit=visit)
        return safe_dict, safe_store

    @staticmethod
    def _to_glom_path(path, sep: str = ".") -> Path:
        """
        Coerce a path argument into a ``glom.Path``.

        - ``glom.Path``     → returned as-is.
        - ``list`` / ``tuple`` → wrapped in a ``Path``.
        - ``str``           → split on ``sep``; numeric segments become ``int``
          so they index into lists (matching the convention used by
          :meth:`GraphDict._create_index`).
        """
        if isinstance(path, Path):
            return path
        if isinstance(path, (list, tuple)):
            segments = list(path)
        elif isinstance(path, str):
            segments = path.split(sep)
        else:
            return Path(path)

        converted = [
            int(seg) if isinstance(seg, str) and seg.isdigit() else seg
            for seg in segments
        ]
        return Path(*converted)

    @staticmethod
    def collapse_values(obj):
        """
        Recursively traverse nested dicts/lists and replace any JSON-LD value object
        (i.e. a dict containing '@value') with its native value.
        """

        def visit(path, key, value):
            if isinstance(value, dict) and "@value" in value:
                return key, value["@value"]

            return True  # continue traversal

        return remap(obj, visit=visit)

    ###########################
    # DUNDER METHODS
    ###########################

    # String shown upon print
    def __repr__(self):
        return f"GraphDict(\n{str(self.dict)})"

    # Indexing graph_dict returns indexed self.dict
    def __getitem__(self, key):
        return self.dict[key]


# A private YAML Dumper that prints multi-line strings using block style ('|').
# Scoped to a subclass so importing this module does not mutate the global
# yaml representer registry for `str`.
class _PrettyDumper(yaml.SafeDumper):
    pass


def _pretty_multiline(dumper, data):
    if "\n" in data:  # detect multiline
        data = data.replace("\r\n", "\n")
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_PrettyDumper.add_representer(str, _pretty_multiline)
