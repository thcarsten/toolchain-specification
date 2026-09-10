# D2.3b: results on the tests for dataset integration pipelines

This deliverable reports execution-time measurements for the two dataspace reference architectures of [D2.2b](../D2.2b/README.MD), Brugge waterkwaliteit and Brugge fietsstallingen, when their pipelines are produced by the pipeline generator of the toolchain specification, and places these measurements in the context of what an execution-time comparison with hand-built pipelines can and cannot express. All figures are means and standard deviations over five runs on the same developer laptop (Windows 11, Docker Desktop), obtained on 10 September 2026.

## 1. Generation time per step

The generation notebooks ([demo_nifi.ipynb](../../pipeline%20generator/src/demo_nifi.ipynb), [demo_fietsstallingen.ipynb](../../pipeline%20generator/src/demo_fietsstallingen.ipynb)) have an identical cell layout so that the steps are directly comparable: import the generator, load the catalog and pipeline definition into a graph and apply the inference rules, validate the graph against the SHACL application profile, compile, and write the project folder. Each notebook was executed five times with `nbconvert`; durations are taken from the cell execution metadata.

| Step | waterkwaliteit | fietsstallingen |
| --- | ---: | ---: |
| Import generator | 0.61 ± 0.02 s | 0.59 ± 0.01 s |
| Load catalog and definition, apply inference | 0.29 ± 0.01 s | 0.35 ± 0.01 s |
| SHACL validation (pySHACL, application profile) | 3.27 ± 0.06 s | 3.27 ± 0.03 s |
| Compile | 12.56 ± 0.12 s | 27.08 ± 0.10 s |
| Write project folder | 0.01 ± 0.00 s | 0.01 ± 0.00 s |
| **Total** | **16.77 ± 0.15 s** | **31.31 ± 0.11 s** |

Compilation dominates in both cases. The fietsstallingen run compiles two plans across three frameworks, which accounts for its longer duration. Validation takes the same time for both definitions because the application profile is evaluated over the full catalog graph, whose size dwarfs that of either pipeline definition. Run-to-run variation is below one percent throughout.

## 2. What an execution-time comparison can express

The comparison that would be most informative, the time a practitioner saves by describing a pipeline once in the toolchain specification rather than configuring the target framework by hand, cannot be obtained from these figures, nor from any measurement feasible within this project. Development time is dominated by the practitioner's prior competence. An experienced NiFi user will draw and wire a flow in the canvas faster than they will author a first pipeline definition in Turtle; conversely, a practitioner familiar with the toolchain specification will produce a working LDIO pipeline faster than someone configuring LDIO for the first time. A fair measurement of development speed would require several practitioners per framework at controlled levels of experience, and its result would remain difficult to reproduce and to validate. What the toolchain specification offers independently of competence is not raw speed but the ability to work across frameworks from a single description, with the knowledge required of the target framework reduced to the properties exposed in the component catalog.

## 3. Overhead of the abstraction layer

The figures of Section 1 are the total cost of the abstraction. The generated artefacts are native configuration files of the target frameworks (LDIO YAML, an RDF-Connect pipeline, a NiFi persisted flow) and the pipelines execute in those frameworks exactly as their hand-written counterparts would. The pipeline generator therefore adds no runtime overhead; the sixteen to thirty-one seconds of generation are the entire additional cost, incurred once per change to the definition.

Against this cost stands an earlier point of failure detection. In a hand-built pipeline, a misconfiguration becomes visible at the earliest when the framework loads the configuration; in the toolchain specification, every class of error covered by the application profile and the component configuration shapes is reported by the validation step, with the offending node and a message, before any artefact is generated. To quantify the difference, the generated stacks were deployed five times each from a warm state (images present, no build, no network creation) and timed until the framework logged that it had processed the pipeline configuration, which is the moment a hand-written error would surface. For LDIO, which also accepts pipelines through its management API while running, the round trip of a delete-and-repost of the pipeline YAML was measured as well.

| Feedback path | Duration |
| --- | ---: |
| SHACL validation of the definition (either notebook) | 3.27 ± 0.05 s |
| LDIO: warm `docker compose up`, publisher, until pipeline initialiser finished | 8.18 ± 0.03 s |
| LDIO: hot POST of a pipeline to a running orchestrator, until HTTP response | 0.03 ± 0.02 s |
| LDIO + RDF-Connect + Virtuoso: warm `docker compose up`, consumer, until LDIO initialiser finished and RDF-Connect server listening | 15.71 ± 0.94 s |
| NiFi: warm `docker compose up`, waterkwaliteit, until "Started Application" | 20.52 ± 0.88 s |

Two qualifications follow from this table. First, against a container restart, validation shortens the feedback loop by a factor of two and a half (single LDIO workbench) to six (NiFi), which is a consistent but modest gain; a cold deployment that still has to pull or build images would widen the gap considerably, but such a cost is paid once and by both approaches alike. Second, where a framework accepts configuration through an API while running, as LDIO does, the framework's own feedback is as fast as validation, and the hand-built path has no latency disadvantage for the errors that framework detects at load time. The benefit of validation is then not speed but coverage and localisation: it reports cross-framework inconsistencies (a channel whose reader and writer disagree, an LDIO Input placed mid-pipeline, a NiFi relationship routed to a channel no processor reads) that no single framework can see, and it names the node in the definition rather than a line in a container log. Furthermore, a number of hand-built errors, such as a wrong SPARQL endpoint or graph IRI, pass load time altogether and surface only when the first message flows, or never; for these, the deploy timings above are a lower bound on the manual feedback latency. It is this combination, bounded and framework-independent feedback with a well-defined coverage, together with the cross-framework portability shown in D2.2b, that constitutes the practical value of the approach, rather than the absolute generation times themselves.
