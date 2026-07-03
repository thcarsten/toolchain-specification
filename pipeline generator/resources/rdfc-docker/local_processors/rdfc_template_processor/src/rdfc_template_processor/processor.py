import logging
from dataclasses import dataclass
from logging import getLogger, Logger

import httpx

from rdfc_runner import Processor, Reader, Writer


# --- Type Definitions ---
@dataclass
class HttpOutArgs:
    reader: Reader  # Data in (required)
    writer: Writer  # Data out (optional)
    endpoint: str  # Foward data to (required)
    content_type: str  # for http header, e.g. "Content-Type: text/turtle" (optional)


# --- Processor Implementation ---
class HttpOut(Processor[HttpOutArgs]):
    logger: Logger = getLogger("rdfc.HttpOut")

    def __init__(self, args: HttpOutArgs):
        super().__init__(args)
        self.logger.debug(msg="Created HttpOut with args: {}".format(args))

    async def init(self) -> None:
        """This is the first function that is called (and awaited) when creating a processor.
        This is the perfect location to start things like database connections."""
        self.client = httpx.AsyncClient(timeout=None)
        self.logger.debug("Initializing TemplateProcessor with args: {}", self.args)
        self.headers = (
            {"Content-Type": self.args.content_type} if self.args.content_type else {}
        )

    async def transform(self) -> None:
        """Function to start reading channels.
        This function is called for each processor before `produce` is called.
        Listen to the incoming stream, push them to a http endpoint, and optionally forward to the outgoing stream.
        """

        self.logger.debug("Starting to forward messages to http endpoint")

        async for msg in self.args.reader.strings():
            # Send a http post request per incoming message
            try:
                response = await self.client.post(
                    self.args.endpoint,
                    content=msg,
                    headers=self.headers,
                )
                response.raise_for_status()
            except Exception as e:
                self.logger.error(f"Failed to send message: {e}")

            # Echo the message to the writer
            if self.args.writer:
                await self.args.writer.string(msg)

        # Close the writer after processing all messages
        if self.args.writer:
            await self.args.writer.close()
        self.logger.debug("done reading so closed writer.")

    async def produce(self) -> None:
        """Function to start the production of data, starting the pipeline.
        This function is called after all processors are completely set up."""
        pass
