"""End-to-end tests for the remaining ValidationReportCompiler methods —
fill_missing_shapes, list_shapes_to_match, validate_throughput_shapes,
match_shapes and generate_validation_report — run against the real
demonstrator pipeline (demo:DishacledPipeline).

Together with test_gather_throughput_shapes.py and
test_normalize_passthrough_shapes.py, this exercises the full pillar-2
strategy from test suite/README.md end to end: every channel ends up
with both an inputShape and an outputShape, one row per channel is
queued for shape-matching, and a single combined report file is
attached to the build."""

from compilers import ValidationReportCompiler
from testing_helpers import compile_pipeline


def _channels(build):
    reads_from = build.select(
        "?instance ?channel", "?instance tcs:readsFrom ?channel ."
    )
    writes_to = build.select("?instance ?channel", "?instance tcs:writesTo ?channel .")
    return set(reads_from["channel"]) | set(writes_to["channel"])


def _report_content(build) -> str:
    df = build.filter(pred="tcs:filename", obj="validation-report.ttl").df
    file_node = df["sub"].iloc[0]
    return str(build.filter(sub=file_node, pred="tcs:literal").df["obj"].iloc[0])


def test_fill_missing_shapes_gives_every_channel_both_roles(demonstrator_graph):
    _, build = compile_pipeline(demonstrator_graph, "demo:DishacledPipeline")
    channels = _channels(build)
    assert channels, "sanity check: the real pipeline has channels"

    for role in ("tcs:inputShape", "tcs:outputShape"):
        resolved = set(
            build.select(
                "?channel",
                f"""
                ?channel dcat:qualifiedRelation ?rel .
                ?rel dcat:hadRole {role} ; dct:relation ?shape .
                """,
            )["channel"]
        )
        missing = channels - resolved
        assert not missing, f"channels missing {role}: {missing}"


def test_list_shapes_to_match_covers_every_channel_exactly_once(demonstrator_graph):
    gen, build = compile_pipeline(demonstrator_graph, "demo:DishacledPipeline")
    channels = _channels(build)
    shapes_to_match = gen.compilers[ValidationReportCompiler].shapes_to_match
    assert set(shapes_to_match["channel"]) == channels
    assert len(shapes_to_match) == len(channels)


def test_validate_throughput_shapes_defaults_to_unverified(demonstrator_graph):
    # No shape-matching library is available yet, so match_shapes()'s
    # default stub must leave every pair unverified rather than
    # guessing a result.
    gen, _ = compile_pipeline(demonstrator_graph, "demo:DishacledPipeline")
    matches = gen.compilers[ValidationReportCompiler].throughput_matches["matches"]
    assert matches.isna().all() or (matches == None).all()  # noqa: E711


def test_match_shapes_override_flows_into_the_report(demonstrator_graph, monkeypatch):
    # match_shapes() is the documented extension point for the future
    # native Python shape-matching library — verify overriding it
    # actually changes the end-to-end result, not just the isolated method.
    monkeypatch.setattr(
        ValidationReportCompiler, "match_shapes", lambda self, i, o: True
    )
    gen, build = compile_pipeline(demonstrator_graph, "demo:DishacledPipeline")

    matches = gen.compilers[ValidationReportCompiler].throughput_matches["matches"]
    assert (matches == True).all()  # noqa: E712

    content = _report_content(build)
    assert "tcs:ThroughputMatchResult" in content
    assert '"true"' in content
    assert '"unknown"' not in content


def test_generate_validation_report_attaches_exactly_one_file(demonstrator_graph):
    gen, build = compile_pipeline(demonstrator_graph, "demo:DishacledPipeline")

    files = build.filter(pred="tcs:filename", obj="validation-report.ttl").df
    assert len(files) == 1

    content = _report_content(build)
    assert "sh:conforms" in content
    assert "ThroughputMatchResult" in content

    compiler = gen.compilers[ValidationReportCompiler]
    assert compiler.conforms is True
    assert len(compiler.throughput_matches) == len(_channels(build))


def test_validated_shapes_is_python_only_not_asserted_into_report(demonstrator_graph):
    # validated_shapes tracks shape coverage for programmatic inspection
    # only (SpecializedComponentIsCatalogedShape carries a sh:target via
    # sh:sparql, so it must show up here) — it must never be asserted as
    # RDF into the attached report, since that would mean inventing new
    # tcs: vocabulary without sign-off.
    gen, build = compile_pipeline(demonstrator_graph, "demo:DishacledPipeline")
    compiler = gen.compilers[ValidationReportCompiler]

    assert "tcs:SpecializedComponentIsCatalogedShape" in compiler.validated_shapes
    assert not hasattr(compiler, "untargeted_shapes")

    content = _report_content(build)
    assert "ValidatedShape" not in content
    assert "UntargetedShape" not in content


def test_real_pipeline_components_are_not_flagged_as_dangling(demonstrator_graph):
    # GraphReducer's narrowing must preserve the catalog's own
    # dcat:resource membership assertion for every component this
    # pipeline's steps actually specialize — otherwise every real, valid
    # step looks identical to a genuinely dangling/mistyped
    # prov:specializationOf target to SpecializedComponentIsCatalogedShape.
    gen, _ = compile_pipeline(demonstrator_graph, "demo:DishacledPipeline")
    report = gen.compilers[ValidationReportCompiler]._shacl_report
    violations = report.select(
        "?focus ?message",
        """
        ?r a sh:ValidationResult ;
           sh:sourceShape tcs:SpecializedComponentIsCatalogedShape ;
           sh:focusNode ?focus ; sh:resultMessage ?message .
        """,
    )
    assert violations.empty, violations["message"].tolist()
