export type AssetRef = {
  asset_id: string;
  version: number;
};

export type AssetRecord = {
  ref: AssetRef;
  kind: "image" | "video" | string;
  media_type: string;
  byte_size: number;
  width: number | null;
  height: number | null;
  duration_seconds: number | null;
  parent: AssetRef | null;
};

export type RuntimeStatus =
  | "queued"
  | "running"
  | "waiting_review"
  | "completed"
  | "partial"
  | "failed"
  | "cancelled";

export type InspectionResult = "pending" | "normal" | "defects_found" | "inconclusive";
export type ReviewStatus = "tentative" | "confirmed" | "rejected" | "needs_review";
export type DetectionLabel = "AJ" | "BX" | "FS" | "PL" | "BLOCK" | "OTHER";

export type EvidenceRef = {
  asset: AssetRef;
  region: { width: number; height: number; bbox_xyxy: [number, number, number, number] } | null;
  frame_index: number | null;
  timestamp_seconds: number | null;
};

export type Finding = {
  finding_id: string;
  task_id: string;
  canonical_label: DetectionLabel | null;
  open_label: string | null;
  review_status: ReviewStatus;
  source: "detector" | "open_detector" | "vlm" | "human";
  score: number | null;
  evidence: EvidenceRef[];
  confirmed_by: "rules" | "human" | null;
};

export type CoverageSummary = {
  sufficient_coverage: boolean;
  sufficient_quality: boolean;
  negative_audit_complete: boolean;
};

export type TaskSnapshot = {
  schema_version: "1";
  task_id: string;
  runtime_status: RuntimeStatus;
  inspection_result: InspectionResult;
  stage: string;
  stage_progress: number;
  overall_progress: number;
  processed_frames: number;
  processed_chunks: number;
  active_tool: string | null;
  retry_count: number;
  waiting_reason: string | null;
  updated_at: string;
  findings: Finding[];
  coverage: CoverageSummary;
};

export type TaskRecord = {
  parent_task_id?: string | null;
  request_id: string;
  session_id: string;
  input_asset: AssetRef;
  instruction: string;
  created_at: string;
  snapshot: TaskSnapshot;
};

export type SessionRecord = {
  session_id: string;
  created_at: string;
};

export type SessionDetail = {
  session: SessionRecord;
  tasks: TaskRecord[];
};

export type TaskSubmission = {
  request_id: string;
  session_id: string;
  input_asset: AssetRef;
  instruction: string;
};

export type TurnStatus = "queued" | "running" | "completed" | "failed";
export type TurnStage = "queued" | "planning" | "executing" | "finished";

export type TurnSubmission = {
  request_id: string;
  text: string;
  input_asset: AssetRef | null;
  task_id: string | null;
};

export type TurnRecord = TurnSubmission & {
  session_id: string;
  created_at: string;
  updated_at: string;
  status: TurnStatus;
  stage: TurnStage;
  reply: string | null;
  linked_task_id: string | null;
  error_code: string | null;
  attempts: number;
};
