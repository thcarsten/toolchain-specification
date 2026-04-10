from .data_tree import DataTree


class Compiler:
    """
    Superclass for all compilers used by the Pipeline Generator.
    A Compiler is defined as a service which produces an input based on an output.
    By default both input and output are expected to be one DataTree.
    The function which produces the output based on the input is called 'compile'.
    The compile function is always defined in the child class, here it is just a dummy where input is copied to output.
    Both input and output can be described by a JsonSchema.
    This allows to track and validate the expected in- and outputs of compilers on a case by case basis.
    The compile_and_validate function always returns the output after compiling and validating.

    This makes the creation of child compiler classes straightforward. An example:
    class LdioCompiler(Compiler):
        def __init__(self, input : DataTree):
            super().__init__() # Inheriting the init of the parent class
            self.input = input # Overwriting the empty input with the one used to initialize the LdioCompiler
            self.input_schema {type : object, properties : ... required: ...} # Define the schema the input has to comply to
            self.output_schema: Define the output schema that you expect the LdioCompiler to produce

        def generate_output(self):
            ... # Overwrite the dummy compile function here: Implement the compile logic of your compiler

    -> That's it! Essentially for each compiler you define the expected input, output and compilation logic to transform input to output.

    Lets take another example:

    class PipelineExtractor(Compiler):
        def __init__(self, pipeline_id, graph_reader):
            super().__init__() # Inheriting the init of the parent class
            self.output_schema: Define the output schema that you expect the PipelineExtractor to produce
            self.pipeline_id = pipeline_id
            self.graph_reader = graph_reader

        def generate_output(self):
            pipeline_graph = self.graph_reader.extract_subgraph ( ... )
            pipeline_tree = DataTree(pipeline_graph.to_dict ...
            pipeline_tree.rename(old_key, new_key)
            self.output = pipeline_tree

    -> That's it! Although no input is defined, compile_and_validate will work just fine. input auto-validates if left empty,
    so no need to awkwardly trying to define and validate the graph input. The advantage is here that the output produced
    by the PipelineExtractor can still be defined and validated in the same way as other compilers. You can also imagine a
    compiler that has an input but not output, for example a FileSaver. It would save a received input to file, but not return
    or validate an output.

    A couple more remarks:
        - You are not forced to define input and output schemas. The default input and output schemas always validate to true.
          Add schemas for better documentation and debugging. You are also not forced to define inputs and outputs.
        - failed validations result in an Exception, so issues are catched early
        - compilers have max one single input and output by design. This is intentional to force a separation of responsibility:
          There should be one compiler child class per expected output schema.
          For multiple outputs of the same schema, simply make several instances of the same compiler.
        - Compilers force DataTrees as expected input / output by design.
          Input and output of compilers need to be predictable and commit to predefined schemas.
          This is needed to make chaining of many compilers feasible.
          As a data format, DataTrees should be flexible enough to support a wide range of use cases.
    """

    ###########################
    # Customize in child classes
    ###########################

    def __init__(self):
        self.input = DataTree({})
        self.output = DataTree({})
        self.input_schema = {}
        self.output_schema = {}

    def generate_output(self):
        self.output = self.input

    ###########################
    # Do not touch in child classes
    ###########################

    def validate_input(self):
        self.input.validate(self.input_schema)

    def validate_output(self):
        self.output.validate(self.output_schema)

    def compile(self):
        self.validate_input()
        self.generate_output()
        self.validate_output()
        return self.output
