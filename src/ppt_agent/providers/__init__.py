"""External model provider contracts and adapters."""

from ppt_agent.providers.ark import ARK_BASE_URL, ArkMultimodalAdapter
from ppt_agent.providers.catalog import DEFAULT_MODEL_CATALOG, ModelCatalog

__all__ = [
    "ARK_BASE_URL",
    "ArkMultimodalAdapter",
    "DEFAULT_MODEL_CATALOG",
    "ModelCatalog",
]
