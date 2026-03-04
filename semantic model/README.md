
# Table Of Contents

- [1. Overview](#1-overview)
    - [1.1. Introduction](#11-introduction)
    - [1.2 Term definitions](#12-term-definitions)

- [2. Ontology](#2-ontology)
    - [2.1. Namespaces](#21-namespaces)
    - [2.2. Relation to other ontologies](#22-relation-to-other-ontologies)
    - [2.3. Core Classes](#23-core-classes)
    - [2.4. Additional Classes](#24-additional-classes)
    - [2.5. Future Directions](#25-future-directions)

- [3. Examples](#3-examples)

<br><br>

# 1. Overview
## 1.1 Introduction
*Intention*: discoverability, replication, modular design, separation of concerns, ease of use 
<br><br>
The goal is to design a framework-agnostic, streaming-oriented pipeline orchestration system for Linked Data. Pipelines are described semantically using RDF, and execution is realized across multiple frameworks (currently LDIO, RDF Connect, semantic.works) using Docker containers and RDF Connect for orchestration. For this purpose, the *toolchain* ontology is developed.

The *toolchain* ontology allows to describe data pipelines for the purpose of replicability. Four Core Classes are established, each of which are concerned with different responsibilities. Providing a minimal **Pipeline Definition** is sufficient to allow replication of a pipeline. Pipelines are defined as a series of data transformation steps, which are executed by Pipeline Components. These Pipeline Components are collected in a **Component Catalogue**, which hence provides the resources for building a pipeline. A Pipeline Generator takes a Pipeline Definition and attempts to build an executable pipeline based on the resources at its disposal in the Component Catalogue. The **Pipeline Build** reflects the compiled pipeline and provides all information concerning instantiation of a pipeline in specific Environments.
<br><br>
Splitting the *toolchain* ontology into these four Core Classes leaves a clean separation of concerns: The Pipeline Definition allows the user to create new pipelines with minimal effort: It is sufficient to describe which Processors provide data to other Processors, and to provide Configs to configure each Processor used in the Pipeline. The Component Catalogue is based on the idea that each Pipeline Component is modular and should be easy to reuse. Hence, all information that is known about a PipelineComponent (and which may be relevant for a Pipeline Generator) should be included in the Component Catalogue. This allows a user to merely point to the Processor in the Component Catalogue when defining a particular Pipeline Step. Hence, essential details about the Processor do not need to be repeated with each new Pipeline Definition. Likewise, distinguishing between the Pipeline Definition and the Pipeline Build allows defining Pipelines without having to be  concerned with their implementation. It is up to the Pipeline Generator to figure out a feasible implementation of the pipeline, allowing to largely automate this process: Based on what is known about each Pipeline Component, the feasibility of the defined Pipeline can be evaluated and a corresponding Pipeline Build can be generated.  
<br>
Even if the Pipeline Generator is not utilized, the *toolchain* ontology provides a unified language for describing pipelines across different frameworks. This has several advantages. Pipelines and Pipeline Components become discoverable, allowing a community to grow around the idea of sharing modular resources for building pipelines. Datasets can be described by the pipelines that generated them, allowing full replication in research. Pipeline Definitions can be subjected to automated validation, so that the feasibility of pipelines does not have to be evaluated through trial and error. 
<br><br>

## 1.2 Term definitions

| Term | Definition |
| ----- | ----- |
| **Pipeline** | We understand a Pipeline as an arrangement of modular data transformation steps, whereby each step acts on data in a well-defined, configurable way, before forwarding the data to the next step. These steps can be arranged in a nonlinear way, similar to a directed graph: Steps can receive zero or more inputs and outputs. To qualify as Pipeline, all Steps have to be directly or indirectly chained to each other. |
| **Data** | Data is any kind of digital content that carries information of interest. This information can be extracted through transformation and made available through transportation, i.e. through Pipelines. As such, Data are the entities that are send through pipeslines to make information available at a target location. |
| **Config** | A Config defines the expected (transformation) behavior for each Step in a Pipeline. As such, it differs from Data in so far as it is not subject to transformation itself, does not provide much valuable information beyond the behavior within the Pipeline, and is also not send through the Pipeline. | 
| **Instance** | We understand instantiating Pipeline Components as installing or deploying these in Environments so that they are ready to run. | 
<br><br>


# 2. Ontology
## 2.1. Namespaces
| prefix |	namespace IRI | documentation |
|----------|----------|----------|
| tc  | toolchain  | the document you are currently reading |
| rdf  | http://www.w3.org/1999/02/22-rdf-syntax-ns#  | https://www.w3.org/TR/rdf11-concepts/ |
| rdfs | http://www.w3.org/2000/01/rdf-schema#  | https://www.w3.org/TR/rdf-schema/ |
| rdfc | https://w3id.org/rdf-connect# | https://rdf-connect.github.io/specification/ |
| p-plan  | http://purl.org/net/p-plan# | https://vocab.linkeddata.es/p-plan/index.html |
| prov  | http://www.w3.org/ns/prov# | https://www.w3.org/TR/prov-o/ |
| osw  | http://ontosoft.org/software#  | https://ontosoft-earthcube.github.io/ontosoft/ontosoft%20ontology/v1.0.1/doc/index.html |
| sosa | http://www.w3.org/ns/sosa/ | https://www.w3.org/TR/vocab-ssn/ |
| ssn | http://www.w3.org/ns/ssn/ | https://www.w3.org/TR/vocab-ssn/ |
| sh  | http://www.w3.org/ns/shacl#  | https://www.w3.org/TR/shacl/ |
| dcat  | http://www.w3.org/ns/dcat#  | https://www.w3.org/TR/vocab-dcat-3/ |
<br><br>

## 2.2. Relation to other ontologies
- For the *Pipeline Definition* the p-plan ontology is used. This is because the *Pipeline Definition* reflects a plan of a pipeline to be built and executed.
- For the *Component Catalogue* the osw-ontology is used, which allows extensive metadata annotation of software, and hence provides sufficient terms for defining PipelineComponents exhaustively. 
- For the *Pipeline Build* a mix of prov and sosa is used. Prov is used for describing the Pipeline Build as a prov:SoftwareAgent, which allows to define Pipeline Runs as Activities of the Pipeline Build. Sosa is used for describing the Pipeline Build as a sosa:System, which allows describing how this system may be hosted in different Environments (sosa:Platforms). 
- A link with dcat is made twice: A Pipeline Run is a prov:Activity that used and generated dcat:datasets, allowing to link datasets to the pipelines that generated them. A *Component Catalogue* is also a dcat:dataset by itself, providing a means for publishing and sharing Pipeline Components with a wider audience. 
<br><br>

## 2.3. Core Classes
<br>

### Pipeline Generator
| | |
|----------|----------|
| **Definition** | A Pipeline Generator takes a Pipeline Definition and one or more Component Catalogues as input and produces a Pipeline Build as output. In other words, a Pipeline Generator compiles a system capable of executing a Pipeline Definition based on the Components it has at its disposal as resources. It considers constraints (sh:NodeShapes) of each PipelineComponent to evaluate the feasibility of suggesting a Pipeline Build. As such it should be sufficient for a user to define a Pipeline Definition to arrive at a Pipeline Build capable of running the pipeline. A Pipeline Generator could hence automate the task of building a pipeline, making it sufficient for the user to formulate the intended pipeline. |
| **subclass of** | prov:SoftwareAgent |
<br>

### Pipeline Definition
| | |
|----------|----------|
| **Definition** | In its essence, a Pipeline Definition is a directed graph of planned PipelineSteps, each aimed to generate or transform data. For this purpose, each Pipeline Step points at a Processor, which is a Pipeline Component appointed to carry out the Pipeline Step. A Pipeline Step also receives a Config in order to define the expected behavior of the Processor during the Pipeline Step. A Pipeline Step can correspond to a Pipeline Definition, making nesting possible. <br><br> A Pipeline Definition declares intent; it is a plan of a pipeline to be run. |
| **subclass of** | p-plan:Plan |
<br>

### Component Catalogue
| | |
|----------|----------|
| **Definition** | A Component Catalogue is a collection of the PipelineComponents that can be instanced in order to create an Pipeline Build capable of executing a Pipeline Definition. Machine-readable installation instructions (thus steps needed for instancing the component) can be expressed as Dockerfiles. Dependencies between PipelineComponents can be expressed to ensure that instancing a PipelineComponent includes instancing the supporting Runners in the respective Environments. 
| **subclass of** |prov:Collection, dcat:Dataset |
<br>

### Pipeline Build
| | |
|----------|----------|
| **Definition** | An Pipeline Build is the description of a system capable of executing the Pipeline Definition. As such, a Pipeline Build is based on a PipelineDefinition (prov:hadPlan) and consists of one or more Environments. Each Environment is responsible for executing one or more PipelineSteps contained in the PipelineDefinition. Every Environment is associated with a Dockerfile that specifies the runtime environment required to execute the associated Pipeline Steps. An Environment may also be linked to one or more configs, which represent the compiled configurations of its Pipeline Steps. If a Pipeline Build contains more than one Environment, it must also be associated with a Docker Compose file, which defines how the different Docker containers (built from the respective Dockerfiles) are started and connected. In multi-segment setups, Environments may require Bridge Steps, which are special steps added at the beginning or end of a segment and executed by a regular Pipeline Component to enable communication between containers; for example, HTTP In and HTTP Out components can forward data across containers via HTTP. |
| **subclass of** |sosa:System, prov:SoftwareAgent |
<br>



## 2.4. Additional Classes
<br>

### PipelineStep
| | |
|----------|----------|
| **Definition** | See Pipeline Definition. |
| **subclass of** | p-plan:Step |


<br>

### Config
| | |
|----------|----------|
| **Definition** | A Config is a data structure equivalent to the "object" type in JSON, consisting of an unordered set of name/value pairs. The name is a string and the value is a string, number, boolean, array, or object (same concept as in the [Common Workflow Language](https://www.commonwl.org/v1.2/Workflow.html#Data_concepts)). Defining a Config in this way allows for both simple configuration of Pipeline Steps (a simple set of parameter names and their values), as well as complex, nested configurations. <br><br> A Config can be serialized in different formats, and this format can differ from the format that is compiled during runtime. As of now, we support RDF as format, JSON and yaml are planned. If the Config is serialized as RDF, it can directly be embedded in the RDF graph that makes up the Component Catalogue: Using the "embedded"-predicate, the Config should point to a blank node representing a data object: Field-names serve as predicates and field-values as objects. The subgraph originating from the root blind node must be acyclic to later allow conversion between formats. If the Config is serialized as JSON or yaml, it cannot be embedded in the RDF graph. In this case, the predicate "external" should either point to a url that is dereferencable. Alternatively, the predicate "literal" should point to a string which is of the corresponding format. A Config should also declare the serialization format it uses (json, yaml), although this may be inferred. |
| **subclass of** | p-plan:Variable |


<br>

### Processor
| | |
|----------|----------|
| **Definition** | A Processor is any modular unit that can generate or transform data. To allow easy reuse, the Component Catalogue has to provide enough information about the Processor so that it can be instantiated as part of the Pipeline Build based on the provided information. |
| **subclass of** | tc:PipelineComponent |


<br>

### Runner
| | |
|----------|----------|
| **Definition** | A Runner is any modular piece of software needed as instance in the Pipeline Build to run a pipeline, but not responsible for transforming and forwarding data. It is needed in the Pipeline Build because either a Processor or another Runner depends on it. |
| **subclass of** | tc:PipelineComponent |


<br>

### PipelineComponent
| | |
|----------|----------|
| **Definition** | A Pipeline Component is a superclass of Processors and Runners. |
| **subclass of** | osw:Software |


<br>

### sh:NodeShape
| | |
|----------|----------|
| **Definition** | In order to evaluate the feasibility of turning a Pipeline Definition into an Pipeline Build, the PipelineGenerator has to know which constraints instantiated Pipeline Components add to a Pipeline Build. This is what sh:NodeShapes are for. NodeShapes can express the kinds of data that a Processor can transform and the output it produces. NodeShapes also allow to express mandatory and optional fields of a Config that a Processor can interpret. Processors may also have specific constraints about the relationships they allow with other processors (via isPrecededBy). For example, an "API-call"-processor may not allow data input through a preceding Step, because it fetches new data from a remote source. <br><br> Taken together, defining NodeShapes are a powerful tool to formulate explicit constraints that modular pipeline components impose when instantiated. These constraints can be validated to evaluate the feasibility of creating a Pipeline Build. They can also form a basis for the Pipeline Generator to reason about viable Pipeline Builds given the Pipeline Definition and Component Catalogue. These constraints are expressed as SHACL-shapes to allow automatic validation. Each Constraint should have a sh:message for human readibility. These should clearly indicate and describe what kind of constraint is imposed, to allow easier implementation of the logic behind the constraint.
| **subclass of** | --- |


<br>

### Environment
| | |
|----------|----------|
| **Definition** | An Environment reflects the Runtime Environment(s) in which Pipeline Components are instantiated to prepare for a Pipeline Run. As such an Environment is mainly defined through its Dockerfile, which is machine-readible way of defining this runtime environment. |
| **subclass of** | sosa:Platform |



<br>

### Bridge Step
| | |
|----------|----------|
| **Definition** | These are Pipeline Steps inserted as part of a Pipeline Build, within a specific Environment. They handle the communication across Environments and are hence inserted at the beginning or end of a segment of Pipeline Steps contained in an Environment. |
| **subclass of** | --- |

<br>



### Dockerfile
| | |
|----------|----------|
| **Definition** | A Dockerfile provides machine-readable instructions on how to instantiate a Pipeline Component (or Environment consisting of PipelineComponents). |
| **subclass of** | osw:TextEntity |
<br>

### DockerComposeFile
| | |
|----------|----------|
| **Definition** | Describes a Pipeline Build as a whole, i.e. how different Environments are spinned up together to run a pipeline. |
| **subclass of** | osw:TextEntity |
<br>


## 2.5. Future Directions 
- In this model, pipelines are only described as concrete instances, not as reusable templates. In my view, a pipeline automatically becomes reusable by simply changing the used data and potentially some configuration values. But some people may see that differently and may want distinction of class and instance for pipelines. 
- We may want to be able to express the state of a pipeline run while it is still ongoing. This remains to be discussed, because in the current scope the rdf graph is only used to compile a pipeline build, but not used to monitor the pipeline progress. 
- We may not always want to build each PipelineComponent, some PipelineComponents may already be running. We have to think more about how to express this and how this would work in practice. 
- We may want to be able to express that a PipelineComponent requires one of several runners.
- We may want to be able to provide hints to the Pipeline Generator for the Pipeline Build we want. For example, saying that Pipeline Components of the same framework should be put in the same environment. Or that a Channel should be configured in a specific way.  
- Config of runners: The ontology supports that Runners can have Configs, but currently there is no way for the user to define the Config of Runners. This is because Runners do not make part of the Pipeline Definition. 
- The shape of data inputs and outputs can be defined via NodeShapes, however currently the ontology does not include Data entities. I honestly am not sure how a Data entity could be defined for pipelines that stream data, unless the stream is seen as a data dump that is processed gradually over time.  
<br>



# 3. Examples

Description of a simple LDIO pipeline:
~~~
:LdioExamplePipeline a tc:PipelineDefinition;
    rdfs:label "LDIO Example Pipeline";
    rdfs:comment "A simple example pipeline using three processors and one runner of the LDIO framework for the pipeline generator PoC.".

:LDESClientStep a tc:PipelineStep;
        p-plan:isStepOfPlan :LdioExamplePipeline;
        tc:toBeCarriedOutByProcessor ldio:LdesClient;
        p-plan:hasInputVar :LdesClientConfig .

:ConsoleOutStep a tc:PipelineStep;
        p-plan:isStepOfPlan :LdioExamplePipeline;
        tc:toBeCarriedOutByProcessor ldio:ConsoleOut;
        p-plan:hasInputVar :ConsoleOutConfig .
    
:ConsoleOutStep p-plan:isPrecededBy :LDESClientStep .

:LdesClientConfig a tc:Config;
    tc:embedded [
        :urls "https://ca-westtoerwin-nginx-prod.livelyisland-1fa58ea1.westeurope.azurecontainerapps.io/touristattractions/latestView";
        :retries [ :enabled true] ;
    ] .

:ConsoleOutConfig a tc:Config;
    tc:embedded [
        :rdf-writer 
            [:content-type "text/turtle"] ;
    ] .
~~~

Based on this pipeline, the pipeline generator looks up the references pipeline components in the component catalogue:
~~~
:LdioComponentCatalogue a tc:ComponentCatalogue;
    prov:hadMember 
            ldio:LdesClient, 
            ldio:SparqlConstructTransformer,
            ldio:ConsoleOut,
            ldio:LinkedDataInteractionsOrchestrator .


ldio:ConsoleOut a tc:Processor ;
    a tc:Processor ;
    rdfs:label "Ldio:ConsoleOut" ; 
    osw:hasDependency ldio:LinkedDataInteractionsOrchestrator ;
    ldio:type "Output" ;
    osw:hasUseLimitations :LdioConsoleOutConfigShape .

:LdioConsoleOutConfigShape
        a sh:NodeShape ;
        sh:target [ # I express here that the tc:Config needs to belong to ldio:ConsoleOut
        	a sh:SPARQLTarget ;
            sh:prefixes :prefixes ;
        	sh:select """
            		SELECT ?this
            		WHERE {
                		?step tc:toBeCarriedOutByProcessor ldio:ConsoleOut .
                		?step p-plan:hasInputVar ?this .
                		?this a tc:Config .
            			}
        		  """ ;
    		  ] ;
        sh:property [
            sh:path tc:embedded ;
            sh:node [
                a sh:NodeShape ;
                sh:property [
                sh:path ( :rdf-writer :content-type ) ;
                    sh:datatype xsd:string ;
                    sh:minCount 0 ;
                    sh:maxCount 1 ;
                    sh:message "content-type may have max one value of type string." ;
                ] ;
            ] ;
        ] . 

(...)
~~~

