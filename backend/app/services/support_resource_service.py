"""Support-resource service shared by API routes and harness skills."""

from app.knowledge.service import KnowledgeService
from app.models import RiskLevel
from app.models_knowledge import KnowledgeBaseType
from app.models_support import SupportQueryRequest, SupportQueryResponse
from app.safety.classifier import BaseSafetyClassifier, create_safety_classifier
from app.safety.crisis import crisis_escalation_response


SUPPORT_CRISIS_RESPONSE = crisis_escalation_response(paused_activity="普通资源检索")


class SupportResourceService:
    """Coordinate support-resource safety checks and grounded retrieval."""

    def __init__(
        self,
        knowledge: KnowledgeService | None = None,
        safety_classifier: BaseSafetyClassifier | None = None,
    ) -> None:
        self.knowledge = knowledge or KnowledgeService()
        self.safety_classifier = safety_classifier or create_safety_classifier()

    async def query_resources(self, request: SupportQueryRequest) -> SupportQueryResponse:
        """Query public support resources unless escalation is required."""
        safety_result = await self.safety_classifier.classify(request.query)
        if safety_result.risk_level == RiskLevel.CRISIS:
            return SupportQueryResponse(
                answer=SUPPORT_CRISIS_RESPONSE,
                citations=[],
                unknown=False,
                confidence=1.0,
                retrieval=None,
                safety_result=safety_result,
                blocked=True,
            )

        response = self.knowledge.query(
            query=request.query,
            kb_type=KnowledgeBaseType.SUPPORT_RESOURCES,
        )
        return SupportQueryResponse(
            answer=response.answer,
            citations=response.citations,
            unknown=response.unknown,
            confidence=response.confidence,
            retrieval=response.retrieval,
            safety_result=safety_result,
            blocked=False,
        )


support_resource_service = SupportResourceService()
