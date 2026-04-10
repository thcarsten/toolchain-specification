from rdf_extract import Compiler, DataTree
import pandas as pd


class LdioConfigCompiler(Compiler):
    """
    Class to generate config file for LDIO.
    """

    def __init__(self, pipeline_tree: DataTree) -> None:
        super().__init__()  # Inheriting the init of the parent class
        self.input = pipeline_tree.copy()
        self.output = DataTree(
            {
                "name": "",
                "description": "",
                "input": {"adapter": {}},
                "transformers": [],
                "outputs": [],
                "@context": pipeline_tree.prefix_store.prefixes,
            }
        )

    def generate_output(self) -> None:
        # Creating a dataframe for simple retrieval of entry
        self.df_steps = self.fetch_steps()

        self.output["name"] = self.input["@id"]
        self.output["description"] = self.input["rdfs:comment"]

        for index, row in self.df_steps.iterrows():
            dict_processor = {"name": row["name"], "config": row["config"].to_dict()}
            processor_type = row["type"]
            if processor_type == "Input":
                self.output["input"].update(dict_processor)
            elif processor_type == "Adapter":
                self.output["input"]["adapter"].update(dict_processor)
            elif processor_type == "Transformer":
                self.output["transformers"].append(dict_processor)
            elif processor_type == "Output":
                self.output["outputs"].append(dict_processor)

        # Cleanup
        self.output.drop_empty()
        self.output.drop_prefixes()

    def fetch_steps(self) -> pd.DataFrame:
        list_steps = []
        for step_id in self.input.get("steps").to_dict().keys():
            component_id = self.input.get(f"steps.{step_id}.component")["@id"]
            ldio_type = self.input.get(f"components.{component_id}")["ldio:type"]
            ldio_label = self.input.get(f"components.{component_id}")["rdfs:label"]
            config = self.input.get(f"configs.{component_id}.config")
            dict_step = {
                "step": step_id,
                "processor": component_id,
                "type": ldio_type,
                "name": ldio_label,
                "config": config,
            }
            list_steps.append(dict_step)

        return pd.DataFrame(list_steps)
