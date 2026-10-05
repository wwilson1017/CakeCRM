// CakeCRM — assistant chat types (single built-in assistant).

import type { SettingsSectionId } from '../crm/settingsSections';

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

// CRM record context (issue #14) ---------------------------------------------

export type ActiveRecordType = 'deal' | 'contact' | 'company';

export interface ActiveRecordContext {
  recordType: ActiveRecordType;
  recordId: number;
  /** Display only (drawer chip / quick-actions header). NEVER sent to the
   *  backend — the wire payload carries record_type + record_id exclusively. */
  label?: string;
}

// Settings page context (issue #200) -----------------------------------------

/** The settings section open behind the drawer. A SEPARATE wire field from the record
 *  context, never a widening of it — the two answer different questions and a turn can
 *  carry both. `SettingsSectionId` is imported rather than restated so the drawer can
 *  never name a section the Settings page does not have; the BACKEND restates it as a
 *  Pydantic Literal on purpose, because that is the seam that has to be a closed set. */
export interface SettingsPageContext {
  page: 'settings';
  section: SettingsSectionId;
}

/** The GTD weekly Review page (#263). No sections — the page id is the whole context. */
export interface TodoReviewPageContext {
  page: 'todo_review';
}

/** Every page the drawer may describe. The backend restates this as a discriminated
 *  union of Literals, so an unknown page is refused at the seam. */
export type PageContext = SettingsPageContext | TodoReviewPageContext;

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
