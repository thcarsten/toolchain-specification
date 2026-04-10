# Table Of Contents

[1. Introduction](#1-introduction) <br>
[2. Installation](#2-installation) <br>
[3. Workflow](#3-workflow) <br>
[4. Packages](#4-packages) <br>
[5. Future directions](#5-future-directions) <br>
[6. How To](#6-how-to) <br>

## 1. Introduction
In this repo you find the codebase for the tool “pipeline generator”. As the name suggests, the pipeline generator automatically generates pipelines based on a semantic description of a pipeline. The pipeline generator accepts pipeline definitions which are written in RDF and follow the [semantic model](https://github.com/thcarsten/toolchain-specification/tree/main/semantic%20model) of the toolchain specification. Based on the pipeline definition, it looks up components and their dependencies in a component catalogue, and builds docker containers to resolve these dependencies. It also generates the configuration files necessary to run the pipelines. In the [data-folder](https://github.com/thcarsten/toolchain-specification/tree/main/pipeline%20generator/data), you can find the pipeline definitions and the component catalogue used for [the demo](https://github.com/thcarsten/toolchain-specification/tree/main/pipeline%20generator/src). Currently two frameworks are supported, RDF Connect and LDIO. 

The codebase is found in the [src- folder](https://github.com/thcarsten/toolchain-specification/tree/main/pipeline%20generator/src). It consists of two packages, rdf_extract and compilers. The package "rdf_extract" contains all classes for extracting, transforming and validating graph data effectively (rdf_extract is build on top of [rdflib](https://rdflib.readthedocs.io/en/stable/)). The package "compilers" contains the building blocks for the pipeline generator: Each compiler generates a "DataTree", which is a class based on [Json-LD](https://json-ld.org/). DataTrees can be serialized as json, yaml or turtle, and can be treated as graphs or dictionaries. This allows to use one single in- and output format across different compilers and frameworks. 

## 2. Installation
Install all dependencies contained in the requirements-file. Use this command (adjust the filepath as needed): 
```
pip install -r /path/to/requirements.txt
```

## 3. Workflow
The notebook demo.ipynb gives an overview of the workflow for using the pipeline generator:
- instantiate a new GraphReader and load all data contained in the Component Catalogue and Pipeline Definitions from the data-folder.
- Use the PipelineExtractor to extract all data concerning one single pipeline definition.
- Use either the RdfcConfigCompiler or the LdioConfigCompiler to generate a configuration file corresponding to the pipeline definition.

In the future it will be possible to use a generic "PipelineGenerator"-compiler, which automatically invokes the different compilers based on the Pipeline Definition. However this is a work in progress and hence not implemented yet. For more details, see below

## 4. Packages
The package "rdf_extract" contains the following classes:
- **GraphReader**: Loads a RDF graph into memory to interact with it. Can execute queries, rename nodes, extract subgraphs, and output triples as dataframes or dictionaries with native Python data types. 
- **PrefixStore**: Stores prefixes and their urls as key-value pairs to support various operations based on prefixes (e.g. append prefixes to queries, drop prefixes from keys in dictionaries, etc.)
- **DataTree**: Class based on Json-Ld, which allows to express data as graphs or dictionaries interchangably. Subsets of the data can be selected, keys (or values) can be transformed. Supports various serializations (yaml, json, turtle).
- **Compiler**: Generic class that either takes a DataTree as input, returns a DataTree as output, or both. Schemas for expected input and output can be optionally provided for automatic validation of the transformation step. 

The package "compilers" contains the following classes:
- **PipelineExtractor**: Extracts data of one Pipeline Definition by looking up all associated components and their dependencies in the component catalogue. Returns this data as DataTree.
- **LdioConfigCompiler**: Takes a DataTree describing a pipeline of the LDIO framework and compiles the corresponding configuration file.
- **RdfcConfigCompiler**: Takes a DataTree describing a pipeline of the RDF Connect framework and compiles the corresponding configuration file.

## 5. Future directions 
### 5.1. Goals
The pipeline generator is not fully implemented yet, it is a work in progress. We are but a few steps away from generating interoperable pipelines. We have the following goals for the year 2026:
1. Add support for Pipeline Definitions spanning components of both the RDF Connect and LDIO framework. This warrants automatic generation of interoperable pipelines. 
2. Add another framework, [semantic.works](https://semantic.works/). 
3. Iterate over the [semantic model]((https://github.com/thcarsten/toolchain-specification/tree/main/semantic%20model)) used for Pipeline Definitions and the Component Catalogue: Better consistency, better alignment with other ontologies, add new features (for example configuring which dependency should be used, if several dependencies can provide support for a component).
4. Provide a [user interface](#53-frontend) for browsing the component catalogue and creating Pipeline Definitions.
5. Add [automatic validation](https://pypi.org/project/pyshacl/) of pipelines. 
6. Add unit testing: The code base has not been thoroughly tested, so expect bugs.

### 5.2. Achieving interoperability
Concretely, we can achieve our first goal of generating interoperable pipelines by writing a couple more compilers:
- **PipelineSegmenter**: Looks up whether a Pipeline Definition contains segments to be covered by different microservices. If so, generates a DataTree describing the microservices and the pipeline segments they ought to cover. Also includes a new Pipeline Definition per microservice (to be fed to downstream compilers). 
- **PipelineDockerComposeCompiler**: Takes the output of the PipelineSegmenter and compiles a DockerCompose file based on the description of the different microservices.
- **RdfcDockerFileCompiler**: Takes a DataTree describing a pipeline of the RDF Connect framework and generates the Dockerfile needed to run this pipeline. This allows to include only those dependencies in a docker container which are actually used in the pipeline (currently I use a generic RDF Connect Docker container that includes most RDF Connect components). Should also support components that are stored locally rather than PyPi. 
- **ProjectFolderBuilder**: Takes a DataTree containing all files to be created (currently: Dockerfiles, Docker-compose files and configuration files), serializes them in the required format, and writes them to a folder with the expected filepaths and filenames. Should also copy expected resources to that project folder (such as locally stored RDF Connect components). 
- **Validator**: Looks up all constraints (SHACL shapes) associated with components used in a pipeline and validates them.
- **SemanticModelVersionMapper**: Can map from one version of the semantic model to the internal model that is used by the pipeline generator. 
- **PipelineGenerator**: Uses all previously described compilers to route the compilation flow for generating a pipeline project folder based on a Pipeline Definition. 

### 5.3. Frontend
Akin to the [model-view-controller pattern](https://en.wikipedia.org/wiki/Model%E2%80%93view%E2%80%93controller), a frontend should provide a user interface that provides a visual representation of the Pipeline Definition and Component Catalog. Part of the automatic validation should happen here: Web forms should be generated based on the expected configuration of a pipeline component. Linking incompatible pipeline components with each other should prompt a warning. 

Multiple views should be provided. At the very least, I would provide both a user interface that shows a pipeline as a [directed acyclic graph](https://www.geeksforgeeks.org/dsa/introduction-to-directed-acyclic-graph/), and a more traditional text-view, which allows to write Pipeline Definitions in RDF directly. Examples are [RDF playground](https://rdfplayground.dcc.uchile.cl/) or [SHACL playground](https://shacl-playground.zazuko.com/). These views may be intermixed or changed on click.There should be a console output which displays the sh:message for validations that failed. Ideally, the target of the corresponding [NodeShape](https://www.w3.org/TR/shacl/#node-shapes) should be highlighted. 

It should be possible to trigger the Pipeline Generator in the frontend.
<br><br>


## 6. How To

### 6.1. How to write your own Pipeline Definition
You can find a couple of examples in the pipeline.ttl- file in the data folder. A Pipeline Definition is as simple as a bunch of PipelineSteps, linked to each other. Each PipelineStep receives a Config and is carried out by a component of the Component Catalog. So you do not need a lot to write a Pipeline Definition. This part will be made easier in the future by providing a frontend.  


### 6.2. How to onboard your own components to the catalogue
Check the Component Catalog in the data folder for examples. At a minimum, a Pipeline Component needs either a MicroserviceConfig or a dependency to a component with a MicroserviceConfig. The assumption is that each MicroserviceConfig resolves all dependencies of components attached to it, including itself. Depending on the framework, you may also need to include framework-specific properties. For example, the LdioConfigCompiler needs to be able to look up ldio:type and rdf:label, whereas the RdfcConfigCompiler needs to be able to look up owl:imports. Everything else is optional, but it is a good idea to include as many NodeShapes as possible: It serves as a lookup reference for the constraints that need to be fullfilled once a component is included in a pipeline. These constraints are automatically validated by the pipeline generator.


### 5.3. How to onboard new frameworks
- Describe pipeline components with the semantic model and add them to the component catalogue. 
- Write compilers for your new framework that can produce the expected output files. 
- Test the new compilers based on a new Pipeline Definition written to exclusively include components of the new framework.
- Update the PipelineGenerator to route to your new compilers as needed. Currently this is done via glue-code (if-statements). For the future a better solution should be found for this, such as dependency injection (i.e. a PipelineGenerator can be instantiated with different sets of compilers, depending on which ones are required for a given Pipeline Definition). 


### 5.4. How to deal with version changes of the semantic model
You will have to create a new version of the SemanticModelVersionMapper to convert the semantic model to the internal model expected by the pipeline generator. The compiler class is designed to make this process a little bit easier: Each compiler can be provided with json schemas for the expected input and outputs. This means that it is a good idea to give the SemanticModelVersionMapper an output schema of the required internal model. The input schema reflects the received updated semantic model. The compilation process hence has to convert the expected input to the expected output. Each time a compiler is used with provided schemas, it is automatically validated that inputs and outputs are as expected.


