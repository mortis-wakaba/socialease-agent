"""DDL schema for persisted SocialEase records."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS roleplay_sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS worksheets (
    worksheet_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exposure_plans (
    plan_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exposure_attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(plan_id) REFERENCES exposure_plans(plan_id)
);
CREATE TABLE IF NOT EXISTS protocols (
    protocol_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    protocol_type TEXT NOT NULL,
    status TEXT NOT NULL,
    session_id TEXT,
    harness_action TEXT,
    request_hash TEXT,
    expires_at TEXT,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS intervention_plans (
    plan_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_memory_settings (
    user_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session_reviews (
    review_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    source TEXT NOT NULL,
    source_id TEXT,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS episodic_memories (
    memory_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    scenario_type TEXT,
    scenario_id TEXT,
    practice_thread_id TEXT,
    skill_codes TEXT NOT NULL DEFAULT '[]',
    context_tags TEXT NOT NULL DEFAULT '[]',
    source_type TEXT NOT NULL,
    source_id TEXT,
    evidence_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_retrieved_at TEXT,
    expires_at TEXT,
    consent_version TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    supersedes_id TEXT,
    version INTEGER NOT NULL,
    CHECK (memory_type IN ('practice_experience', 'helpful_strategy', 'practice_milestone', 'social_context', 'recurring_pattern')),
    CHECK (source_type IN ('chat', 'roleplay', 'worksheet', 'exposure', 'session_review', 'user_confirmed')),
    CHECK (evidence_type IN ('explicit_user_statement', 'completed_product_action', 'user_confirmed')),
    CHECK (confidence >= 0 AND confidence <= 1),
    CHECK (status IN ('active', 'inactive', 'archived', 'superseded', 'revoked')),
    CHECK (version >= 1)
);
CREATE TABLE IF NOT EXISTS thread_checkpoints (
    thread_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    current_goal TEXT,
    current_stage TEXT,
    current_scenario TEXT,
    current_scenario_id TEXT,
    current_scenario_summary TEXT,
    scenario_skill_codes TEXT NOT NULL DEFAULT '[]',
    helpful_strategy_codes TEXT NOT NULL,
    attempted_skill_names TEXT NOT NULL,
    unresolved_next_step TEXT,
    status TEXT NOT NULL,
    version INTEGER NOT NULL,
    last_activity_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (status IN ('active', 'paused', 'completed', 'archived')),
    CHECK (version >= 1)
);
CREATE TABLE IF NOT EXISTS memory_events (
    event_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    reason_code TEXT NOT NULL,
    subject_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (subject_type IN ('episodic_memory', 'thread_checkpoint', 'memory_proposal')),
    CHECK (subject_version >= 1)
);
CREATE TABLE IF NOT EXISTS memory_proposals (
    proposal_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    scenario_type TEXT,
    scenario_id TEXT,
    practice_thread_id TEXT,
    skill_codes TEXT NOT NULL DEFAULT '[]',
    context_tags TEXT NOT NULL DEFAULT '[]',
    source_type TEXT NOT NULL,
    source_id TEXT,
    evidence_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    occurred_at TEXT NOT NULL,
    status TEXT NOT NULL,
    policy_reason TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    CHECK (confidence >= 0 AND confidence <= 1),
    CHECK (status IN ('pending_confirmation', 'confirmed', 'rejected', 'expired')),
    CHECK (version >= 1)
);
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    last_login_at TEXT,
    last_failed_login_at TEXT,
    failed_login_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    refresh_token_hash TEXT NOT NULL UNIQUE,
    access_token_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    revoked_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(user_id)
);
CREATE TABLE IF NOT EXISTS harness_metric_events (
    metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
    intent TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    selected_agent TEXT NOT NULL,
    permission_action TEXT,
    latency_ms REAL NOT NULL,
    is_crisis INTEGER NOT NULL,
    fallback_used INTEGER NOT NULL,
    hook_blocked INTEGER NOT NULL,
    memory_write_blocked INTEGER NOT NULL,
    product_boundary_eval TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS harness_runtime_metric_events (
    metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    active_module_depth INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    history_notice_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (status IN ('active', 'archived', 'deleted')),
    CHECK (active_module_depth >= 0 AND active_module_depth <= 3),
    CHECK (version >= 1)
);
CREATE TABLE IF NOT EXISTS conversation_events (
    event_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    role TEXT NOT NULL,
    content_plaintext TEXT,
    content_ciphertext BLOB,
    content_nonce BLOB,
    content_key_version TEXT,
    structured_payload TEXT,
    module_run_id TEXT,
    parent_module_run_id TEXT,
    idempotency_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
        ON DELETE CASCADE,
    UNIQUE(conversation_id, sequence_no),
    UNIQUE(conversation_id, idempotency_key),
    CHECK (
        (content_plaintext IS NOT NULL AND content_ciphertext IS NULL
            AND content_nonce IS NULL AND content_key_version IS NULL)
        OR
        (content_plaintext IS NULL AND content_ciphertext IS NOT NULL
            AND content_nonce IS NOT NULL AND content_key_version IS NOT NULL)
    )
);
CREATE TABLE IF NOT EXISTS conversation_module_proposals (
    proposal_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    proposed_module TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    status TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    payload TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
        ON DELETE CASCADE,
    CHECK (status IN ('pending', 'accepted', 'rejected', 'expired'))
);
CREATE TABLE IF NOT EXISTS conversation_module_runs (
    module_run_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    module_type TEXT NOT NULL,
    parent_module_run_id TEXT,
    depth INTEGER NOT NULL,
    status TEXT NOT NULL,
    domain_session_id TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    payload TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
        ON DELETE CASCADE,
    FOREIGN KEY(parent_module_run_id)
        REFERENCES conversation_module_runs(module_run_id),
    CHECK (depth >= 1 AND depth <= 3),
    CHECK (status IN ('active', 'suspended', 'completed', 'terminated')),
    CHECK (version >= 1)
);
CREATE TABLE IF NOT EXISTS conversation_context_summaries (
    conversation_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    compacted_through_sequence INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
        ON DELETE CASCADE,
    CHECK (compacted_through_sequence >= 0),
    CHECK (version >= 1)
);
CREATE TABLE IF NOT EXISTS conversation_deletion_receipts (
    conversation_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    deleted_counts TEXT NOT NULL,
    deleted_at TEXT NOT NULL,
    PRIMARY KEY(conversation_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_runs_user_id ON runs(user_id);
CREATE INDEX IF NOT EXISTS idx_roleplay_sessions_user_id ON roleplay_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_worksheets_user_id ON worksheets(user_id);
CREATE INDEX IF NOT EXISTS idx_exposure_attempts_plan_id ON exposure_attempts(plan_id);
CREATE INDEX IF NOT EXISTS idx_exposure_attempts_user_id ON exposure_attempts(user_id);
CREATE INDEX IF NOT EXISTS idx_protocols_user_id ON protocols(user_id);
CREATE INDEX IF NOT EXISTS idx_protocols_status ON protocols(status);
CREATE INDEX IF NOT EXISTS idx_protocols_user_status ON protocols(user_id, status);
CREATE INDEX IF NOT EXISTS idx_intervention_plans_user_id ON intervention_plans(user_id);
CREATE INDEX IF NOT EXISTS idx_intervention_plans_session_id ON intervention_plans(session_id);
CREATE INDEX IF NOT EXISTS idx_intervention_plans_status ON intervention_plans(user_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_session_reviews_user_id ON session_reviews(user_id);
CREATE INDEX IF NOT EXISTS idx_session_reviews_user_created ON session_reviews(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_episodic_memories_user_status
ON episodic_memories(user_id, status, occurred_at);
CREATE INDEX IF NOT EXISTS idx_episodic_memories_user_hash
ON episodic_memories(user_id, content_hash);
CREATE INDEX IF NOT EXISTS idx_episodic_memories_source
ON episodic_memories(user_id, source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_episodic_memories_retrieval
ON episodic_memories(user_id, status, memory_type, scenario_type, occurred_at);
CREATE INDEX IF NOT EXISTS idx_thread_checkpoints_user_status
ON thread_checkpoints(user_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_memory_events_user_created
ON memory_events(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_memory_events_subject
ON memory_events(user_id, subject_type, subject_id, created_at);
CREATE INDEX IF NOT EXISTS idx_memory_proposals_user_status
ON memory_proposals(user_id, status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_proposals_user_idempotency
ON memory_proposals(user_id, idempotency_key);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id ON user_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_sessions_access_token ON user_sessions(access_token_id);
CREATE INDEX IF NOT EXISTS idx_user_sessions_refresh_token ON user_sessions(refresh_token_hash);
CREATE INDEX IF NOT EXISTS idx_harness_metric_events_created_at ON harness_metric_events(created_at);
CREATE INDEX IF NOT EXISTS idx_harness_metric_events_risk ON harness_metric_events(risk_level);
CREATE INDEX IF NOT EXISTS idx_harness_metric_events_permission ON harness_metric_events(permission_action);
CREATE INDEX IF NOT EXISTS idx_harness_runtime_metric_events_name ON harness_runtime_metric_events(event_name);
CREATE INDEX IF NOT EXISTS idx_harness_runtime_metric_events_created_at ON harness_runtime_metric_events(created_at);
CREATE INDEX IF NOT EXISTS idx_conversations_user_updated
ON conversations(user_id, updated_at DESC, conversation_id DESC);
CREATE INDEX IF NOT EXISTS idx_conversation_events_owner_sequence
ON conversation_events(user_id, conversation_id, sequence_no);
CREATE INDEX IF NOT EXISTS idx_conversation_module_proposals_owner_status
ON conversation_module_proposals(user_id, conversation_id, status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_conversation_module_proposals_request
ON conversation_module_proposals(conversation_id, request_hash);
CREATE INDEX IF NOT EXISTS idx_conversation_module_runs_stack
ON conversation_module_runs(user_id, conversation_id, depth);
CREATE INDEX IF NOT EXISTS idx_conversation_context_summaries_owner
ON conversation_context_summaries(user_id, conversation_id);
CREATE INDEX IF NOT EXISTS idx_conversation_deletion_receipts_owner
ON conversation_deletion_receipts(user_id, deleted_at);
"""
