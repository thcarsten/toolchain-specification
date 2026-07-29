# test suite

This readme outlines how Pipeline Definitions of the toolchain specification are to be validated. Validation takes place **before** the [pipeline generator](../pipeline%20generator/README.md) to guarantee that [`PipelineGenerator.compile()`](../pipeline%20generator/src/compilers/pipeline_generator.py) starts from a validated baseline.
<br><br>
To follow along, you should be familiar with the [semantic model](../semantic%20model/README.md) of the toolchain specification and [SHACL](https://www.w3.org/TR/shacl/) .

## Contents

- [Role vocabulary](#role-vocabulary)
  - [Overview](#overview)
  - [configShape](#configshape)
  - [inputShape and outputShape](#inputshape-and-outputshape)
- [Validation strategy](#validation-strategy)
  - [Validation steps](#validation-steps)
  - [Architecture](#architecture)
- [Future directions](#future-directions)
- [Roadmap](#roadmap)

## Role vocabulary

### Overview 

The default way of specifying constraints to certain target entities in a graph is via `sh:target`, as specified in the [official SHACL documentation](https://www.w3.org/TR/shacl/#targets). In addition to that, in the [toolchain specification](../semantic%20model/README.md) we also make use of `dcat:qualifiedRelation` to link SHACL shapes to `dcat:resources`. `dcat:qualifiedRelation` also allows to specify a `dcat:Role` for the relation between the `dcat:resource` and the SHACL shape. We use this mechanism as an alternative to `sh:target`, in that it allows to declare the role a SHACL shape plays with respect to the resource it is attached to. This is useful when specifying constraints for data that is not part of the graph, so no `sh:target` can be easily specified (as is the case for `inputShape` and `outputShape`, see below). It is also useful for cases where the `sh:target` may be cumbersome to express (as is the case by `configShape`). 

In the table below we list the roles we have defined. 

| Role | Attached to | What it constrains | 
| --- | --- | --- |
| `tcs:configShape` | `tcs:PipelineComponent` | schema of the component's `tcs:PipelineConfig` |
| `tcs:inputShape` | `tcs:PipelineComponent`, `tcs:InstancePipelineComponent` or `tcs:Channel` | Schema of data flowing **into** a `tcs:Channel` as part of a `tcs:PipelineDefinition` . | 
| `tcs:outputShape` | `tcs:PipelineComponent`, `tcs:InstancePipelineComponent` or `tcs:Channel` | Schema of data flowing **into** a `tcs:Channel` as part of a `tcs:PipelineDefinition` . |

Roles are extensible — new roles can be added without touching validation logic that concerns other roles. Using `dcat:Role` instead of `sh:target` is not default SHACL and hence will not be recognised or automatically validated by SHACL validators. Instead, we define our own strategy how and when these shapes are to be validated. 


### configShape 

A configShape can be attached to a Pipeline Component like so: 
```
ldio:SparqlConstructTransformer a tcs:PipelineComponent, dcat:Resource;
    rdfs:label "Ldio:SparqlConstructTransformer" ; 
    rdfs:description "The SPARQL Construct Transformer will modify the model based on the given SPARQL Construct Query.";
    ldio:type "Transformer" ;
    dcat:landingPage "https://informatievlaanderen.github.io/VSDS-Linked-Data-Interactions/2.8.0-SNAPSHOT/ldio/ldio-transformers/ldio-sparql-construct" ;
    dct:requires ldio:LinkedDataInteractionsOrchestrator ;
    dcat:qualifiedRelation [
        a dcat:Relationship;
        dcat:hadRole tcs:configShape ;
        dct:relation ldio:SparqlConstructTransformerConfigShape
] .
    
ldio:SparqlConstructTransformerConfigShape a sh:NodeShape;
    sh:property [
        sh:path ldio:endpoint;
        sh:name "endpoint";
        sh:datatype xsd:string;
        sh:minCount 1;
        sh:maxCount 1;
    ], [
    sh:path ldio:infer;
    sh:name "infer";
    sh:datatype xsd:boolean;
    sh:minCount 0;
    sh:maxCount 1;
].
```

Notice that in this example the `ldio:SparqlConstructTransformerConfigShape` has no target defined. Instead the target becomes clear via the `dcat:Relationship`. `ldio:SparqlConstructTransformerConfigShape` concerns the `tcs:PipelineComponent` it is attached to, and more specifically so its `tcs:PipelineConfig`. In other words, the example above is equivalent to
```
ldio:SparqlConstructTransformer a tcs:PipelineComponent, dcat:Resource;
    rdfs:label "Ldio:SparqlConstructTransformer" ; 
    rdfs:description "The SPARQL Construct Transformer will modify the model based on the given SPARQL Construct Query.";
    ldio:type "Transformer" ;
    dcat:landingPage "https://informatievlaanderen.github.io/VSDS-Linked-Data-Interactions/2.8.0-SNAPSHOT/ldio/ldio-transformers/ldio-sparql-construct" ;
    dct:requires ldio:LinkedDataInteractionsOrchestrator .
    
ldio:SparqlConstructTransformerConfigShape a sh:NodeShape;
    sh:prefixes :prefixes ;
    sh:select """
            SELECT ?this WHERE {
                ?instance prov:specializationOf ldio:SparqlConstructTransformer ;
                      p-plan:hasInputVar/tcs:embedded ?this .
            }
        """;
    sh:property [
        sh:path ldio:query;
        ...
```

As such, using `dcat:hadRole tcs:configShape` indicates that the Shape concerns `tcs:PipelineConfig`s of instances of the respective `tcs:PipelineComponent` in a `tcs:PipelineDefinition`. 


### inputShape and outputShape 

`inputShape` and `outputShape` describe the schema of the data that is expected to flow through each `tcs:InstancePipelineComponent` in a pipeline. As such, they describe the transformation a `tcs:InstancePipelineComponent` performs on the data in SHACL terms. See the following example:
```
# schema:PropertyValue → sosa:Observation + qudt:QuantityValue.
demo:SsnSosaMap a tcs:InstancePipelineComponent ;
    prov:specializationOf ldio:SparqlConstructTransformer ;
    p-plan:isStepOfPlan demo:DishacledPipeline ;
    dcat:qualifiedRelation [
        dcat:hadRole tcs:inputShape ;
        dct:relation ldio:transformerInputShape
    ] , [
        dcat:hadRole tcs:outputShape ;
        dct:relation ldio:transformerOutputShape
    ] ;
    p-plan:hasInputVar [
        a tcs:PipelineConfig ;
        tcs:embedded [
            :query """
                PREFIX schema: <https://schema.org/>
                PREFIX sosa:   <http://www.w3.org/ns/sosa/>
                PREFIX qudt:   <http://qudt.org/schema/qudt/>
                PREFIX unit:   <http://qudt.org/vocab/unit/>
                PREFIX dct:    <http://purl.org/dc/terms/>
                PREFIX rdfs:   <http://www.w3.org/2000/01/rdf-schema#>
                PREFIX xsd:    <http://www.w3.org/2001/XMLSchema#>
                PREFIX ex:     <http://dishacled.example.org/>
                CONSTRUCT {
                  ?obs a sosa:Observation ;
                       sosa:madeBySensor         ?sensor ;
                       sosa:observedProperty     ?prop ;
                       sosa:hasFeatureOfInterest ?place ;
                       sosa:resultTime           ?time ;
                       sosa:hasSimpleResult      ?value ;
                       sosa:hasResult            ?result .
                  ?result a qudt:QuantityValue ;
                          qudt:numericValue ?value ;
                          qudt:unit         ?unit .
                }
                WHERE {
                  ?obs a schema:PropertyValue ;
                       schema:value    ?value ;
                       schema:unitCode ?unitCode ;
                       sosa:resultTime ?time ;
                       schema:source   ?source ;
                       schema:location ?place .
                  VALUES (?unitCode ?unit) {
                    (\"CMT\" <http://qudt.org/vocab/unit/CentiM>)
                  }
                  BIND(<http://dishacled.example.org/sensor/source-a>     AS ?sensor)
                  BIND(<http://dishacled.example.org/property/waterLevel> AS ?prop)
                  BIND(IRI(CONCAT(STR(?obs), \"/result\"))                AS ?result)
                }
            """
        ]
    ] ;
    tcs:readsFrom demo:sourceARdf ;
    tcs:writesTo demo:sourceAObservations .

# What the transformer expects to read from demo:sourceARdf.
# Mirrors the star pattern of ?obs in the SPARQL CONSTRUCT's WHERE clause.
ldio:transformerInputShape a sh:NodeShape ;
    sh:closed false ;
    sh:property [
        sh:path rdf:type ;
        sh:hasValue schema:PropertyValue ;
        sh:minCount 1 ;
    ] , [
        sh:path schema:value ;
        sh:minCount 1 ; sh:maxCount 1 ;
    ] , [
        sh:path schema:unitCode ;
        sh:minCount 1 ; sh:maxCount 1 ;
        sh:datatype xsd:string ;
        sh:in ( "CMT" ) ;
    ] , [
        sh:path sosa:resultTime ;
        sh:minCount 1 ; sh:maxCount 1 ;
        sh:datatype xsd:dateTime ;
    ] , [
        sh:path schema:source ;
        sh:minCount 1 ; sh:maxCount 1 ;
        sh:nodeKind sh:IRI ;
    ] , [
        sh:path schema:location ;
        sh:minCount 1 ; sh:maxCount 1 ;
        sh:nodeKind sh:IRI ;
    ] .

# What the transformer produces onto demo:sourceAObservations.
# Mirrors the two star patterns of ?obs and ?result in the CONSTRUCT clause.
ldio:transformerOutputShape a sh:NodeShape ;
    sh:closed false ;
    sh:property [
        sh:path rdf:type ;
        sh:hasValue sosa:Observation ;
        sh:minCount 1 ;
    ] , [
        sh:path sosa:madeBySensor ;
        sh:minCount 1 ; sh:maxCount 1 ;
        sh:nodeKind sh:IRI ;
    ] , [
        sh:path sosa:observedProperty ;
        sh:minCount 1 ; sh:maxCount 1 ;
        sh:nodeKind sh:IRI ;
    ] , [
        sh:path sosa:hasFeatureOfInterest ;
        sh:minCount 1 ; sh:maxCount 1 ;
        sh:nodeKind sh:IRI ;
    ] , [
        sh:path sosa:resultTime ;
        sh:minCount 1 ; sh:maxCount 1 ;
        sh:datatype xsd:dateTime ;
    ] , [
        sh:path sosa:hasSimpleResult ;
        sh:minCount 1 ; sh:maxCount 1 ;
    ] , [
        sh:path sosa:hasResult ;
        sh:minCount 1 ; sh:maxCount 1 ;
        sh:node ldio:quantityValueShape ;
    ] .

# Nested shape reached from sosa:hasResult on the output observation.
ldio:quantityValueShape a sh:NodeShape ;
    sh:closed false ;
    sh:property [
        sh:path rdf:type ;
        sh:hasValue qudt:QuantityValue ;
        sh:minCount 1 ;
    ] , [
        sh:path qudt:numericValue ;
        sh:minCount 1 ; sh:maxCount 1 ;
    ] , [
        sh:path qudt:unit ;
        sh:minCount 1 ; sh:maxCount 1 ;
        sh:nodeKind sh:IRI ;
    ] .

```
In short, `inputShape` and `outputShape` is being used to describe the schema of the data flowing through this `InstancePipelineComponent`. In this example, these shapes are attached to the `InstancePipelineComponent` rather than the more generic `PipelineComponent`. This is because the `inputShape` and `outputShape` of the `ldio:SparqlConstructTransformer` largely depends on its `tcs:PipelineConfig`, which is provided when the `PipelineComponent` is instanced in a `PipelineDefinition`. Indeed, closer inspection reveals that the `inputShape` largely mirrors the `where`-clause of the query, whereas the `outputShape` mirrors the `construct`-clause. 
<br><br>
Attaching inputShapes and outputShapes directly to `PipelineComponent`s is appropriate when these shapes are representative of the expected data throughput no matter the configuration and position of the instanced component in a pipeline. This can be the case for simple components that do not require a lot of configuration, or have a standardized throughput. 
<br><br>
Attaching inputShapes and outputShapes to `Channel`s instead can be necessary for cases where instanced pipeline components have several inputs or outputs. To disambiguate to these, `Channel`s allow to specify which specific branch in a pipeline is associated with a specific data schema. Attaching an `inputShape` or `outputShape` to a `Channel` that is written to or read by a single `InstancePipelineComponent` is idiosyncratic to attaching this shape to the instanced component directly. 
<br><br>
Going from general to specific, `inputShape`s and `outputShape`s can be attached to
- `PipelineComponent`s if the schema of the data throughput does not change across `PipelineDefinition`s
-  `InstancePipelineComponent`s if the schema of the data throughput depends on how the component is configured or positioned in a pipeline
-  `Channel`s if disambiguation is needed between multiple inputs or outputs of a component. 

Any of these entities can be attached with zero or one inputShape and zero or one outputShape.

## Validation strategy

### Validation steps

Validation takes place **before** the pipeline generator compiles a pipeline build. For this, the following strategy is chosen: 

1. **Validating regular shapes**
- All `configShapes` receive a `sh:target` by interpreting the `dcat:qualifiedRelationship` and constructing appropriate `sh:select`-triples for the `configShape`. This step "normalizes" the SHACL shape in that it can be interpreted by a regular SHACL validator. 
- All 'regular' shapes (i.e. all shapes with a `sh:target`) are validated. 
<br><br>

2. **Validating input and output shapes**
- For a given `PipelineDefinition`, the most specific `inputShapes` and `outputShapes` per `InstancePipelineComponent` are selected. This can be done for example by ensuring that each `Channel` in a pipeline receives an `inputShape` and `outputShape`, if not already present, with the most specific shape taking precedence. The `InstancePipelineComponent` which `tcs:writesTo` a `Channel` provides the `inputShape` of that `Channel`. The `InstancePipelineComponent` which `tcs:readsFrom` a `Channel` provides the `outputShape` of that `Channel`. If no shape is associated with the specific `Channel`, an empty shape should be attached (to indicate that this channel has no known constraints).  
- `inputShape`s and `outputShapes` are validated by pairwise matching: For each `Channel`, its `inputShape` and `outputShape` are compared. In other words, it is assessed whether the output schema of one component matches with the input schema of the other component. 
<br><br>

3. **Reporting validation results**
- Results of the validation check should be reported if violations occurred.
- Warnings should be given if certain shapes were missing from the graph, as then it cannot be guaranteed that the validation report is accurate. This is the case if 
  - `InstancePipelineComponent`s were provided with a `tcs:PipelineConfig` which was **not** targeted by a shape via `sh:target`
  - A `Channel` had an empty `inputShape` although an `InstancePipelineComponent` `tcs:writesTo` it. 
  - A `Channel` had an empty `outputShape` although an `InstancePipelineComponent` `tcs:readsFrom` it.
- A violated validation report does not necessarily need to stop the `PipelineGenerator` from compiling a `PipelineBuild`, but the user needs to be informed that the `PipelineBuild` is invalid. 

### Architecture

The test suite can be understood as a `tcs:Compiler`, which attaches a validation report to a specific `PipelineDefinition`. As such, it could be integrated into the compilation flow of the `PipelineGenerator`. However, for simplicity and to ensure a separation of concerns, this  `ValidationReportCompiler` will be first developed independently, while still making use of the same design principles as the `PipelineGenerator`. 
<br><br>
Roughly speaking, the `ValidationReportCompiler` consists of the following methods/responsibilities:
1. **extract pipeline**
- Shrinks the data graph to the entities associated with a single pipeline 
- reuses the [`PipelineExtractor`](../pipeline%20generator/src/compilers/core/pipeline_extractor.py) for this. 
2. **normalize configShapes**
- Provide configShapes with `sh:target`s.
3. **validate regular shapes**
- use [`GraphReader.validate()`](../pipeline%20generator/src/rdfine/graph_reader.py) for this, which is a simple wrapper around [pySHACL](https://github.com/RDFLib/pySHACL)
4. **gather inputShapes and outputShapes**
- Per `Channel`, gather the most specific `inputShapes` and `outputShapes`
5. **validate `inputShapes` and `outputShapes` through shape-matching.**
6. **Attach a validation report to the validated `PipelineDefinition`.**

With the exception of shape-matching, all steps above can be executed in Python. Shape-matching however is implemented as a [Typescript library](https://github.com/DiSHACLed/query-shape-matching-algorithm). This requires a bridge between the `ValidationReportCompiler` and an external service responsible for shape-matching. For this, the `ValidationReportCompiler` talks to a small long-lived Node service over HTTP+JSON. 

Bridge:
- `qsm-service` is a thin Node process (Fastify) run in a Docker Container that exposes a containment-checking endpoint.
- The Python side ships a small client that serializes shapes as RDF strings (via `rdflib`) and calls the service with `httpx`.
- Full API contract between TypeScript and Python side is to be discussed


**Why a service, not in-process:** the Typescript library's transitive dependencies (Traqula parsers, n3.js, rdf-data-factory) are ESM and rely on Node
built-ins that a Python-embedded JS engine does not provide, so any in-process option would require a bundle-and-polyfill layer per dependency
upgrade. A separate service keeps 100 % Node compatibility, matches the docker-compose idiom used everywhere else in DiSHACLed, and adds only an
HTTP round-trip that is invisible at test-suite call volumes.

## Future directions

The `ValidationReportCompiler` gathers `inputShapes` and `outputShapes` per `Channel` as part of its compilation process. These shapes could be used to provide automatic on-line validation of data flowing through the instanced pipeline. That is, in addition to the static pre-compiler validation discussed above, pipelines generated by the `PipelineGenerator` could ensure that each data package flowing through the pipeline effectively adheres to the expected structure.  
<br>
One way this could be implemented is to write a new compiler called `ThroughputValidator` for the `PipelineGenerator`. This `ThroughputValidator` could modify the PipelineDefinition by adding a `ShaclValidator` - `InstancePipelineComponent` to the beginning and end of the PipelineDefinition, which is configured to validate the `inputShape` and `outputShape` corresponding to the beginning and end of the pipeline.  

## Roadmap
- [ ] Write sufficient Shacl Shapes for the [demonstrator pipeline](../pipeline%20generator/data/)
    - [x] [shapes](../pipeline%20generator/data/tcs_shapes.ttl) describing the application profile of the toolchain specification
    - [x] [shapes](../pipeline%20generator/data/tcs_shapes.ttl) describing constraints introduced by specific `Compilers`
    - [ ] `configShapes` for each `PipelineComponent` in the Demonstrator pipeline
    - [ ] `inputShape` and `outputShape` for each step in the Demonstrator pipeline 
- [ ] expand the [Typescript library](https://github.com/DiSHACLed/query-shape-matching-algorithm) to support shape-to-shape matching (rather than query-to-shape matching)
- [ ] expose shape-matching as a service that can be containerized and communicated with via API
- [ ] Prototype the `ValidationReportCompiler`
    - [ ] method: extract_pipeline
    - [ ] method: normalize_config_shapes
    - [ ] method: validate_regular_shapes
    - [ ] method: gather_throughput_shapes
    - [ ] method: validate_throughput_shapes
    - [ ] method: generate_validation_report