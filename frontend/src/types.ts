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

export type TaskSnapshot = {
  schema_version: "1";
  task_id: string;
  runtime_status: RuntimeStatus;
  inspection_result: string;
  stage: string;
  stage_progress: number;
  overall_progress: number;
  processed_frames: number;
  processed_chunks: number;
  active_tool: string | null;
  retry_count: number;
  waiting_reason: string | null;
  updated_at: string;
};

export type TaskRecord = {
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
