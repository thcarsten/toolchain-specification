# Table Of Contents

- [3.1. High-level overview](#31-high-level-overview)
- [3.2. Future directions](#32-future-directions)




## 3.1. High-level overview

#### Classes 

The pipeline generator contains the following classes:
- **GraphReader**: Loads a RDF graph into memory to interact with it. Can execute queries, rename nodes, extract subgraphs, and output triples as dataframes or dictionaries. 
- **Pipeline**: A class holding all relevant data of one pipeline as dataframes, dictionaries or graphs for easy access. Holds information on processors, runners, configs, and steps.
- **LdioCompiler**: Takes a Pipeline instance holding a Ldio pipeline and compiles a corresponding LDIO config file and docker compose file for running the pipeline. 
- **RdfcCompiler**: Takes a Pipeline instance holding a RDF Connect pipeline and compiles a corresponding RDF Connect config file and docker compose file for running the pipeline. 

There is also a jupypter notebook called "demo" which demonstrates how the pipeline generator can make use of these classes. The demo also includes validation of the pipeline. 

#### Workflow
- instantiates a new GraphReader and loads all data contained in *Ontology.ttl*, *ComponentCatalogue.ttl* and *PipelineDefinition.ttl* in the data-folder.
- instantiates a new Pipeline object. Loads the data of one pipeline into the Pipeline object.
- validates all shapes that are associated with the Pipeline.
- depending on the Pipeline, uses either the LdioCompiler or RdfcCompiler to compile the required config and docker files.


## 3.2. Future directions 

#### Interoperable pipelines
The next step is to support compiling pipelines spanning multiple frameworks. A semantic.works compiler is planned as well. 


#### Frontend
Akin to the [model-view-controller pattern](https://en.wikipedia.org/wiki/Model%E2%80%93view%E2%80%93controller), a frontend should provide a user interface that provides a visual representation of the pipeline generation process. It should allow browsing pipelines and processors, allow to load and edit pipelines, and add own processors to the catalogues.  

Specifically, the Pipeline Definition should be visually represented as Nodes and Edges, and it should be possible to interactively change both the Config and the assigned Processor of a Node (representing a Pipeline Step). Edges should be changable as well. These user inputs should automatically translate to updates in the underlying RDF graph.

The list of Processors that can be selected should be populated by the Component Catalogue. Ideally, new user inputs should prompt validation (in the background) on the fly, so that timely feedback can be provided and suggestions can be made. For example, assigning a new Processor to a step may either invalidate a Pipeline Definition, or an associated Config, or limit which Processors can be used for subsequent Pipeline Steps.

It should be possible to add new Component Catalogues to the model by providing a link. The list of selectable Processors should be updated accordingly. 

Configs should be presented as forms by automatically converting their ConfigSchemas to web forms. If Configs are written as literals, a multiline-textfield can be presented instead. 

Multiple views should be provided. At the very least, I would provide both a user interface as a view, such as described above, and a more traditional text-view, which allows to write Pipeline Definitions in RDF directly. Examples are [RDF playground](https://rdfplayground.dcc.uchile.cl/) or [SHACL playground](https://shacl-playground.zazuko.com/). These views may be intermixed or changed on click.

There should be a console output which displays the sh:message for validations that failed. Ideally, the target of the corresponding NodeShape should be highlighted. 

It should be possible to trigger the Pipeline Generator in the frontend.
<br><br>

#### Monitoring 
As of now, the Pipeline Generator will only generate the files needed to run a pipeline, but not initiate the running of the pipeline itself. However an extension of this architecture could make this possible. Since the Pipeline Generator / backend is written in Python, the docker library in Python can be used to build and start docker containers and hence the pipeline directly. 

Since the frontend is already used to visualize the pipeline, it may also be used to visualize the monitoring of the pipeline once it runs. For this purpose, it is needed to fetch information from the Docker API: It will provide information basic monitoring information, like container health, logs (stdout/stderr), resource usage. This can indicate whether all Environments and initiated correctly. Fetching stdout and stderr in this way is convenient because it automatically fetches all logs that each Pipeline Component produces, no matter the framework it runs on. 

Ideally we would also want some information on the data throughput. This could be done by having the "glue"-Processors, i.e. the bridges that are interjected between docker containers to provide cross-container communication, provide an API for this. At the very least, it would allow capturing whether data goes in and out of each container, and hence whether the pipeline is running. 


