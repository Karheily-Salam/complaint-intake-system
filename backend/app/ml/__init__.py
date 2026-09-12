"""Machine-learning components.

Everything in this package *assists* the deterministic workflow: it produces
scored signals (a complaint-type prediction, evidence for an extracted value,
an embedding, a similarity, an incident alert) and never changes ticket state,
merges tickets or sends email on its own. The conversation engine, the schema
registry and support staff remain the decision makers.

Runtime dependencies are deliberately small - numpy only - so the models run
inside the existing backend container on a 1-vCPU host. Heavier optional
backends (the ONNX embedding model) are loaded only when configured.
"""
