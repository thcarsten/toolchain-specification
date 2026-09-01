from rdflib import Graph
from rdfine import GraphReader, GraphDict

from ..base import Compiler
from ..utils import attach_file, parse_docker_compose_config

# Top-level compose keys whose values are name-to-body mappings. Their
# member order is meaningless to Docker, so they are sorted by name to
# give a canonical file. Anything else (``version``, scalars, lists) is
# left exactly as authored.
_NAME_KEYED_SECTIONS = ("services", "volumes", "networks", "configs", "secrets")


def _canonical(compose_file: dict) -> dict:
    """Return ``compose_file`` with its name-keyed sections sorted.

    Sorting the config list already makes the output deterministic;
    sorting here additionally makes it *stable under renaming*, so
    changing a ``tcs:DockerComposeConfig`` IRI no longer reshuffles
    unrelated services. Service bodies are untouched — their key order
    comes from the catalog literal and is the author's choice.
    """
    result = {}
    for key, value in compose_file.items():
        if key in _NAME_KEYED_SECTIONS and isinstance(value, dict):
            result[key] = {name: value[name] for name in sorted(value)}
        else:
            result[key] = value
    return result


class DockerComposeCompiler(Compiler):
    """
    Compiles the docker compose configuration file.

    Invoked as an explicit finalize call by
    :class:`compilers.runner.CompilationRunner` after the fixpoint
    loop terminates, so every other compiler that may still be
    editing ``tcs:DockerComposeConfig`` bodies (e.g.
    :class:`SemanticWorksEnvVarCompiler`) has already finished.
    Membership in the generation preset's ``finalize_compilers`` is
    what makes this ordering fixed, not any per-class flag.
    """

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        # Intermediate state — populated in ``compile``.
        self.compose_file: dict = {"services": {}}
        self.config_service_name: dict[str, str] = {}

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Applicable only in the finalize phase of a compilation run.

        Gates on ``<?> tcs:runPhase tcs:FinalizePhase`` — the marker
        that :class:`CompilationRunner` attaches to the compilation
        request between the two fixpoint passes. Before that marker
        appears, every ``tcs:DockerComposeConfig`` body may still be
        edited by e.g. :class:`SemanticWorksEnvVarCompiler`, so this
        compiler must not fire yet.
        """
        return not graph_reader.filter(
            pred="tcs:runPhase", obj="tcs:FinalizePhase"
        ).df.empty

    def compile(self) -> Graph:
        self.merge_docker_compose_configs()
        self.fold_in_depends_on()
        self.attach_docker_compose_file()
        return self.output_reader.graph

    def merge_docker_compose_configs(self) -> None:
        """Aggregate every ``tcs:DockerComposeConfig`` reachable from a
        ``tcs:DockerContainer`` on this build into one normalized
        compose-file dict, stashed on :attr:`compose_file` for
        :meth:`attach_docker_compose_file` to serialize. Also records, on
        :attr:`config_service_name`, the compose service name each config
        normalized to — reused by :meth:`fold_in_depends_on` instead of
        re-parsing.

        Scoped to containers actually reachable from this build (rather
        than every ``tcs:DockerComposeConfig`` present anywhere in the
        graph) so this compiler doesn't depend on the graph having
        already been narrowed down to just this pipeline elsewhere —
        the same reachability path :meth:`_lookup_container_service_names`
        already uses.
        """
        # Sorted for reproducibility: SPARQL SELECT rows come back in
        # whatever order the triple store yields, and two things depend
        # on that order — which config the collision guard below blames
        # second, and, since the YAML dumper preserves insertion order,
        # the order services appear in the emitted file. Without the
        # sort the same build graph produces a different (if equivalent)
        # docker-compose.yml on each run.
        config_list = sorted(
            set(
                self.output_reader.select(
                    "?config",
                    """
            ?container a tcs:DockerContainer ; tcs:instantiates ?component .
            ?component tcs:config ?config .
            ?config a tcs:DockerComposeConfig .
            """,
                )["config"]
            )
        )

        # Every ``tcs:DockerComposeConfig`` is normalized to the same
        # compose-file shape (``{"services": {...}, ...}``) by
        # ``parse_docker_compose_config``, so aggregating multiple
        # configs is a merge per top-level key. Two configs are never
        # *expected* to name the same service/volume/network entry —
        # docker-compose requires unique names, so that's always a
        # modelling mistake; raise instead of letting one silently
        # clobber the other.
        compose_file: dict = {"services": {}}
        contributed_by: dict[str, str] = {}
        config_service_name: dict[str, str] = {}
        for config_id in config_list:
            normalized = parse_docker_compose_config(self.output_reader, config_id)
            if normalized.get("services"):
                config_service_name[config_id] = next(iter(normalized["services"]))
            for key, val in normalized.items():
                if isinstance(compose_file.get(key), dict) and isinstance(val, dict):
                    for name in val:
                        owner = contributed_by.get(f"{key}.{name}")
                        if owner is not None and owner != config_id:
                            raise ValueError(
                                f"tcs:DockerComposeConfig {config_id!s} and {owner!s} "
                                f"both declare a {key!r} entry named {name!r} — "
                                "docker-compose requires unique names; rename one "
                                "of them in the catalog/pipeline definition."
                            )
                        contributed_by[f"{key}.{name}"] = config_id
                    compose_file[key].update(val)
                else:
                    compose_file[key] = val

        self.compose_file = compose_file
        self.config_service_name = config_service_name

    def fold_in_depends_on(self) -> None:
        """Add a ``depends_on`` list to every service in :attr:`compose_file`
        that has one, combining two sources of container-to-container
        dependency:

        1. **Explicit** — a ``dct:requires`` edge between two components
           that each already live in a *different* ``tcs:DockerContainer``
           (see :meth:`_lookup_explicit_container_dependencies`).
        2. **Flow-order fallback** — a ``tcs:Channel`` crossing two
           different containers, only for container pairs the explicit
           source above has no opinion about (see
           :meth:`_lookup_floworder_container_dependencies`).

        Neither source is itself queried for containers that don't own a
        compose service (e.g. a processor folded into its orchestrator's
        container by ``PipelineAssembler``) — :meth:`_lookup_container_service_names`
        maps a container to ``None`` in that case, and such pairs are
        dropped rather than turned into a self-depends_on or an entry
        pointing at a nonexistent service.
        """
        container_service = self._lookup_container_service_names()
        explicit = self._lookup_explicit_container_dependencies()
        floworder = self._lookup_floworder_container_dependencies(explicit)

        depends_on: dict[str, set[str]] = {}
        for container1, container2 in explicit | floworder:
            name1 = container_service.get(container1)
            name2 = container_service.get(container2)
            if name1 is None or name2 is None or name1 == name2:
                continue
            depends_on.setdefault(name1, set()).add(name2)

        for name, deps in depends_on.items():
            if name in self.compose_file["services"]:
                self.compose_file["services"][name]["depends_on"] = sorted(deps)

    def _lookup_container_service_names(self) -> dict[str, str]:
        """Map each ``tcs:DockerContainer`` to the compose service name it
        corresponds to, via whichever component it instantiates owns a
        ``tcs:DockerComposeConfig`` (``PipelineAssembler`` builds exactly
        one container per such component). Reuses
        :attr:`config_service_name` — the names :meth:`merge_docker_compose_configs`
        already derived — instead of re-parsing configs a second time.
        """
        rows = self.output_reader.select(
            "?container ?config",
            """
            ?container a tcs:DockerContainer ; tcs:instantiates ?component .
            ?component tcs:config ?config .
            ?config a tcs:DockerComposeConfig .
            """,
        )
        return {
            container: self.config_service_name[config]
            for container, config in zip(rows["container"], rows["config"])
            if config in self.config_service_name
        }

    def _lookup_explicit_container_dependencies(self) -> set[tuple[str, str]]:
        """Phase 1: container pairs ``(c1, c2)`` meaning c1 depends_on c2,
        from an explicit ``dct:requires`` edge between two components that
        each already live in a *different* ``tcs:DockerContainer``. A
        ``dct:requires`` edge between a component and the microservice
        ``PipelineAssembler`` folds it into never crosses containers, so
        this only ever fires for genuine cross-container software
        dependencies (e.g. ``sw:loket-error-alert-service dct:requires
        sw:mu-delta-notifier``).
        """
        rows = self.output_reader.select(
            "?c1 ?c2",
            """
            ?c1 a tcs:DockerContainer ; tcs:instantiates ?comp1 .
            ?comp1 dct:requires ?comp2 .
            ?c2 a tcs:DockerContainer ; tcs:instantiates ?comp2 .
            FILTER (?c1 != ?c2)
            """,
        )
        return set(zip(rows["c1"], rows["c2"]))

    def _lookup_floworder_container_dependencies(
        self, explicit_pairs: set[tuple[str, str]]
    ) -> set[tuple[str, str]]:
        """Phase 2 (fallback): for a ``tcs:Channel`` crossing two different
        containers, the producing container depends_on the consuming
        container — mirrors the demonstrator's hand-written
        ``ldio-workbench -> rdfc`` edge, which has no ``dct:requires``
        counterpart at all. Skipped for any container pair Phase 1
        already has an opinion about, in either direction — avoids both a
        duplicate and a 2-cycle (docker-compose rejects mutual
        depends_on).
        """
        covered = {frozenset(pair) for pair in explicit_pairs}
        rows = self.output_reader.select(
            "?cProd ?cCons",
            """
            ?prodStep tcs:writesTo ?ch .
            ?consStep tcs:readsFrom ?ch .
            ?cProd tcs:runs ?prodStep .
            ?cCons tcs:runs ?consStep .
            FILTER (?cProd != ?cCons)
            """,
        )
        return {
            (c_prod, c_cons)
            for c_prod, c_cons in zip(rows["cProd"], rows["cCons"])
            if frozenset((c_prod, c_cons)) not in covered
        }

    def attach_docker_compose_file(self) -> None:
        """Serialize :attr:`compose_file` to YAML and attach it as
        ``./docker-compose.yml`` on the build.
        """
        yaml_string = GraphDict(
            _canonical(self.compose_file), prefix_store=self.output_reader.prefix_store
        ).serialize("yml", "drop")

        self.output_reader = attach_file(
            self.output_reader,
            filename="docker-compose.yml",
            filepath=".",
            content=yaml_string,
        )
