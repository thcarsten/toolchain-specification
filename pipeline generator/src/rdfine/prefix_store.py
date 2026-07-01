from rdflib import Graph

from . import prefix_apply, rdflib_bridge


class PrefixConflictError(ValueError):
    """
    Raised when a (prefix, url) pair being added to a :class:`PrefixStore`
    collides with an existing entry.

    ``kind`` is ``"prefix_collision"`` when the same prefix is being
    registered to a different URL, or ``"url_collision"`` when a URL is
    already registered under a different prefix. ``existing`` and
    ``incoming`` are ``(prefix, url)`` tuples.
    """

    def __init__(
        self,
        kind: str,
        existing: tuple[str, str],
        incoming: tuple[str, str],
    ) -> None:
        self.kind = kind
        self.existing = existing
        self.incoming = incoming
        super().__init__(f"{kind}: existing={existing}, incoming={incoming}")


class PrefixStore:
    """
    Registry of ``(prefix, url)`` pairs and string-level compact / expand /
    drop operations.

    Polymorphic application across dicts, lists, and DataFrames lives in
    :mod:`rdfine.prefix_apply`; rdflib glue (graph binding, node conversion)
    lives in :mod:`rdfine.rdflib_bridge`. The methods of those modules are
    re-exposed here as thin forwarders so existing call sites keep working.

    Properties:
            - prefixes: ``dict[str, str]`` mapping prefix -> url. Ordered
              alphabetically for stable display.

    Conflict handling:
            ``load`` and ``replace_prefix_in_store`` raise
            :class:`PrefixConflictError` if an incoming entry would clash
            with an existing one. Conflicts are never resolved silently.
    """

    def __init__(self, source):
        self.prefixes: dict[str, str] = {}
        # Tuples of (prefix, url) ordered from longest URL to shortest so that
        # the most specific prefix wins during compaction.
        self._ordered_prefix_items: list[tuple[str, str]] = []
        self.load(source)

    ###########################
    # LOAD PREFIXES
    ###########################

    def load(self, source: Graph | dict, replace: bool = True) -> None:
        """
        Load prefixes from a source. Dispatches on the type of ``source``.

        If ``replace`` is False, performs an upsert instead of wiping and
        replacing stored prefixes.

        Supported sources:
            - ``rdflib.Graph``: prefixes are read from the graph's namespaces.
            - ``dict``: copied as-is.

        Raises :class:`PrefixConflictError` if any (prefix, url) pair in
        ``source`` collides with an existing entry. With ``replace=True`` no
        collisions with prior state are possible, but the source itself is
        still checked for internal consistency.
        """
        if isinstance(source, Graph):
            prefixes = self._load_from_namespace(source)
        elif isinstance(source, dict):
            prefixes = dict(source)
        else:
            raise TypeError(
                f"Source of type {type(source).__name__} not supported. "
                "Expected rdflib.Graph or dict."
            )

        self._update(prefixes, replace=replace)

    def _load_from_namespace(self, graph: Graph) -> dict:
        """
        Takes the native namespaces from the rdflib library and
        turns them into a dict
        """
        prefixes = {}
        for namespace in graph.namespaces():
            prefix = list(namespace)[0]
            url = str(list(namespace)[1])
            prefixes[prefix] = self.expand_string(
                self.node_to_python(url)
            )  # Url is expanded string version

        return prefixes

    def _update(self, dict_prefixes: dict[str, str], replace: bool = True) -> None:
        """
        Merge ``dict_prefixes`` into ``self.prefixes``.

        - ``replace=True``: discard current state, then merge.
        - ``replace=False``: upsert into current state.

        Conflicts are detected against the *post-replace* state. An incoming
        pair raises :class:`PrefixConflictError` when:

        - its prefix is already registered with a different URL
          (``"prefix_collision"``), or
        - its URL is already registered under a different prefix
          (``"url_collision"``).

        Re-registering an identical ``(prefix, url)`` pair is a no-op.
        """
        merged: dict[str, str] = {} if replace else dict(self.prefixes)
        url_to_prefix: dict[str, str] = {url: p for p, url in merged.items()}

        for prefix, url in dict_prefixes.items():
            existing_url = merged.get(prefix)
            if existing_url is not None and existing_url != url:
                raise PrefixConflictError(
                    "prefix_collision",
                    existing=(prefix, existing_url),
                    incoming=(prefix, url),
                )
            existing_prefix = url_to_prefix.get(url)
            if existing_prefix is not None and existing_prefix != prefix:
                raise PrefixConflictError(
                    "url_collision",
                    existing=(existing_prefix, url),
                    incoming=(prefix, url),
                )
            merged[prefix] = url
            url_to_prefix[url] = prefix

        self.prefixes = merged
        self._order_prefixes()

    ###########################
    # APPLY PREFIXES
    ###########################

    def apply_prefixes(self, data, action: str):
        """
        Apply *this store's* prefixes to ``data`` under ``action``
        (``"compact"`` | ``"expand"`` | ``"drop"``). Dispatches on the type
        of ``data`` via :func:`rdfine.prefix_apply.apply_prefixes`.
        """
        return prefix_apply.apply_prefixes(data, self, action)

    def compact(self, data):
        """
        Convenience alias for ``apply_prefixes(data, "compact")``. For
        string-only inputs prefer :meth:`compact_string`.
        """
        return self.apply_prefixes(data, "compact")

    def expand(self, data):
        """
        Convenience alias for ``apply_prefixes(data, "expand")``. For
        string-only inputs prefer :meth:`expand_string`.
        """
        return self.apply_prefixes(data, "expand")

    def drop(self, data):
        """
        Convenience alias for ``apply_prefixes(data, "drop")``. For
        string-only inputs prefer :meth:`drop_string`.
        """
        return self.apply_prefixes(data, "drop")

    def compact_string(self, url: str) -> str:
        """
        Compact a single IRI string against the registry. Longest-URL match
        wins; the input is returned unchanged if no prefix applies.
        """
        for prefix, prefix_url in self._ordered_prefix_items:
            if url.startswith(prefix_url):
                return prefix + ":" + url.removeprefix(prefix_url)
        return url

    def expand_string(self, url: str) -> str:
        """
        Expand a compacted IRI string (``prefix:local``) back to its full URL
        using the registry. Returns the input unchanged if the prefix is
        not registered or the string has no ``:``.
        """
        if ":" in url:
            prefix, local = url.split(":", 1)
            if prefix in self.prefixes:
                return self.prefixes[prefix] + local
        return url

    def drop_string(self, url: str) -> str:
        """
        Strip any known prefix from a single IRI string, returning only the
        local part. Unknown / unprefixed inputs are returned unchanged.
        """
        prefix = self.fetch_prefix(url)
        if prefix is None:
            return url
        shortened_url = self.compact_string(url)
        return shortened_url.removeprefix(prefix + ":")

    ###########################
    # OTHER
    ###########################

    def fetch_prefix(self, node_id: str) -> str | None:
        """
        Look up which known prefix (if any) ``node_id`` uses.

        Returns the matching prefix name (e.g. ``"rdf"``) or ``None`` when no
        known prefix applies. Longest-URL match wins, consistent with
        :meth:`compact_string`.
        """

        compacted_node_id = self.compact_string(node_id)

        if ":" in compacted_node_id:
            prefix, _ = compacted_node_id.split(":", 1)
            if prefix in self.prefixes:
                return prefix

        return None

    def bind_to_namespace(self, graph: Graph) -> None:
        """
        Forward to :func:`rdfine.rdflib_bridge.bind_to_namespace`.
        """
        rdflib_bridge.bind_to_namespace(graph, self)

    def include_in_query(self, query: str) -> str:
        """
        Forward to :func:`rdfine.prefix_apply.include_in_query`.
        """
        return prefix_apply.include_in_query(query, self)

    def _order_prefixes(self) -> None:
        """
        Refresh derived prefix orderings after ``self.prefixes`` changes.

        - ``self._ordered_prefix_items``: list of ``(prefix, url)`` tuples
          sorted by URL length descending. Longer (more specific) URLs are
          matched first during compaction so they take precedence on ties.
        - ``self.prefixes`` itself is re-bound in alphabetical key order for
          stable, human-readable printing.
        """

        # Match the longest urls first (most specific takes precedence).
        self._ordered_prefix_items = sorted(
            self.prefixes.items(), key=lambda item: -len(item[1])
        )

        # Keep ``self.prefixes`` itself ordered alphabetically for display.
        self.prefixes = {key: self.prefixes[key] for key in sorted(self.prefixes)}

    def replace_prefix_in_store(self, original: str, replacement: str) -> None:
        """
        Rename a prefix in the store. Affects both ``compact`` and ``expand``.

        Raises:
            KeyError: if ``original`` is not registered.
            PrefixConflictError: if ``replacement`` is already registered
                under a different URL.
        """
        if original not in self.prefixes:
            raise KeyError(f"Prefix '{original}' is not registered.")
        url = self.prefixes[original]
        if replacement in self.prefixes and self.prefixes[replacement] != url:
            raise PrefixConflictError(
                "prefix_collision",
                existing=(replacement, self.prefixes[replacement]),
                incoming=(replacement, url),
            )
        del self.prefixes[original]
        self.prefixes[replacement] = url
        self._order_prefixes()

    ###########################
    # TYPE CONVERSIONS
    ###########################
    # The conversions themselves live in :mod:`rdfine.rdflib_bridge` so the
    # registry stays free of rdflib-specific concerns. These methods remain
    # here as thin forwarders for callers that prefer ``store.node_to_python``
    # over importing the bridge module directly.

    def node_to_python(self, cell):
        """Forward to :func:`rdfine.rdflib_bridge.node_to_python`."""
        return rdflib_bridge.node_to_python(cell, self)

    def python_to_node(self, cell, node_class):
        """Forward to :func:`rdfine.rdflib_bridge.python_to_node`."""
        return rdflib_bridge.python_to_node(cell, node_class, self)

    ###########################
    # DUNDER METHODS
    ###########################

    # String shown upon print
    def __repr__(self):
        return f"PrefixStore({str(dict(self.prefixes))})"

    # Indexing prefix_store returns indexed self.prefixes
    def __getitem__(self, key):
        return self.prefixes[key]
