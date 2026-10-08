export type UserRole = "WRITER" | "REVIEWER" | "APPROVER";
export type ReviewStatus = "AWAITING_REVIEW" | "APPROVED" | "REJECTED";
export type EditAction = "replace" | "append";
export type ProposalDecision = "update_required" | "no_update" | "human_investigation";

export interface User {
  id: number;
  username: string;
  role: UserRole;
}

export interface DocumentEdit {
  id: number;
  file_path: string;
  section_heading: string;
  action: EditAction;
  content: string;
  original_content: string;
  evidence: string[];
  start_line: number | null;
}

export interface Proposal {
  id: number;
  model_name: string;
  prompt_version: string;
  decision: ProposalDecision;
  rationale: string;
  raw_response: string;
  edits: DocumentEdit[];
}

export interface AuditEvent {
  id: number;
  document_edit_id: number;
  actor_id: number;
  actor_username: string;
  previous_content: string;
  updated_content: string;
  created_at: string;
}

export interface ReviewSessionSummary {
  id: string;
  source_commit: string;
  head_commit: string;
  status: ReviewStatus;
  created_by: number;
  updated_at: string;
  creator: User;
}

export interface ReviewSession extends ReviewSessionSummary {
  reviewed_by: number | null;
  review_note: string | null;
  source_diff: string;
  changed_files: string[];
  evidence_refs: string[];
  analysis_decision: string;
  analysis_rationale: string;
  patch_diff: string | null;
  proposal: Proposal | null;
  audit_events: AuditEvent[];
}

export type CurrentUser = User;

export interface CreateSessionInput {
  source_repo: string;
  docs_repo: string;
  base_revision: string;
  head_revision: string;
  model_name: string;
  ollama_url?: string;
}

export interface ApiErrorShape {
  detail?: string;
}