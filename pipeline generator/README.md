# Table Of Contents

[1. Introduction](#1-introduction) <br>
[2. Installation](#2-installation) <br>
[3. Workflow](#3-workflow) <br>
[4. Future directions](#4-future-directions) <br>
[5. How To](#5-how-to) <br>

## 1. Introduction
In this repo you find the codebase for the tool “pipeline generator”. As the name suggests, the pipeline generator automatically generates pipelines based on a semantic description of a pipeline. The pipeline generator accepts pipeline definitions which are written in RDF and follow the [semantic model](https://github.com/thcarsten/toolchain-specification/tree/main/semantic%20model) of the toolchain specification. Based on the pipeline definition, it looks up components and their dependencies in a component catalogue, and builds docker containers to resolve these dependencies. It also generates the configuration files necessary to run the pipelines. In the [data-folder](https://github.com/thcarsten/toolchain-specification/tree/main/pipeline%20generator/data), you can find the pipeline definitions and the catalogue used for [the demo](https://github.com/thcarsten/toolchain-specification/blob/main/pipeline%20generator/src/demo.ipynb). Currently three frameworks are supported, RDF Connect, LDIO and semantic.works. 

The codebase is found in the [src- folder](https://github.com/thcarsten/toolchain-specification/tree/main/pipeline%20generator/src). It consists of two packages, rdfine and compilers. The package "rdfine" contains all classes for extracting and transforming and serializing graph data effectively. The package "compilers" contains the building blocks for the pipeline generator: They act on the input graph by compiling different aspects of a PipelineBuild, such as configuration files.

## 2. Installation
Install all dependencies contained in the requirements-file. Use this command (adjust the filepath as needed): 
```
pip install -r /path/to/requirements.txt
```

## 3. Workflow
The notebook demo.ipynb gives an overview of the workflow of the pipeline generator. In the future this will be wrapped into a PipelineGenerator-class.
- instantiate a new GraphReader and load all data contained in the Catalog.
- Use inference rules to enrich the data. 
- PipelineExtractor extracts all data concerning one single pipeline definition.
- PipelineAssembler assigns Docker Containers and Pipeline Components for the Pipeline Definition. 
- LdioConfigCompiler generates the config file for LDIO.
- RdfcConfigCompiler generates the config file for RDF-Connect.
- SemanticWorksCompiler integrates configurations into Docker Containers. 
- DockerComposeCompiler compiles DockerCompose -configuration for the pipeline.

## 4. Future directions 
The pipeline generator is not fully implemented yet, it is a work in progress. We have the following goals for the year 2026:

- [x] Add support for Pipeline Definitions spanning components of both the RDF Connect and LDIO framework. This warrants automatic generation of interoperable pipelines. 
- [x] Add another framework, [semantic.works](https://semantic.works/). 
- [x] PipelineAssembler: Looks up whether a Pipeline Definition contains segments to be covered by different microservices. If so, generates a DataTree describing the microservices and the pipeline segments they ought to cover. Also includes a new Pipeline Definition per microservice (to be fed to downstream compilers). 
- [x] DockerComposeCompiler: Compiles a DockerCompose file based on the description of the different microservices.
- [ ] ProjectFolderBuilder: Takes the semantic description of the PipelineBuild and writes them to a folder with the expected filepaths and filenames. Requires that the PipelineBuild contains all necessary information semantically described (filepaths and formats).
- [ ] CompilerAssigner: It may be necessary at some point to provide a lookup which compilers need to be called depending on information contained in the graph. So that compilers can be called dynamically based on need.
- [ ] SemanticModelVersionMapper: Can map from one version of the semantic model to the internal model that is used by the pipeline generator. Allows decoupling versioning of the official semantic model and the internal model used for implementation.
- [ ] RdfcDockerFileCompiler: Creates an adhoc Dockerfile for RDF-Connect. This allows to include only those dependencies in a docker container which are actually used in the pipeline (currently I use a generic RDF-Connect Docker container that includes most RDF Connect components).
- [ ] PipelineGenerator: Uses all previously described compilers to route the compilation flow for generating a pipeline project folder based on a Pipeline Definition. 


## 5. How To

### 5.1. How to write your own Pipeline Definition
You can find a couple of examples in the pipeline.ttl- file in the data folder. A Pipeline Definition is as simple as a bunch of InstancePipelineComponents, linked to each other. Each InstancePipelineComponent receives a Config and is carried out by a component of the Catalog. So you do not need a lot to write a Pipeline Definition. This part will be made easier in the future by providing a frontend.  


### 5.2. How to onboard your own components to the catalogue
Check the Catalog in the data folder for examples. At a minimum, a Pipeline Component needs either a DockerComposeConfig or a dependency to a component with a DockerComposeConfig. The assumption is that each DockerComposeConfig resolves all dependencies of components attached to it, including itself. Depending on the framework, you may also need to include framework-specific properties. For example, the LdioConfigCompiler needs to be able to look up ldio:type and rdf:label, whereas the RdfcConfigCompiler needs to be able to look up owl:imports. Everything else is optional, but it is a good idea to include as many NodeShapes as possible: It serves as a lookup reference for the constraints that need to be fullfilled once a component is included in a pipeline. 


### 5.3. How to onboard new frameworks
- Describe pipeline components with the semantic model and add them to the catalog. 
- Write compilers for your new framework that can produce the expected output files. 
- Test the new compilers based on a new Pipeline Definition written to exclusively include components of the new framework.
- Update the PipelineGenerator to route to your new compilers as needed. Currently this is done via glue-code (if-statements). For the future a better solution should be found for this, such as dependency injection (i.e. a PipelineGenerator can be instantiated with different sets of compilers, depending on which ones are required for a given Pipeline Definition), see CompilerAssigner above.


