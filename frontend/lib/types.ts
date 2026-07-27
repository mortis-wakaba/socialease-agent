export type RiskLevel = "low" | "medium" | "high" | "crisis";

export type Intent =
  | "emotional_support"
  | "roleplay_practice"
  | "cbt_worksheet"
  | "exposure_planning"
  | "campus_resource_query"
  | "progress_review"
  | "calendar_planning"
  | "clarification_needed"
  | "out_of_scope"
  | "crisis";

export type SafetyResult = {
  risk_level: RiskLevel;
  reason: string;
  llm_usage: LLMUsage;
};

export type IntentResult = {
  intent: Intent;
  confidence: number;
  reason: string;
  llm_usage: LLMUsage;
};

export type ExecutionVersionInfo = {
  app_version: string;
  trace_schema_version: string;
  llm_provider?: string | null;
  llm_model?: string | null;
  model_config_version: string;
  prompt_versions: Record<string, string>;
  guardrail_policy_version: string;
  skill_registry_version: string;
  eval_dataset_version?: string | null;
};

export type TraceRecord = {
  run_id: string;
  user_id: string;
  session_id?: string | null;
  intervention_plan_id?: string | null;
  execution_version: ExecutionVersionInfo;
  input: string;
  safety_result: SafetyResult;
  intent_result: IntentResult;
  selected_skill?: string | null;
  selected_agent: string;
  action?: string | null;
  permission_action?: string | null;
  permission_reason?: string | null;
  agent_loop_used?: boolean;
  agent_loop_stop_reason?: string | null;
  agent_loop_steps?: Array<{
    step: number;
    action: string;
    reason: string;
    query?: string | null;
    observation_id?: number | null;
    selected_observation_ids?: number[];
    citation_count?: number;
    unknown?: boolean | null;
    outcome: string;
  }>;
  output_guardrail_action?: "allow" | "augment" | "repair" | "replace" | null;
  output_guardrail_categories?: string[];
  output_guardrail_semantic_checked?: boolean;
  output_guardrail_semantic_failed?: boolean;
  output_guardrail_semantic_error_type?: string | null;
  output_guardrail_semantic_schema_error_code?: string | null;
  output_guardrail_semantic_schema_error_field?: string | null;
  output_guardrail_semantic_retry_attempted?: boolean;
  output_guardrail_violation_tier?: "hard_safety" | "soft_factual" | null;
  output_guardrail_repair_attempted?: boolean;
  output_guardrail_repair_succeeded?: boolean;
  output_guardrail_recheck_action?: string | null;
  output: string;
  product_safe: boolean;
  privacy_summary: TracePrivacySummary;
  latency_ms: number;
  errors: string[];
  created_at: string;
};

export type TraceFieldPolicy = {
  field: string;
  persistence_kind: string;
  minimized: boolean;
  redacted_types: string[];
  original_length: number;
  persisted_length: number;
};

export type TracePrivacySummary = {
  trace_layer: string;
  raw_input_retained: boolean;
  raw_output_retained: boolean;
  fields: TraceFieldPolicy[];
};

export type ChatResponse = {
  run_id: string;
  risk_level: RiskLevel;
  intent: Intent;
  response: string;
  structured_data: Record<string, unknown>;
  trace: TraceRecord;
};

export type ConversationStatus = "active" | "archived" | "deleted";
export type ConversationEventType =
  | "user_message"
  | "assistant_message"
  | "module_proposed"
  | "module_started"
  | "module_message"
  | "module_suspended"
  | "module_resumed"
  | "module_completed"
  | "module_terminated"
  | "crisis_escalated";
export type ModuleType = "roleplay" | "worksheet" | "exposure" | "resource";
export type ModuleProposalStatus =
  | "pending"
  | "accepted"
  | "rejected"
  | "expired";
export type ModuleRunStatus =
  | "active"
  | "suspended"
  | "completed"
  | "terminated";

export type Conversation = {
  conversation_id: string;
  user_id: string;
  title: string;
  status: ConversationStatus;
  active_module_depth: number;
  version: number;
  created_at: string;
  updated_at: string;
  history_notice_version: string;
};

export type ConversationEvent = {
  event_id: string;
  conversation_id: string;
  user_id: string;
  sequence_no: number;
  event_type: ConversationEventType;
  role: "user" | "assistant" | "system";
  content: string;
  structured_payload?: Record<string, unknown> | null;
  module_run_id?: string | null;
  parent_module_run_id?: string | null;
  idempotency_key: string;
  created_at: string;
};

