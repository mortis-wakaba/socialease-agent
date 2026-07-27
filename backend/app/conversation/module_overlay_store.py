"""Encrypted cache-aside storage for rebuildable module overlays."""

from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64DecodeError
import os

from pydantic import BaseModel, ConfigDict, Field

from app.conversation.content_protector import (
    ConversationContentProtectionError,
    ConversationContentProtector,
    ProtectedContent,
    configured_content_protector,
)
from app.memory.runtime_requirements import task_state_runtime_report
from app.memory.redis_settings import redis_task_state_settings
from app.memory.task_state_store import (
    DisabledTaskStateStore,
    RedisTaskStateStore,
    TaskStateStore,
    TaskStateStoreUnavailable,
)
from app.models_conversation import ModuleRun
from app.models_module_overlay import ModuleOverlay


class ProtectedModuleOverlay(BaseModel):
    """Encrypted Redis envelope whose metadata contains no conversation text."""

    model_config = ConfigDict(extra="forbid")

    run_version: int = Field(ge=1)
    plaintext: str | None = None
    ciphertext: str | None = None
    nonce: str | None = None
    key_version: str | None = None
    version: int = Field(ge=1)


class ModuleOverlayStore:
    """Cache module projections while keeping domain records authoritative."""

    def __init__(
        self,
        *,
        store: TaskStateStore[ProtectedModuleOverlay],
        protector: ConversationContentProtector,
        ttl_seconds: int = 3600,
    ) -> None:
        self._store = store
        self._protector = protector
        self._ttl_seconds = min(max(ttl_seconds, 60), 86_400)

    async def get(self, run: ModuleRun) -> ModuleOverlay | None:
        """Return one matching owner-scoped overlay, or miss safely."""
        try:
            envelope = await self._store.get(
                user_id=run.user_id,
                task_id=run.module_run_id,
            )
            if envelope is None or envelope.run_version != run.version:
                return None
            overlay = self._recover(envelope, run)
            _validate_overlay_scope(overlay, run)
            return overlay
        except (
            Base64DecodeError,
            ConversationContentProtectionError,
            TaskStateStoreUnavailable,
            ValueError,
        ):
            return None

    async def put(self, run: ModuleRun, overlay: ModuleOverlay) -> None:
        """Best-effort cache one validated projection."""
        _validate_overlay_scope(overlay, run)
        protected = self._protector.protect(
            overlay.model_dump_json(),
            associated_data=_associated_data(run),
        )
        envelope = ProtectedModuleOverlay(
            run_version=run.version,
            plaintext=protected.plaintext,
            ciphertext=(
                urlsafe_b64encode(protected.ciphertext).decode("ascii")
                if protected.ciphertext is not None
                else None
            ),
            nonce=(
                urlsafe_b64encode(protected.nonce).decode("ascii")
                if protected.nonce is not None
                else None
            ),
            key_version=protected.key_version,
            version=run.version,
        )
        try:
            await self._store.put(
                user_id=run.user_id,
                task_id=run.module_run_id,
                state=envelope,
                ttl_seconds=self._ttl_seconds,
            )
        except TaskStateStoreUnavailable:
            return None

    async def delete(self, run: ModuleRun) -> None:
        """Remove one module cache entry."""
        await self._store.delete(
            user_id=run.user_id,
            task_id=run.module_run_id,
        )

    async def delete_user(self, *, user_id: str) -> int:
        """Remove every module overlay cached for one owner."""
        return await self._store.delete_user(user_id=user_id)

    async def health(self) -> bool:
        """Return whether the configured cache backend responds."""
        if self._store.backend_name == "disabled":
            return True
        return await self._store.ping()

    async def close(self) -> None:
        """Close the underlying task-state client."""
        await self._store.close()

    def _recover(
        self,
        envelope: ProtectedModuleOverlay,
        run: ModuleRun,
    ) -> ModuleOverlay:
        protected = ProtectedContent(
            plaintext=envelope.plaintext,
            ciphertext=(
                urlsafe_b64decode(envelope.ciphertext)
                if envelope.ciphertext is not None
                else None
            ),
            nonce=(
                urlsafe_b64decode(envelope.nonce)
                if envelope.nonce is not None
                else None
            ),
            key_version=envelope.key_version,
        )
        raw = self._protector.recover(
            protected,
            associated_data=_associated_data(run),
        )
        return ModuleOverlay.model_validate_json(raw)


def create_module_overlay_store() -> ModuleOverlayStore:
    """Create the shared Redis overlay cache, or an explicit disabled store."""
    report = task_state_runtime_report()
    settings = redis_task_state_settings()
    task_store: TaskStateStore[ProtectedModuleOverlay]
    if report.redis_url:
        task_store = RedisTaskStateStore(
            redis_url=report.redis_url,
            namespace="module-overlay",
            model_type=ProtectedModuleOverlay,
            socket_timeout_seconds=settings.socket_timeout_seconds,
        )
    else:
        task_store = DisabledTaskStateStore()
    return ModuleOverlayStore(
        store=task_store,
        protector=configured_content_protector(),
        ttl_seconds=_overlay_ttl_seconds(),
    )


def _overlay_ttl_seconds() -> int:
    raw = os.getenv("MODULE_OVERLAY_CACHE_TTL_SECONDS", "3600")
    try:
        return int(raw)
    except ValueError:
        return 3600


def _associated_data(run: ModuleRun) -> bytes:
    return (
        "module-overlay:"
        f"{run.user_id}:{run.conversation_id}:{run.module_run_id}:{run.version}"
    ).encode("utf-8")


def _validate_overlay_scope(overlay: ModuleOverlay, run: ModuleRun) -> None:
    if (
        overlay.user_id != run.user_id
        or overlay.conversation_id != run.conversation_id
        or overlay.module_run_id != run.module_run_id
        or overlay.module_type != run.module_type
        or overlay.parent_module_run_id != run.parent_module_run_id
        or overlay.version != run.version
    ):
        raise ValueError("module overlay does not match its durable run")
