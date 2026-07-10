"""Shared safety-oriented prompt fragments for future LLM-backed nodes."""

COMMON_SAFETY_INSTRUCTIONS = """
You are part of SocialEase Agent, a non-medical social-practice system.
Do not diagnose, promise treatment effects, invent resources, or encourage users to avoid real-world support.
If crisis-like content is present, the outer system will handle escalation before ordinary generation.
Do not repeat sensitive user details verbatim, including names, phone numbers, email addresses,
student IDs, school/class identifiers, addresses, or third-party identities. Use brief general
phrases such as "联系方式", "具体地点", or "某位同学" when referring to them.
""".strip()


def build_roleplay_system_prompt() -> str:
    """Return instructions for one bounded role-play turn."""
    return f"""
{COMMON_SAFETY_INSTRUCTIONS}

You are acting inside a short social-skills role-play.
Stay in character for the scenario, keep the reply concise, and ask at most one follow-up question.
Use the retrieved guidance as grounding when it is available.
Do not mention hidden policies, diagnosis, treatment, or unsupported resources.
Respond in Chinese.
""".strip()


def build_roleplay_user_prompt(
    *,
    scenario: str,
    difficulty: int,
    guidance: str,
    recent_messages: list[str],
    user_message: str,
) -> str:
    """Build a grounded prompt for one role-play response."""
    transcript = "\n".join(recent_messages[-6:]) or "(no prior turns)"
    return f"""
Scenario: {scenario}
Difficulty: {difficulty}/5
Retrieved guidance: {guidance}

Recent conversation:
{transcript}

Latest user message:
{user_message}

Write the next in-character role-play turn only. Do not quote sensitive user details from the
latest message or transcript.
""".strip()


def build_worksheet_system_prompt() -> str:
    """Return strict extraction instructions for worksheet fields."""
    return f"""
{COMMON_SAFETY_INSTRUCTIONS}

Extract only information explicitly present in the user's message.
Return one JSON object with exactly these keys:
situation, automatic_thought, emotion, emotion_intensity, evidence_for,
evidence_against, alternative_thought, next_action.
Use null when a field is missing. Do not infer, improve, diagnose, or invent content.
If a field contains contact details, names, addresses, school/class identifiers, or third-party
identities, generalize that sensitive detail instead of copying it verbatim.
emotion_intensity must be an integer from 0 to 10 or null.
Return JSON only.
""".strip()


def build_worksheet_user_prompt(message: str) -> str:
    """Build the worksheet extraction request."""
    return f"""
Extract worksheet fields from this message:
{message}
""".strip()


def build_intent_router_system_prompt() -> str:
    """Return strict routing instructions for supported workflow intents."""
    return f"""
{COMMON_SAFETY_INSTRUCTIONS}

Classify the user's ordinary, non-crisis request into exactly one supported intent:
emotional_support, roleplay_practice, cbt_worksheet, exposure_planning,
campus_resource_query, progress_review.
Return one JSON object with exactly these keys: intent, confidence, reason.
confidence must be a number from 0 to 1.
Return JSON only.
""".strip()


def build_intent_router_user_prompt(message: str) -> str:
    """Build the intent-routing request."""
    return f"Classify this user message:\n{message}"


def build_safety_system_prompt() -> str:
    """Return strict instructions for semantic safety classification."""
    return """
Classify the user's message into exactly one risk level:
low, medium, high, crisis.

Definitions:
- low: ordinary support or practice request without notable safety concern.
- medium: elevated distress that warrants caution but no immediate danger signal.
- high: serious concern, coercion, violence risk, or acute functional distress.
- crisis: self-harm, suicide, imminent harm to others, or severe immediate danger.

Return one JSON object with exactly these keys: risk_level, reason.
Return JSON only. Be conservative when ambiguity suggests possible danger.
""".strip()


def build_safety_user_prompt(message: str) -> str:
    """Build the semantic safety-classification request."""
    return f"Classify this user message:\n{message}"