export type ModuleProposal = {
  proposal_id: string;
  conversation_id: string;
  user_id: string;
  proposed_module: ModuleType;
  reason_code: string;
  bounded_parameters: Record<string, unknown>;
  status: ModuleProposalStatus;
  request_hash: string;
  expires_at: string;
  created_at: string;
};

export type ModuleRun = {
  module_run_id: string;
  conversation_id: string;
  user_id: string;
  module_type: ModuleType;
  parent_module_run_id?: string | null;
  depth: number;
  status: ModuleRunStatus;
  module_parameters: Record<string, unknown>;
  domain_session_id?: string | null;
  started_at: string;
  ended_at?: string | null;
  version: number;
};

export type ConversationPage = {
  items: Conversation[];
  next_cursor?: string | null;
};

export type ConversationEventPage = {
  items: ConversationEvent[];
  next_cursor?: string | null;
};

export type ConversationContextDiagnostics = {
  conversation_id_hash: string;
  recent_event_count: number;
  recent_event_sequence_start?: number | null;
  recent_event_sequence_end?: number | null;
  compact_summary_version?: number | null;
  active_module_count: number;
  selected_memory_count: number;
  estimated_tokens: number;
  total_token_budget: number;
  dropped_sections: string[];
  tokenizer_backend: string;
};

export type ConversationDetailResponse = {
  conversation: Conversation;
  events: ConversationEventPage;
  active_module_stack: ModuleRun[];
  pending_module_proposals: ModuleProposal[];
};

export type ConversationMessageResponse = {
  conversation: Conversation;
  appended_events: ConversationEvent[];
  active_module_stack: ModuleRun[];
  pending_module_proposal?: ModuleProposal | null;
  response: string;
  safety_result: SafetyResult;
  context_diagnostics: ConversationContextDiagnostics;
  workflow_response?: ChatResponse | null;
};

export type ModuleControlResponse = {
  conversation: Conversation;
  active_module_stack: ModuleRun[];
  appended_events: ConversationEvent[];
  response: string;
};

export type ConversationExportResponse = {
  conversation: Conversation;
  events: ConversationEvent[];
  module_runs: ModuleRun[];
  module_proposals: ModuleProposal[];
  exported_at: string;
};

export type ChatWorkflowStage =
  | "safety"
  | "routing"
  | "skill"
  | "output_guardrail"
  | "trace";

export type ChatProgressEvent = {
  type: "run_started" | "stage_completed";
  run_id: string;
  stage: ChatWorkflowStage | null;
  stage_latency_ms: number | null;
  elapsed_ms: number;
};

export type AuthUser = {
  user_id: string;
  email: string;
};

export type AuthTokenPair = {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
};

export type AuthResponse = {
  user: AuthUser;
  tokens: AuthTokenPair;
};

export type AuthConfigResponse = {
  auth_mode: string;
  signup_enabled: boolean;
  cookie_auth_enabled: boolean;
};

export type AuthMeResponse = {
  authenticated: boolean;
  user_id: string | null;
  roles: string[];
  auth_mode: string;
  is_demo_user: boolean;
  developer_endpoints_enabled: boolean;
  developer_access: boolean;
};

export type LogoutResponse = {
  revoked: boolean;
};

export type AccountDeleteResponse = {
  deleted: boolean;
  revoked_sessions: number;
  deleted_memory_counts: Record<string, number>;
};

export type UserPracticeSummary = {
  recent_scenarios: string[];
  roleplay_session_count: number;
  worksheet_count: number;
  exposure_attempt_count: number;
  latest_anxiety_level: number | null;
  preferred_difficulty: number | null;
};

export type UserConsentState = {
  store_conversation_history: boolean;
  consent_to_practice_summary: boolean;
  consent_to_save_preferences: boolean;
  do_not_store_raw_messages: boolean;
  allow_sensitive_memory: boolean;
};

export type PreferredFeedbackStyle =
  | "gentle_specific"
  | "brief_actionable"
  | "encouraging_reflective";

export type OnboardingPrimaryGoal =
  | "clearer_classroom_expression"
  | "steadier_group_or_dorm_communication"
  | "boundary_and_refusal_practice"
  | "interview_self_intro_confidence";

export type OnboardingPracticePreference =
  | "short_sentence_first"
  | "step_by_step_ladder"
  | "roleplay_then_feedback";

export type PracticePreferences = {
  preferred_roleplay_difficulty: number | null;
  preferred_feedback_style: PreferredFeedbackStyle | null;
  preferred_practice_scenarios: string[];
};

