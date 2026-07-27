"""Support-resource service shared by API routes and harness skills."""

from datetime import datetime, timezone
from hashlib import sha256
import re
from uuid import uuid4

from app.knowledge.service import KnowledgeService
from app.models import RiskLevel
from app.models_knowledge import Citation, KnowledgeBaseType
from app.models_support import SupportQueryRequest, SupportQueryResponse
from app.models_support import SupportSearchContext
from app.memory.redis_settings import redis_task_state_settings
from app.memory.task_session_settings import support_search_ttl_seconds
from app.memory.task_state_store import (
    DisabledTaskStateStore,
    RedisTaskStateStore,
    TaskStateStore,
    TaskStateStoreUnavailable,
)
from app.safety.classifier import BaseSafetyClassifier, create_safety_classifier
from app.safety.crisis import crisis_escalation_response


SUPPORT_CRISIS_RESPONSE = crisis_escalation_response(paused_activity="普通资源检索")


class SupportResourceService:
    """Coordinate support-resource safety checks and grounded retrieval."""

    def __init__(
        self,
        knowledge: KnowledgeService | None = None,
        safety_classifier: BaseSafetyClassifier | None = None,
        search_store: TaskStateStore[SupportSearchContext] | None = None,
        search_ttl_seconds: int | None = None,
    ) -> None:
        self.knowledge = knowledge or KnowledgeService()
        self.safety_classifier = safety_classifier or create_safety_classifier()
        settings = redis_task_state_settings()
        self.search_store = search_store or (
            RedisTaskStateStore(
                redis_url=settings.redis_url,
                namespace="support-search",
                model_type=SupportSearchContext,
                socket_timeout_seconds=settings.socket_timeout_seconds,
            )
            if settings.redis_url
            else DisabledTaskStateStore()
        )
        self.search_ttl_seconds = search_ttl_seconds or support_search_ttl_seconds()

    async def query_resources(self, request: SupportQueryRequest) -> SupportQueryResponse:
        """Query public support resources unless escalation is required."""
        safety_result = await self.safety_classifier.classify(request.query)
        user_id = request.user_id or "anonymous"
        session_id = request.search_session_id or str(uuid4())
        if safety_result.risk_level == RiskLevel.CRISIS:
            await self.search_store.delete(user_id=user_id, task_id=session_id)
            return SupportQueryResponse(
                answer=SUPPORT_CRISIS_RESPONSE,
                citations=[],
                unknown=False,
                confidence=1.0,
                retrieval=None,
                safety_result=safety_result,
                blocked=True,
                search_session_id=session_id,
            )

        previous = await self._load(user_id, session_id)
        reference_index = _resolve_citation_reference(
            request.query,
            previous,
        )
        if reference_index is not None:
            citation = self._resolve_cached_citation(
                previous,
                reference_index,
            )
            if citation is None:
                return SupportQueryResponse(
                    answer="当前检索会话中找不到你指向的那条来源，请重新描述资料标题或重新查询。",
                    citations=[],
                    unknown=True,
                    confidence=0.0,
                    retrieval=None,
                    safety_result=safety_result,
                    blocked=False,
                    search_session_id=session_id,
                    resolved_reference_index=reference_index,
                )
            await self._save(
                previous.model_copy(
                    update={
                        "selected_citation_index": reference_index,
                        "version": previous.version + 1,
                        "updated_at": datetime.now(timezone.utc),
                    },
                    deep=True,
                )
            )
            return SupportQueryResponse(
                answer=f"你指的是《{citation.title}》。{citation.snippet}",
                citations=[citation],
                unknown=False,
                confidence=1.0,
                retrieval=None,
                safety_result=safety_result,
                blocked=False,
                search_session_id=session_id,
                resolved_reference_index=reference_index,
            )

        response = self.knowledge.query(
            query=request.query,
            kb_type=KnowledgeBaseType.SUPPORT_RESOURCES,
        )
        await self._save(
            SupportSearchContext(
                user_id=user_id,
                search_session_id=session_id,
                query_fingerprint=_query_fingerprint(request.query),
                ordered_citation_ids=[
                    citation.citation_id
                    for citation in response.citations[:10]
                    if citation.citation_id is not None
                ],
                selected_citation_index=None,
                retrieval_unknown=response.unknown,
                version=(previous.version + 1 if previous else 1),
                updated_at=datetime.now(timezone.utc),
            )
        )
        return SupportQueryResponse(
            answer=response.answer,
            citations=response.citations,
            unknown=response.unknown,
            confidence=response.confidence,
            retrieval=response.retrieval,
            safety_result=safety_result,
            blocked=False,
            search_session_id=session_id,
        )

    async def delete_user_context(self, user_id: str) -> int:
        return await self.search_store.delete_user(user_id=user_id)

    async def close(self) -> None:
        await self.search_store.close()

    async def context_health(self) -> bool:
        """Return whether the configured support-search backend responds."""
        return await self.search_store.ping()

    async def get_search_context(
        self,
        *,
        user_id: str,
        session_id: str,
    ) -> SupportSearchContext | None:
        """Return bounded citation-reference state for an owned search session."""
        return await self._load(user_id, session_id)

    async def rebuild_search_context(
        self,
        *,
        user_id: str,
        session_id: str,
        citation_ids: list[str],
        retrieval_unknown: bool,
    ) -> SupportSearchContext:
        """Rebuild citation references from the reviewed knowledge base."""
        state = SupportSearchContext(
            user_id=user_id,
            search_session_id=session_id,
            query_fingerprint="0" * 16,
            ordered_citation_ids=citation_ids[:10],
            retrieval_unknown=retrieval_unknown,
            updated_at=datetime.now(timezone.utc),
        )
        await self._save(state)
        return state

    async def _load(self, user_id: str, session_id: str) -> SupportSearchContext | None:
        try:
            return await self.search_store.get(user_id=user_id, task_id=session_id)
        except TaskStateStoreUnavailable:
            return None

    async def _save(self, state: SupportSearchContext) -> None:
        for _attempt in range(3):
            try:
                current = await self.search_store.get(
                    user_id=state.user_id,
                    task_id=state.search_session_id,
                )
                if current is None:
                    await self.search_store.put(
                        user_id=state.user_id,
                        task_id=state.search_session_id,
                        state=state.model_copy(update={"version": 1}),
                        ttl_seconds=self.search_ttl_seconds,
                    )
                    return None
                selected_index = state.selected_citation_index
                ordered_citation_ids = state.ordered_citation_ids
                if selected_index is not None:
                    ordered_citation_ids = current.ordered_citation_ids
                    if selected_index >= len(ordered_citation_ids):
                        selected_index = None
                candidate = state.model_copy(
                    update={
                        "ordered_citation_ids": ordered_citation_ids,
                        "selected_citation_index": selected_index,
                        "version": current.version + 1,
                        "updated_at": datetime.now(timezone.utc),
                    },
                    deep=True,
                )
                saved = await self.search_store.compare_and_set(
                    user_id=state.user_id,
                    task_id=state.search_session_id,
                    state=candidate,
                    expected_version=current.version,
                    ttl_seconds=self.search_ttl_seconds,
                )
                if saved:
                    return None
            except TaskStateStoreUnavailable:
                return None
        return None

    def _resolve_cached_citation(
        self,
        state: SupportSearchContext | None,
        index: int,
    ) -> Citation | None:
        """Rehydrate one reviewed citation without trusting cached body text."""
        if (
            state is None
            or index < 0
            or index >= len(state.ordered_citation_ids)
        ):
            return None
        resolved = self.knowledge.resolve_citations(
            [state.ordered_citation_ids[index]],
            kb_type=KnowledgeBaseType.SUPPORT_RESOURCES,
        )
        return resolved[0] if resolved else None


support_resource_service = SupportResourceService()


def _query_fingerprint(query: str) -> str:
    return sha256(query.encode("utf-8")).hexdigest()[:16]


def _resolve_citation_reference(
    query: str,
    state: SupportSearchContext | None,
) -> int | None:
    """Resolve explicit ordinal references without asking a model to invent IDs."""
    normalized = query.casefold()
    words = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4}
    match = re.search(r"第\s*([1-5一二三四五])\s*(?:个|条|篇|份|项|来源)?", normalized)
    if match:
        value = match.group(1)
        return words[value] if value in words else int(value) - 1
    if any(term in normalized for term in ("最后一个", "最后一条", "最后一篇", "最后的来源")):
        return (
            len(state.ordered_citation_ids) - 1
            if state and state.ordered_citation_ids
            else 0
        )
    if any(term in normalized for term in ("上一个", "刚才那个", "刚刚那个")):
        if state and state.selected_citation_index is not None:
            return state.selected_citation_index
        return 0
    return None
