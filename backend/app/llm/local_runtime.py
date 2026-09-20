"""Local inference model residency, warm-up, and readiness lifecycle."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
import os
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.llm.factory import LLMConfig, local_llm_capacity_snapshot


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class LocalRuntimeSettings:
    """Operational settings for a managed local inference runtime."""

    enabled: bool
    required: bool
    runtime: str
    base_url: str | None
    api_key: str | None
    model: str | None
    keep_alive: str
    probe_timeout_seconds: float
    recovery_interval_seconds: float
    server_slots: int

    @classmethod
    def from_env(cls) -> "LocalRuntimeSettings":
        config = LLMConfig.local_from_env()
        runtime = os.getenv("LOCAL_LLM_RUNTIME", "auto").strip().casefold()
        if runtime == "auto":
            if "11434" in (config.base_url or ""):
                runtime = "ollama"
            elif "8080" in (config.base_url or ""):
                runtime = "llama_server"
            else:
                runtime = "external"
        managed = runtime in {"ollama", "llama_server"}
        return cls(
            enabled=config.enabled and managed,
            required=_env_bool(
                "LOCAL_LLM_READINESS_REQUIRED",
                config.enabled and managed,
            ),
            runtime=runtime,
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            keep_alive=os.getenv("LOCAL_LLM_KEEP_ALIVE", "-1").strip() or "-1",
            probe_timeout_seconds=max(
                0.5,
                float(os.getenv("LOCAL_LLM_READINESS_TIMEOUT_SECONDS", "30")),
            ),
            recovery_interval_seconds=max(
                1.0,
                float(os.getenv("LOCAL_LLM_RECOVERY_INTERVAL_SECONDS", "10")),
            ),
            server_slots=_configured_server_slots(runtime),
        )


class LocalLLMRuntime:
    """Keep a local model resident and expose inference-level health."""

    def __init__(self, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._client = http_client
        self._owns_client = http_client is None
        self._maintenance_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._inference_verified = False
        self._resident = False
        self._last_error: str | None = None
        self._warmed_at: str | None = None
        self._observed_slots: int | None = None

    async def start(self) -> None:
        """Warm the configured model once and start bounded recovery probes."""
        settings = LocalRuntimeSettings.from_env()
        if not settings.enabled:
            return
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=settings.probe_timeout_seconds,
                trust_env=False,
            )
        await self._ensure_ready(settings)
        self._maintenance_task = asyncio.create_task(self._maintain(settings))

    async def close(self) -> None:
        """Stop recovery probes and release the local HTTP connection pool."""
        if self._maintenance_task is not None:
            self._maintenance_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._maintenance_task
            self._maintenance_task = None
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def health(self) -> dict[str, object]:
        """Return model residence, inference verification, and limiter capacity."""
        settings = LocalRuntimeSettings.from_env()
        capacity = local_llm_capacity_snapshot()
        if not settings.enabled:
            return {
                "ok": not settings.required,
                "required": settings.required,
                "enabled": False,
                "runtime": settings.runtime,
                "capacity": capacity,
            }
        await self._refresh_residence(settings)
        effective_slots = self._observed_slots or settings.server_slots
        slot_contract_ok = capacity["max_concurrency"] in {0, effective_slots}
        healthy = self._resident and self._inference_verified and slot_contract_ok
        return {
            "ok": healthy or not settings.required,
            "healthy": healthy,
            "required": settings.required,
            "enabled": True,
            "runtime": settings.runtime,
            "model": settings.model,
            "resident": self._resident,
            "inference_verified": self._inference_verified,
            "warmed_at": self._warmed_at,
            "slot_contract_ok": slot_contract_ok,
            "declared_server_slots": settings.server_slots,
            "observed_server_slots": self._observed_slots,
            "capacity": capacity,
            "error": self._last_error,
        }

    async def _maintain(self, settings: LocalRuntimeSettings) -> None:
        while True:
            await asyncio.sleep(settings.recovery_interval_seconds)
            await self._refresh_residence(settings)
            if not self._resident or not self._inference_verified:
                await self._ensure_ready(settings)

    async def _ensure_ready(self, settings: LocalRuntimeSettings) -> None:
        """Load the model and verify one minimal generation under a single flight."""
        if self._client is None or not settings.base_url or not settings.model:
            self._last_error = "incomplete_local_llm_configuration"
            return
        async with self._lock:
            try:
                self._inference_verified = await self._verify_inference(settings)
                self._resident = self._inference_verified
                self._last_error = None if self._inference_verified else "inference_incomplete"
                if self._inference_verified:
                    self._warmed_at = datetime.now(UTC).isoformat()
            except Exception as exc:  # provider-specific failures are diagnostics only
                self._resident = False
                self._inference_verified = False
                self._last_error = exc.__class__.__name__

    async def _refresh_residence(self, settings: LocalRuntimeSettings) -> None:
        if self._client is None or not settings.base_url or not settings.model:
            self._resident = False
            return
        try:
            if settings.runtime == "ollama":
                await self._refresh_ollama(settings)
            else:
                await self._refresh_llama_server(settings)
            if not self._resident:
                self._inference_verified = False
                self._last_error = "model_not_resident"
        except Exception as exc:
            self._resident = False
            self._inference_verified = False
            self._last_error = exc.__class__.__name__

    async def _verify_inference(self, settings: LocalRuntimeSettings) -> bool:
        """Execute one token through the runtime-specific inference API."""
        assert self._client is not None
        assert settings.base_url is not None
        assert settings.model is not None
        if settings.runtime == "ollama":
            response = await self._client.post(
                f"{_local_origin(settings.base_url)}/api/generate",
                json={
                    "model": settings.model,
                    "prompt": "OK",
                    "stream": False,
                    "think": False,
                    "keep_alive": _keep_alive_value(settings.keep_alive),
                    "options": {"temperature": 0, "num_predict": 1},
                },
            )
            response.raise_for_status()
            return response.json().get("done") is True
        response = await self._client.post(
            f"{settings.base_url.rstrip('/')}/chat/completions",
            headers=_authorization_headers(settings.api_key),
            json={
                "model": settings.model,
                "messages": [{"role": "user", "content": "OK"}],
                "temperature": 0,
                "max_tokens": 1,
                "stream": False,
            },
        )
        response.raise_for_status()
        return bool(response.json().get("choices"))

    async def _refresh_ollama(self, settings: LocalRuntimeSettings) -> None:
        """Refresh Ollama model residence without running generation."""
        assert self._client is not None
        assert settings.base_url is not None
        assert settings.model is not None
        response = await self._client.get(
            f"{_local_origin(settings.base_url)}/api/ps",
            timeout=min(settings.probe_timeout_seconds, 2.0),
        )
        response.raise_for_status()
        names = {
            str(item.get("name") or item.get("model") or "")
            for item in response.json().get("models", [])
            if isinstance(item, dict)
        }
        self._resident = _model_is_present(settings.model, names)
        self._observed_slots = settings.server_slots

    async def _refresh_llama_server(self, settings: LocalRuntimeSettings) -> None:
        """Probe llama-server health and count its actual execution slots."""
        assert self._client is not None
        assert settings.base_url is not None
        headers = _authorization_headers(settings.api_key)
        origin = _local_origin(settings.base_url)
        health = await self._client.get(
            f"{origin}/health",
            headers=headers,
            timeout=min(settings.probe_timeout_seconds, 2.0),
        )
        health.raise_for_status()
        slots = await self._client.get(
            f"{origin}/slots",
            headers=headers,
            timeout=min(settings.probe_timeout_seconds, 2.0),
        )
        slots.raise_for_status()
        payload = slots.json()
        self._observed_slots = len(payload) if isinstance(payload, list) else 0
        self._resident = self._observed_slots > 0


def _local_origin(base_url: str) -> str:
    """Convert an OpenAI-compatible `/v1` URL into its server origin."""
    parsed = urlsplit(base_url.rstrip("/"))
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def _model_is_present(model: str, names: set[str]) -> bool:
    expected = model.removesuffix(":latest")
    return any(name == model or name.removesuffix(":latest") == expected for name in names)


def _keep_alive_value(value: str) -> int | str:
    """Use Ollama's numeric sentinel type for values such as -1 and 0."""
    normalized = value.strip()
    if normalized.lstrip("+-").isdigit():
        return int(normalized)
    return normalized


def _configured_server_slots(runtime: str) -> int:
    """Return the declared capacity for the selected local runtime."""
    if runtime == "ollama":
        value = os.getenv(
            "LOCAL_LLM_OLLAMA_SLOTS",
            os.getenv("OLLAMA_NUM_PARALLEL", "1"),
        )
    else:
        value = os.getenv("LOCAL_LLM_SERVER_SLOTS", "4")
    return max(1, int(value))


def _authorization_headers(api_key: str | None) -> dict[str, str]:
    """Return the local server authorization header when configured."""
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


local_llm_runtime = LocalLLMRuntime()
