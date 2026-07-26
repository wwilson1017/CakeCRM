// CakeCRM — assistant chat types (single built-in assistant).

export type ToolMode = 'read-only' | 'normal' | 'power';

export interface ToolCallInfo {
  tool: string;
  toolUseId: string;
  args?: Record<string, unknown>;
  description?: string;
  result?: unknown; // preview (history) or live tool_end result
  status: 'running' | 'done';
  elapsedMs?: number;
}

export interface PendingConfirmation {
  tool: string;
  toolUseId: string;
  msgId?: string; // the persisted assistant row; disambiguates reused tool_use_ids
  args: Record<string, unknown>;
  description?: string;
  status: 'pending' | 'approved' | 'denied';
  result?: unknown;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  toolCalls?: ToolCallInfo[];
  pendingConfirmations?: PendingConfirmation[]; // one per tool_use_id
  model?: string;
  streaming?: boolean;
  error?: boolean;
}

export interface Conversation {
  id: string;
  title: string;
  message_count?: number;
  preview?: string;
  updated_at?: string;
}

export interface ContextUsage {
  contextTokens: number;
  contextWindow: number;
}

// CRM record context (issue #14) ---------------------------------------------

export type ActiveRecordType = 'deal' | 'contact' | 'company';

export interface ActiveRecordContext {
  recordType: ActiveRecordType;
  recordId: number;
  /** Display only (drawer chip / quick-actions header). NEVER sent to the
   *  backend — the wire payload carries record_type + record_id exclusively. */
  label?: string;
}

// Raw server shapes (SSE events + history rows) ------------------------------

export interface ServerToolCall {
  tool: string;
  tool_use_id: string;
  args?: Record<string, unknown>;
  result?: unknown;
}

export interface ServerMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  tool_calls?: ServerToolCall[] | null;
  model?: string;
}
