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
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id ON user_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_sessions_access_token ON user_sessions(access_token_id);
CREATE INDEX IF NOT EXISTS idx_user_sessions_refresh_token ON user_sessions(refresh_token_hash);
CREATE INDEX IF NOT EXISTS idx_harness_metric_events_created_at ON harness_metric_events(created_at);
CREATE INDEX IF NOT EXISTS idx_harness_metric_events_risk ON harness_metric_events(risk_level);
CREATE INDEX IF NOT EXISTS idx_harness_metric_events_permission ON harness_metric_events(permission_action);
CREATE INDEX IF NOT EXISTS idx_harness_runtime_metric_events_name ON harness_runtime_metric_events(event_name);
CREATE INDEX IF NOT EXISTS idx_harness_runtime_metric_events_created_at ON harness_runtime_metric_events(created_at);
"""