export type UserOnboardingProfile = {
  primary_goal: OnboardingPrimaryGoal | null;
  preferred_scenario: string | null;
  current_anxiety_level: number | null;
  practice_preference: OnboardingPracticePreference | null;
  wants_pause_reminders: boolean;
  wants_auto_review: boolean;
  boundary_acknowledged: boolean;
};

export type UserOnboardingProfileResponse = {
  user_id: string;
  onboarding_profile: UserOnboardingProfile;
};

export type UserProfileResponse = {
  user_id: string;
  practice_summary: UserPracticeSummary;
  consent_state: UserConsentState;
  practice_preferences: PracticePreferences;
  privacy_notice: string;
  memory_export_available: boolean;
  memory_delete_available: boolean;
};

export type UserMemoryExportResponse = {
  user_id: string;
  profile: UserProfileResponse;
  records: Record<string, Record<string, unknown>[]>;
};

export type UserMemoryDeleteResponse = {
  user_id: string;
  deleted_counts: Record<string, number>;
  profile_after_delete: UserProfileResponse;
};

export type SessionReviewSource = "roleplay" | "worksheet" | "exposure" | "general";

export type SessionReviewCompletion = "completed" | "partial" | "pause";

export type SessionReviewRecord = {
  review_id: string;
  user_id: string;
  source: SessionReviewSource;
  source_id?: string | null;
  completed: SessionReviewCompletion;
  anxiety_before: number;
  anxiety_after: number;
  next_step_summary: string;
  created_at: string;
};

export type SessionReviewCreateResponse = {
  review: SessionReviewRecord | null;
  saved: boolean;
  message: string;
};

export type SessionReviewListResponse = {
  user_id: string;
  reviews: SessionReviewRecord[];
};

export type MemoryPreferencesUpdateResponse = {
  user_id: string;
  consent_state: UserConsentState;
  practice_preferences: PracticePreferences;
};

export type PracticeSummaryConsentUpdateResponse = {
  user_id: string;
  consent_state: UserConsentState;
};

export type MemoryType =
  | "practice_experience"
  | "helpful_strategy"
  | "practice_milestone"
  | "social_context"
  | "recurring_pattern";

export type MemoryRecordStatus =
  | "active"
  | "inactive"
  | "archived"
  | "superseded"
  | "revoked";

export type EpisodicMemoryView = {
  memory_id: string;
  memory_type: MemoryType;
  summary: string;
  scenario_type?: string | null;
  scenario_id?: string | null;
  practice_thread_id?: string | null;
  skill_codes?: string[];
  context_tags?: string[];
  source_type: string;
  evidence_type: string;
  confidence: number;
  status: MemoryRecordStatus;
  saved_reason: string;
  occurred_at: string;
  created_at: string;
  updated_at: string;
  last_retrieved_at?: string | null;
  expires_at?: string | null;
  version: number;
};

export type MemoryProposalView = {
  proposal_id: string;
  memory_type: MemoryType;
  summary: string;
  scenario_type?: string | null;
  scenario_id?: string | null;
  practice_thread_id?: string | null;
  skill_codes?: string[];
  context_tags?: string[];
  source_type: string;
  evidence_type: string;
  confidence: number;
  status: "pending_confirmation" | "confirmed" | "rejected" | "expired";
  saved_reason: string;
  occurred_at: string;
  created_at: string;
  expires_at: string;
  version: number;
};

export type PracticeThreadCheckpoint = {
  thread_id: string;
  user_id: string;
  current_goal?: OnboardingPrimaryGoal | null;
  current_stage?: string | null;
  current_scenario?: string | null;
  current_scenario_id?: string | null;
  current_scenario_summary?: string | null;
  scenario_skill_codes?: string[];
  helpful_strategy_codes: string[];
  attempted_skill_names: string[];
  unresolved_next_step?: string | null;
  status: "active" | "paused" | "completed" | "archived";
  version: number;
  last_activity_at: string;
  created_at: string;
  updated_at: string;
};

export type MemoryCenterResponse = {
  user_id: string;
  stable_memory: {
    consent_state: UserConsentState;
    practice_preferences: PracticePreferences;
    onboarding_profile: UserOnboardingProfile;
    disabled_memory_types: MemoryType[];
  };
  active_threads: PracticeThreadCheckpoint[];
  memories: EpisodicMemoryView[];
  pending_proposals: MemoryProposalView[];
  doctor: MemoryDoctorReport;
  memory_history_distinction: string;
};

