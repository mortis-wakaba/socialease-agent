"""Harness action types used by permission decisions."""

from enum import Enum


class HarnessAction(str, Enum):
    """Bounded actions the lead harness may execute."""

    GENERAL_SUPPORT = "general_support"
    START_ROLEPLAY = "start_roleplay"
    CREATE_WORKSHEET = "create_worksheet"
    CREATE_EXPOSURE_PLAN = "create_exposure_plan"
    COMPLETE_EXPOSURE_TASK = "complete_exposure_task"
    QUERY_SUPPORT_RESOURCE = "query_support_resource"
    REQUEST_CLARIFICATION = "request_clarification"
    DECLINE_OUT_OF_SCOPE = "decline_out_of_scope"
    CRISIS_ESCALATION = "crisis_escalation"
    WRITE_MEMORY = "write_memory"
    CREATE_CALENDAR_EVENT = "create_calendar_event"
    UPDATE_CALENDAR_EVENT = "update_calendar_event"
    DELETE_CALENDAR_EVENT = "delete_calendar_event"
    PROPOSE_CALENDAR_EVENT = "propose_calendar_event"
