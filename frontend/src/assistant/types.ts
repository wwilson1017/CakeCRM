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
  // 'failed' — the write was approved but its executor returned an error (e.g. a
  // Gmail draft when the connection dropped); without it a failed action wrongly
  // renders as "Approved" (issue #8).
  status: 'pending' | 'approved' | 'denied' | 'failed';
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