export type MemoryDoctorIssueCode =
  | "duplicate_memory"
  | "conflicting_memory"
  | "stale_unused_memory"
  | "consent_inactive_memory"
  | "type_personalization_disabled"
  | "source_reference_missing"
  | "timestamp_invalid"
  | "orphan_embedding"
  | "active_memory_over_budget"
  | "stale_checkpoint"
  | "pending_proposal_aged";

export type MemoryDoctorCheck = {
  code: MemoryDoctorIssueCode;
  status: "passed" | "issues_found" | "not_applicable";
  issue_count: number;
  detail_code?: string | null;
};

export type MemoryDoctorIssue = {
  issue_id: string;
  code: MemoryDoctorIssueCode;
  severity: "info" | "warning" | "action_required";
  subject_type: string;
  subject_id_hashes: string[];
  affected_count: number;
  metadata: Record<string, string | number | boolean>;
  recommendation_code: string;
};

export type MemoryDoctorReport = {
  user_id: string;
  policy_version: "memory-doctor-v1";
  generated_at: string;
  scanned_counts: Record<string, number>;
  thresholds: {
    stale_memory_days: number;
    stale_checkpoint_days: number;
    pending_proposal_days: number;
    active_memory_token_budget: number;
    conflict_term_overlap: number;
  };
  checks: MemoryDoctorCheck[];
  issues: MemoryDoctorIssue[];
  issues_truncated: boolean;
  auto_fix_applied: false;
  contains_memory_content: false;
};

export type MemoryTypePersonalizationResponse = {
  user_id: string;
  memory_type: MemoryType;
  enabled: boolean;
  disabled_memory_types: MemoryType[];
};

export type MemoryMutationResponse = {
  user_id: string;
  memory?: EpisodicMemoryView | null;
  deleted: boolean;
};

export type MemoryProposalDecisionResponse = {
  user_id: string;
  proposal_id: string;
  status: "confirmed" | "rejected";
  memory?: EpisodicMemoryView | null;
};

export type RetrievalHit = {
  title: string;
  score: number;
  source_type: string;
};

export type RetrievalDiagnostics = {
  retriever: string;
  top_k: number;
  hits: RetrievalHit[];
};

export type SupportQueryResponse = {
  answer: string;
  citations: Citation[];
  unknown: boolean;
  confidence: number;
  retrieval?: RetrievalDiagnostics | null;
  safety_result: SafetyResult;
  blocked: boolean;
  search_session_id?: string | null;
  resolved_reference_index?: number | null;
};

export type Citation = {
  title: string;
  source_name: string;
  source_type: "external_public" | "project_authored" | "demo" | string;
  source_url?: string | null;
  snippet: string;
};

export type ScenarioSpec = {
  scenario_id: string;
  safe_summary: string;
  practice_goal: string;
  counterpart_role: string;
  interaction_mode: string;
  skill_codes: string[];
  context_tags: string[];
};

export type RoleplayMessage = {
  role: "user" | "agent" | "system";
  content: string;
  created_at: string;
};

export type RoleplayGuidance = {
  query: string;
  answer: string;
  citations: Citation[];
  unknown: boolean;
  confidence: number;
  no_guidance_found: boolean;
};

export type RoleplaySession = {
  session_id: string;
  user_id: string;
  scenario: string | null;
  scenario_spec: ScenarioSpec | null;
  difficulty: number;
  status: "active" | "paused" | "completed";
  messages: RoleplayMessage[];
  retrieved_guidance: RoleplayGuidance;
  created_at: string;
  updated_at: string;
};

export type RoleplayStartResponse = {
  session: RoleplaySession;
  opening_message: string;
};

export type RoleplaySessionListResponse = {
  user_id: string;
  sessions: RoleplaySession[];
};

export type RoleplayMessageResponse = {
  session: RoleplaySession;
  response: string;
  safety_result: SafetyResult;
  blocked: boolean;
  llm_usage: LLMUsage;
};

export type RoleplayPauseResponse = {
  session: RoleplaySession;
  message: string;
};

export type RoleplayResumeResponse = {
  session: RoleplaySession;
  message: string;
};

export type RoleplayFeedback = {
  clarity_score: number;
  naturalness_score: number;
  assertiveness_score: number;
  empathy_score: number;
  rubric_breakdown: RoleplayRubricBreakdown[];
  strengths: string[];
  suggestions: string[];
  next_try_prompt: string;
  citations: Citation[];
};

export type RoleplayRubricSignal = {
  name: string;
  label: string;
  present: boolean;
  weight: number;
};

export type RoleplayRubricBreakdown = {
  dimension: string;
  score: number;
  signals: RoleplayRubricSignal[];
  rationale: string;
};

