from rdflib import Graph

from rdfine import GraphReader

from ..base import Compiler


class SegmentTagger(Compiler):
    """Tag each maximal run of channel-connected steps within one
    container as one ``tcs:segment``.

    Walks ``tcs:readsFrom``/``tcs:writesTo`` forward from every Entry-
    marked step to the next Exit-marked step or container boundary,
    attaching ``tcs:segment :segment_N`` to each step encountered on
    the way. Steps not reached by any Entry walk are then grouped by
    connected component within their container so channel-linked
    chains without a boundary (e.g. an LDIO segment that begins with
    an ``ldio:HttpInPoller`` puller rather than a push-based Entry)
    still form a single segment. Truly isolated steps with no
    channel wiring (e.g. semantic.works event-driven services) end
    up as single-step segments.
    """

    def __init__(self, graph: Graph) -> None:
        super().__init__(graph)
        self._next_segment_index = 0

    @classmethod
    def applies_to(cls, graph_reader: GraphReader) -> bool:
        """Fires once :class:`PipelineAssembler` has assigned steps to
        containers — every step is then eligible for tagging, whether
        or not :class:`BridgeTransportCompiler` also runs. Registry
        order (Bridge imported before this compiler) ensures any
        Bridge-inserted boundary steps are visible when this compiler
        runs in the same fixpoint iteration.
        """
        return not graph_reader.filter(
            pred="dct:creator", obj="tcs:PipelineAssembler"
        ).df.empty

    def compile(self) -> Graph:
        self.tag_segments()
        return self.output_reader.graph

    def tag_segments(self) -> None:
        assigned: dict[str, str] = {}
        entries = self._lookup_entry_steps()
        for entry in entries:
            if entry in assigned:
                continue
            segment_id = self._mint_segment_id()
            self._walk_segment_from(entry, segment_id, assigned)

        # Untagged steps: group them by channel-connected component
        # within their container, so a chain that never touched an
        # Entry boundary still forms one segment. Isolated steps
        # with no channel wiring end up as their own single-step
        # segment.
        for step in self._lookup_untagged_steps(assigned):
            if step in assigned:
                continue
            segment_id = self._mint_segment_id()
            self._tag_connected_component(step, segment_id, assigned)

        for step, segment_id in assigned.items():
            new_triples = self.output_reader.construct(
                f"{step} tcs:segment {segment_id} . {segment_id} a tcs:Segment .",
                "?s ?p ?o .",
            ).graph
            self.output_reader = self.output_reader.add(new_triples)

    def _lookup_entry_steps(self) -> list[str]:
        return sorted(
            self.output_reader.select(
                "?step",
                """
                ?step a tcs:InstancePipelineComponent ;
                      prov:specializationOf ?component .
                ?component a tcs:EntryBoundaryComponent .
                """,
            )["step"]
            .drop_duplicates()
            .to_list()
        )

    def _walk_segment_from(
        self, entry: str, segment_id: str, assigned: dict[str, str]
    ) -> None:
        container = self._lookup_container(entry)
        stack = [entry]
        while stack:
            step = stack.pop()
            if step in assigned:
                continue
            assigned[step] = segment_id
            if self._is_exit(step) and step != entry:
                continue
            for successor in self._lookup_successors(step, container):
                if successor not in assigned:
                    stack.append(successor)

    def _lookup_container(self, step: str) -> str | None:
        containers = (
            self.output_reader.filter(pred="tcs:runs", obj=step).df["sub"].to_list()
        )
        return containers[0] if containers else None

    def _is_exit(self, step: str) -> bool:
        return self.output_reader.ask(
            f"{step} prov:specializationOf ?c . ?c a tcs:ExitBoundaryComponent ."
        )

    def _lookup_successors(self, step: str, container: str | None) -> list[str]:
        rows = self.output_reader.select(
            "?next",
            f"""
            {step} tcs:writesTo ?ch .
            ?next tcs:readsFrom ?ch .
            """,
        )
        successors = rows["next"].drop_duplicates().to_list()
        if container is None:
            return successors
        return [
            s
            for s in successors
            if self.output_reader.ask(f"{container} tcs:runs {s} .")
        ]

    def _lookup_untagged_steps(self, assigned: dict[str, str]) -> list[str]:
        all_steps = (
            self.output_reader.filter(
                pred="rdf:type", obj="tcs:InstancePipelineComponent"
            )
            .df["sub"]
            .drop_duplicates()
            .to_list()
        )
        return [s for s in all_steps if s not in assigned]

    def _tag_connected_component(
        self, seed: str, segment_id: str, assigned: dict[str, str]
    ) -> None:
        container = self._lookup_container(seed)
        stack = [seed]
        while stack:
            step = stack.pop()
            if step in assigned:
                continue
            assigned[step] = segment_id
            for neighbour in self._lookup_channel_neighbours(step, container):
                if neighbour not in assigned:
                    stack.append(neighbour)

    def _lookup_channel_neighbours(self, step: str, container: str | None) -> list[str]:
        rows = self.output_reader.select(
            "?other",
            f"""
            {{ {step} tcs:writesTo ?ch . ?other tcs:readsFrom ?ch . }}
            UNION
            {{ {step} tcs:readsFrom ?ch . ?other tcs:writesTo ?ch . }}
            """,
        )
        neighbours = rows["other"].drop_duplicates().to_list()
        if container is None:
            return neighbours
        return [
            s
            for s in neighbours
            if self.output_reader.ask(f"{container} tcs:runs {s} .")
        ]

    def _mint_segment_id(self) -> str:
        segment_id = f":segment_{self._next_segment_index}"
        self._next_segment_index += 1
        while self.output_reader.check_exists(segment_id):
            segment_id = f":segment_{self._next_segment_index}"
            self._next_segment_index += 1
        return segment_id
