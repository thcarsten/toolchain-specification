
# Table Of Contents

- [1. Overview](#1-overview)
    - [1.1. Introduction](#11-introduction)
    - [1.2 Term definitions](#12-term-definitions)

- [2. Ontology](#2-ontology)
    - [2.1. Namespaces](#21-namespaces)
    - [2.2. Relation to other ontologies](#22-relation-to-other-ontologies)
    - [2.3. Core Classes](#23-core-classes)
    - [2.4. Additional Classes](#24-additional-classes)
    - [2.5. Subclasses of Config](#25-subclasses-of-config)
    - [2.6. Future Directions](#26-future-directions)

- [3. Examples](#3-examples)

<br><br>

# 1. Overview
## 1.1 Introduction
*Intention*: discoverability, replication, modular design, separation of concerns, ease of use 
<br><br>
The goal is to design a framework-agnostic, streaming-oriented pipeline orchestration system for Linked Data. Pipelines are described semantically using RDF, and execution is realized across multiple frameworks (currently LDIO, RDF Connect, semantic.works) using Docker containers and RDF Connect for orchestration. For this purpose, the *toolchain* ontology is developed.

The *toolchain* ontology allows to describe data pipelines for the purpose of replicability. Four Core Classes are established, each of which are concerned with different responsibilities. Providing a minimal **Pipeline Definition** is sufficient to allow replication of a pipeline. Pipelines are defined as a series of data transformation steps, which are executed by Pipeline Components. These Pipeline Components are collected in a **Catalog**, which hence provides the resources for building a pipeline. A Pipeline Generator takes a Pipeline Definition and attempts to build an executable pipeline based on the resources at its disposal in the Component Catalog. The **Pipeline Build** is the output of the Pipeline Generator and reflects a deployable pipeline.
<br><br>
Splitting the *toolchain* ontology into these four Core Classes leaves a clean separation of concerns: The Pipeline Definition allows the user to create new pipelines with minimal effort: It is sufficient to describe which Pipeline Components provide data to other Pipeline Components, and to provide Configs to configure each one used in the Pipeline. The Component Catalog is based on the idea that each Pipeline Component is modular and should be easy to reuse. Hence, all information that is known about a Pipeline Component (and which may be relevant for a Pipeline Generator) should be included in the Component Catalog. This allows a user to merely point to the Pipeline Component in the Component Catalog when defining a particular Pipeline Step. Hence, essential details about the component do not need to be repeated with each new Pipeline Definition. Likewise, distinguishing between the Pipeline Definition and the Pipeline Build allows defining Pipelines without having to be concerned with their implementation. It is up to the Pipeline Generator to figure out a feasible implementation of the pipeline, allowing to largely automate this process: Based on what is known about each Pipeline Component, the feasibility of the defined Pipeline can be evaluated and a corresponding Pipeline Build can be generated.  
<br>
Even if the Pipeline Generator is not utilized, the *toolchain* ontology provides a unified language for describing pipelines across different frameworks. This has several advantages. Pipelines and Pipeline Components become discoverable, allowing a community to grow around the idea of sharing modular resources for building pipelines. Datasets can be described by the pipelines that generated them, allowing full provenance. Pipeline Definitions can be subjected to automated validation, so that the feasibility of pipelines does not have to be evaluated through trial and error. 
<br><br>

## 1.2 Term definitions

| Term | Definition |
| ----- | ----- |
| **Pipeline** | We understand a Pipeline as an arrangement of modular data transformation steps, whereby each step acts on data in a well-defined, configurable way, before forwarding the data to the next step. These steps can be arranged in a nonlinear way, similar to a directed acyclic graph: Steps can receive zero or more inputs and outputs. To qualify as Pipeline, all Steps have to be directly or indirectly chained to each other. |
| **Data** | Data is any kind of digital content that carries information of interest. This information can be extracted through transformation and made available through transportation, i.e. through Pipelines. As such, Data are the entities that are send through pipeslines to make information available at a target location. |
| **Config** | A Config defines the expected (transformation) behavior for each Step in a Pipeline. As such, it differs from Data in so far as it is not subject to transformation itself, does not provide much valuable information beyond the behavior within the Pipeline, and is also not send through the Pipeline. | 
| **Instance** | We understand instantiating Pipeline Components as installing or deploying these so that they are ready to run. | 
<br><br>


# 2. Ontology
## 2.1. Namespaces
| prefix |	namespace IRI | documentation |
|----------|----------|----------|
| dcat  | http://www.w3.org/ns/dcat#  | https://www.w3.org/TR/vocab-dcat-3/ |
| dct | http://purl.org/dc/terms/ | https://www.dublincore.org/specifications/dublin-core/dcmi-terms/ |
| p-plan  | http://purl.org/net/p-plan# | https://vocab.linkeddata.es/p-plan/index.html |
| prov  | http://www.w3.org/ns/prov# | https://www.w3.org/TR/prov-o/ |
 rdf  | http://www.w3.org/1999/02/22-rdf-syntax-ns#  | https://www.w3.org/TR/rdf11-concepts/ |
| rdfs | http://www.w3.org/2000/01/rdf-schema#  | https://www.w3.org/TR/rdf-schema/ |
| sh  | http://www.w3.org/ns/shacl#  | https://www.w3.org/TR/shacl/ |
| tcs  | toolchain specification | the document you are currently reading |

<br><br>

## 2.2. Relation to other ontologies
- The [p-plan ontology](https://vocab.linkeddata.es/p-plan/index.html) is used for the *Pipeline Definition*. This is because the *Pipeline Definition* reflects a plan of a pipeline to be built and executed.
- The [dcat-ontology](https://www.w3.org/TR/vocab-dcat-3/) is used for the *Component Catalog* , which allows expressing Pipeline Components as resources in a catalog. 
- The [prov-ontology](https://www.w3.org/TR/prov-o/) is used to express provenance. 
<br><br>

## 2.3. Core Classes
<br>

### Catalog
![Component catalog](diagrams/component_catalog.svg)
| | |
|----------|----------|
| **Definition** | A Catalog is a collection of resources, such as Pipeline Components. Each Pipeline Component is a modular unit that can be instanced within a pipeline build. The Catalog stores information for each Pipeline Component, which is relevant for instancing. Such information are for example dependencies or constraints that come with including a pipeline component in a pipeline.
| **subclass of** | dcat:Catalog |
<br>

### Pipeline Definition
![Pipeline Definition](diagrams/pipeline_definition.svg)
| | |
|----------|----------|
| **Definition** | A Pipeline Definition describes the intented pipeline to be run. It is hence a plan for a pipeline that needs to be build. It is sufficient to define which Pipeline Components make part of a pipeline, to define their sequence, and to give Pipeline Components Configs to define their behavior. |
| **subclass of** | p-plan:Plan |
<br>

### Pipeline Generator
![Pipeline Generator](diagrams/pipeline_generator.svg)
| | |
|----------|----------|
| **Definition** | A Pipeline Generator compiles a system capable of executing a Pipeline Definition by looking up the Pipeline Components implicated in the pipeline. Based on a Pipeline Definition and the Catalog, it produces a Pipeline Build. Therefore it is up to the Generator to decide how the information in the Pipeline Definition and Component Catalog can be interpreted to produce a deployable pipeline. |
| **subclass of** | prov:SoftwareAgent |


### Pipeline Build
| | |
|----------|----------|
| **Definition** | A Pipeline Build reflects the pipeline that was generated by the Pipeline Generator, and which is ready for deployment. It consists of one or more DockerContainers, which instantiate the Pipeline Components and hence provide the environment for executing the pipeline. |
| **subclass of** | prov:SoftwareAgent |
<br>



## 2.4. Additional Classes
<br>

![Toolchain Model](diagrams/toolchain_model.svg)
### PipelineComponent
| | |
|----------|----------|
| **Definition** | A Pipeline Component is any modular unit that can be included in a pipeline to help execute a task. It may be a component which produces or transforms data. It may also be a component which is not responsible for forwarding data, but which is needed because another Pipeline Components depends on it. Pipeline Components are resources of the Catalog. Therefore, the need to be described with information relevant for their deployment, such as dependencies and constraints. |
| **subclass of** | --- |
<br>

### InstancePipelineComponent
| | |
|----------|----------|
| **Definition** | A InstancePipelineComponent is any component in a pipeline which acts on data. This can mean producing, transforming, consuming data (ETL) or a combination of these. t is a specialization of a Pipeline Component in that it is instanced by a Docker Container as part of a Pipeline Build. |
| **subclass of** | p-plan:Step |
<br>

### Config
| | |
|----------|----------|
| **Definition** | A Config is a set of input parameters for a Pipeline Component, which defines its behavior in a pipeline. In its simplest case, it is a set of key-value pairs. However, also nested configurations are possible. As a data structure a Config is equivalent to the "object" type in JSON, consisting of an unordered set of name/value pairs. The name is a string and the value is a string, number, boolean, array, or object (same concept as in the [Common Workflow Language](https://www.commonwl.org/v1.2/Workflow.html#Data_concepts)), resulting in an unordered, tree-like (acyclic) structure. Each Config requires either a tc:embedded or tc:literal-predicate. This tells the Pipeline Generator whether the Config is contained in the graph or must be parsed from a string-literal. If tc:embedded is used, the Config points to a blank node representing a data object: Predicates serve as keys and objects as values. If tc:literal is used, the Config points to a string of either json- or yaml-format. In this case the Pipeline Generator parses the string to extract the configuration data. A Config can be stored as a preset in the Component Catalog. In this case a Pipeline Component can point to its associated Configs via tc:storedConfig. This is ideal for attaching default configurations, or attaching configurations often needed for specific use cases. As part of an Assignment and hence Pipeline Definition (see below), tc:config can either point to one of these pre-made Configs or to a newly defined Config. 
|
| **subclass of** | --- |
<br>


### sh:NodeShape
| | |
|----------|----------|
| **Definition** | A NodeShape reflects a constraint, which needs to be validated whenever a Pipeline Component becomes part of a Pipeline Build. These constraints can concern different aspects of a Pipeline Build. For example, each Config assigned to a specific Pipeline Component may have to comply to a schema. A Pipeline Component may not accept data input from another Component, because it itself produces data. Or a Pipeline Component may pose specific constraints on how Pipeline Steps may be linked, such as expecting a strictly linear pipeline. It is also possible to describe constraints for the expected data input and output of Pipeline Components.Taken together, defining NodeShapes are a powerful tool to ensure that pipelines run as expected before being deployed. These constraints are expressed as SHACL-shapes to allow automatic validation. Each sh:NodeShape should have a sh:message for human readibility. These should clearly indicate and describe what kind of constraint is imposed, to allow easier implementation of the logic behind the constraint. Node Shapes are only indirectly linked to Pipeline Components via dcat:Relationship. This allows to further qualify the relationship between Pipeline Components and their constraints. For example, dcat:Role can be used to indicate that a constraint belongs to a specific category, for example concerning input data, output data or Configs. Dcat:Relationship may also be used to define when a constraint should be validated, for example before or after building the pipeline. |
| **subclass of** | --- |
<br>


### DockerContainer
| | |
|----------|----------|
| **Definition** | A DockerContainer is the core entity of a Pipeline Build. Each Pipeline Build consists of one or more DockerContainers, which are responsible for executing one or more Pipeline Steps. For this purpose, one or more Pipeline Components are instantiated within the DockerContainer. A DockerContainer is defined through several Configs, which are generated by the Pipeline Generator as part of the Pipeline Build. These Configs define how the DockerContainer should be build, started up, and configured for its task. 
| **subclass of** | --- |
<br>


## 2.5. Subclasses of Config

### DockerComposeConfig
| | |
|----------|----------|
| **Definition** | The DockerComposeConfig defines how the DockerContainer should be started up, for example which volumes should be mounted and which ports should be opened. |
| **subclass of** | tc:Config |
<br>

### PipelineConfig
| | |
|----------|----------|
| **Definition** | The PipelineConfig are those input parameters that the DockerContainer requires to execute the Pipeline Steps as intended.  |
| **subclass of** | tc:Config |
<br>

### DefaultConfig
| | |
|----------|----------|
| **Definition** | The Config that should be used for a PipelineComponent if not other Config is assigned in a PipelineDefinition.  |
| **subclass of** | tc:Config |
<br>

# 3. Examples

For a concrete example [check the catalog used by the pipeline generator](../pipeline%20generator/data/graph.ttl). 