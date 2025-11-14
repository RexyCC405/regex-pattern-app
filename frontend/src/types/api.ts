export type UploadResp = {
  file_id: number;
  filename: string;
  is_excel: boolean;
  columns: string[];
  head: any[];
};

export type FindStats = {
  total_matches: number;
  per_column: Record<string, number>;
  rows_with_hits: number;
  changed_row_indices?: number[];
  head_hit_row_indices?: number[]; // legacy backend support
};

export type IntentInfo = {
  intent: "find" | "replace";
  replacement?: string | null;
  confidence?: number;
  reason?: string;
};
