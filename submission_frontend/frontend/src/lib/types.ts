export type A2UIMessageName = "createSurface" | "updateDataModel" | "updateComponents";

export type ReadinessState =
  | "Still learning"
  | "Needs clarification"
  | "Ready for recommendation"
  | "Security review required"
  | string;

export type A2UIBinding = {
  path?: string;
};

export type A2UIAction = {
  event?: string;
  payload?: Record<string, unknown>;
};

export type A2UIComponent = {
  id: string;
  component: string;
  props?: Record<string, unknown>;
  binding?: A2UIBinding;
  action?: A2UIAction;
};

export type A2UIMessage = {
  version?: string;
  message: A2UIMessageName | string;
  surfaceId: string;
  catalogId?: string;
  root?: string;
  data?: Record<string, unknown>;
  components?: A2UIComponent[];
};

export type A2UISurface = {
  surfaceId: string;
  catalogId?: string;
  root: string;
  data: Record<string, unknown>;
  components: A2UIComponent[];
};

export type DocumentReviewItem = {
  field_label: string;
  extracted_value: string;
  confidence_state: string;
  needs_review: boolean;
  explanation: string;
};

export type SessionState = {
  session_id: string;
  current_stage: string;
  sanitized_user_story: string;
  redacted_categories: string[];
  known_facts: Record<string, unknown>;
  missing_facts: string[];
  candidate_entities: string[];
  readiness_state: ReadinessState;
  a2ui_messages: A2UIMessage[];
  recommendation: string | null;
  explanation: string | null;
  security_flags: string[];
  document_review_items: DocumentReviewItem[];
  interrupt_id: string | null;
  requested_input_payload: Record<string, unknown> | null;
  runtime_available: boolean;
  raw_session_state: Record<string, unknown>;
};
