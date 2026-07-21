# Calendar Planning Skill

## When to use

Use when the user explicitly asks to schedule a bounded SocialEase practice reminder in a calendar.

## Output contract

- Return a neutral `CalendarEventProposal` preview.
- Require an explicit timezone and a finite recurrence end date.
- State clearly that no calendar write has occurred yet.

## Safety and permission boundaries

- Do not create medical, diagnostic or treatment reminders.
- Do not read unrelated calendar event content.
- Do not invite third parties.
- Never call a write tool from this Skill.
- Create, update and delete operations require an owner-bound, request-hash-bound Consent protocol.

## Fallback

If time information is missing, ask one short clarification question and produce no proposal.
