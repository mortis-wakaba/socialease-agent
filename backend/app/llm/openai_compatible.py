"""OpenAI-compatible HTTP adapter for hosted LLM providers."""

import httpx

from app.llm.retry import (
    CircuitBreaker,
    PermanentProviderError,
    ProviderConcurrencyLimiter,
    RetryPolicy,
    classify_httpx_error,
    retry_async,
)


class OpenAICompatibleLLMClient:
    """Call providers exposing an OpenAI-compatible chat-completions API."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        http_client: httpx.AsyncClient | None = None,
        retry_policy: RetryPolicy | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        concurrency_limiter: ProviderConcurrencyLimiter | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.http_client = http_client
        self.retry_policy = retry_policy or RetryPolicy()
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        self.concurrency_limiter = concurrency_limiter or ProviderConcurrencyLimiter(
            max_concurrency=0
        )

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
    ) -> str:
        """Generate text through the chat-completions compatible endpoint."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.http_client is not None:
            return await self.concurrency_limiter.run(
                lambda: retry_async(
                    lambda: self._post(self.http_client, payload, headers),
                    retry_policy=self.retry_policy,
                    circuit_breaker=self.circuit_breaker,
                )
            )

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            return await self.concurrency_limiter.run(
                lambda: retry_async(
                    lambda: self._post(client, payload, headers),
                    retry_policy=self.retry_policy,
                    circuit_breaker=self.circuit_breaker,
                )
            )

    async def _post(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, object],
        headers: dict[str, str],
    ) -> str:
        try:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise classify_httpx_error(exc) from exc
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise classify_httpx_error(exc) from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise PermanentProviderError("LLM provider returned invalid JSON.") from exc
        choices = data.get("choices", [])
        if not choices:
            raise PermanentProviderError("LLM response did not include choices.")
        message = choices[0].get("message", {})
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise PermanentProviderError("LLM response did not include text content.")
        return content.strip()
