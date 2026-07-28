"""Domain-service adapters used by the conversation module coordinator."""

from app.conversation.adapters.base import (
    ModuleAdapter,
    ModuleAdapterResult,
    PreparedModuleStart,
)
from app.conversation.adapters.exposure import ExposureModuleAdapter
from app.conversation.adapters.resource import ResourceModuleAdapter
from app.conversation.adapters.roleplay import RoleplayModuleAdapter
from app.conversation.adapters.worksheet import WorksheetModuleAdapter

__all__ = [
    "ExposureModuleAdapter",
    "ModuleAdapter",
    "ModuleAdapterResult",
    "PreparedModuleStart",
    "ResourceModuleAdapter",
    "RoleplayModuleAdapter",
    "WorksheetModuleAdapter",
]