export type RoleplayFeedbackResponse = {
  session: RoleplaySession;
  feedback: RoleplayFeedback;
};

export type WorksheetFields = {
  situation: string | null;
  automatic_thought: string | null;
  emotion: string | null;
  emotion_intensity: number | null;
  evidence_for: string | null;
  evidence_against: string | null;
  alternative_thought: string | null;
  next_action: string | null;
};

export type WorksheetRecord = {
  worksheet_id: string;
  user_id: string;
  source_message: string;
  fields: WorksheetFields;
  citations: Citation[];
  disclaimer: string;
  missing_fields: string[];
  gentle_followup_questions: string[];
  created_at: string;
  updated_at?: string | null;
  completed?: boolean;
};

export type WorksheetCreateResponse = {
  worksheet: WorksheetRecord | null;
  safety_result: SafetyResult;
  missing_fields: string[];
  gentle_followup_questions: string[];
  disclaimer: string;
  blocked: boolean;
  response: string;
  llm_usage: LLMUsage;
};

export type LLMUsage = {
  used: boolean;
  fallback_used: boolean;
  error_category?: string | null;
};

export type HarnessAction =
  | "general_support"
  | "start_roleplay"
  | "create_exposure_plan"
  | "complete_exposure_task"
  | "write_memory"
  | "consent_required"
  | "action_blocked"
  | "skill_failed"
  | "roleplay_started"
  | "worksheet_created"
  | "exposure_plan_created"
  | "support_resources_queried"
  | "clarification_requested"
  | "out_of_scope"
  | "crisis_escalation"
  | string;

export type ProtocolRecord = {
  protocol_id: string;
  user_id: string;
  protocol_type: "consent_request" | string;
  status: "pending" | "approved" | "rejected" | string;
  payload: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type ProtocolResponse = {
  protocol: ProtocolRecord;
};

export type ConsentRequiredDetail = {
  action: "consent_required";
  consent_required: true;
  protocol_id: string;
  protocol_status: string;
  protocol_expires_at?: string | null;
  protocol_request_hash: string;
  harness_action: HarnessAction;
};

export type InterventionStepStatus =
  | "pending"
  | "in_progress"
  | "completed"
  | "skipped"
  | "cancelled"
  | "blocked";

export type InterventionPlanStatus =
  | "pending_consent"
  | "active"
  | "completed"
  | "cancelled"
  | "blocked"
  | "paused";

export type InterventionStepView = {
  order: number;
  step_id: string;
  title: string;
  status: InterventionStepStatus;
  skill: string;
  intensity?: number | null;
  requires_consent: boolean;
  protocol_id?: string | null;
  stop_condition?: string | null;
  result_summary?: string | null;
  is_current: boolean;
};

export type InterventionPlanView = {
  plan_id: string;
  user_id: string;
  session_id: string;
  status: InterventionPlanStatus;
  protocol_id?: string | null;
  current_step_id?: string | null;
  completed_steps: number;
  total_steps: number;
  progress_ratio: number;
  timeline: InterventionStepView[];
  created_at: string;
  updated_at: string;
};

export type InterventionPlanResponse = {
  plan: InterventionPlanView;
};

export type InterventionPlanListResponse = {
  user_id: string;
  plans: InterventionPlanView[];
};

export type ExposureTask = {
  task_id: string;
  title: string;
  description: string;
  difficulty: number;
  estimated_time_minutes: number;
  success_criteria: string;
  fallback_task: string;
  citations: Citation[];
};

export type ExposureAttempt = {
  task_id: string;
  status: "completed" | "skipped" | "too_hard";
  anxiety_before: number;
  anxiety_after: number;
  reflection: string;
  created_at: string;
};

export type ExposurePlan = {
  plan_id: string;
  user_id: string;
  target_scenario: string;
  current_anxiety_level: number;
  previous_attempts: string[];
  tasks: ExposureTask[];
  attempts: ExposureAttempt[];
  recommended_next_task_id: string | null;
  disclaimer: string;
  created_at: string;
  updated_at: string;
};

export type ExposurePlanResponse = {
  plan: ExposurePlan | null;
  intervention_plan_id?: string | null;
  intervention_plan?: InterventionPlanView | null;
  safety_result: SafetyResult;
  blocked: boolean;
  response: string;
};

export type ExposureCompleteResponse = {
  plan: ExposurePlan;
  next_task: ExposureTask | null;
  adjustment_reason: string;
};

export type UserExposureResponse = {
  user_id: string;
  plan: ExposurePlan | null;
  intervention_plan_id?: string | null;
  intervention_plan?: InterventionPlanView | null;
};
