import { Processor } from "@rdfc/js-runner";
import jsonld from "jsonld";

export class JsonLdToNQuads extends Processor {
  async init() {}

  async transform() {
    for await (const msg of this.reader.strings()) {
      try {
        const doc = JSON.parse(msg);
        const nquads = await jsonld.toRDF(doc, { format: "application/n-quads" });
        await this.writer.string(nquads);
      } catch (err) {
        this.logger.error(`Failed to convert JSON-LD payload to RDF: ${err}`);
      }
    }
    await this.writer.close();
  }

  async produce() {}
}
