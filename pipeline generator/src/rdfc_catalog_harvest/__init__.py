"""Generate the RDF-Connect section of the component catalog.

The catalog entry for an RDF-Connect processor is almost entirely a
restatement of facts the package already publishes: its label, its
description, which runner it needs, which npm/PyPI package supplies it,
where its ``processor.ttl`` lands after install, and its SHACL parameter
shape. Transcribing that by hand is what produced the drift this package
removes.

The pipeline is two steps, split so the second one is reproducible:

.. code-block:: text

    catalog-rdfc-requests.ttl  --harvest-->  data/rdfc_harvest/  --generate-->  catalog-rdfc.ttl
      (hand-written: which        (network)   (committed        (offline)    (generated)
       package, which version)                 snapshot)

This is not a :class:`compilers.Compiler`. Those are graph-to-graph
transformations inside a single pipeline build; this runs *before* the
generator and crosses the boundary into the outside world, which puts it
in the same category as :class:`compilers.FileMaterializer`.

Scope is RDF-Connect only. LDIO and semantic.works components have no
machine-readable upstream definition to derive from, so their catalog
files stay hand-written.
"""

from .emitter import generate
from .harvester import harvest, harvest_one
from .model import CatalogRequest, HarvestRecord
from .requests import load_requests

__all__ = [
    "CatalogRequest",
    "HarvestRecord",
    "generate",
    "harvest",
    "harvest_one",
    "load_requests",
]
