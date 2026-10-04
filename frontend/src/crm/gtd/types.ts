// Todo-GTD — shared types (mirrors backend/crm/gtd_common.py).

export type TodoStatus =
  | 'inbox'
  | 'next_action'
  | 'waiting_for'
  | 'delegated'
  | 'someday_maybe'
  | 'done'
  | 'dropped';

export type TodoProjectStatus = 'active' | 'someday' | 'completed' | 'dropped';

export interface Todo {
  id: number;
  title: string;
  /** The `todos.description` column, aliased — GTD calls it notes. */
  notes: string;
  project_id: number | null;
  project_name: string | null;
  context: string;
  tags: string[];
  status: TodoStatus;
  star: boolean;
  /** YYYY-MM-DD, or '' for no due date (the CRM's TEXT NOT NULL convention). */
  due_date: string;
  /** Bring-back date (#261), YYYY-MM-DD or null: hidden from the working lists until that
   * day, then on Today. Not a deadline. Optional only so older fixtures type-check; the
   * server always sends it. */
  bring_back_on?: string | null;
  /** '' | daily | weekdays | weekly | monthly | yearly | every:N */
  repeat: string;
  /** Repeating todos only: star the next occurrence when it comes due today. */
  auto_star_on_due: boolean;
  source: string;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  // The CRM link. CakeCRM's todos table already carries these, which is why the
  // blueprint's Projects-card connector was not ported — a todo points at a contact
  // or a deal instead, and the row carries the label needed to render the chip.
  contact_id: number | null;
  deal_id: number | null;
  contact_name?: string | null;
  deal_title?: string | null;
}

export interface TodoProject {
  id: number;
  name: string;
  notes: string;
  /** One line: why the project exists (#262). '' when unset. */
  purpose: string;
  /** One line: what done looks like (#262). '' when unset. */
  outcome: string;
  status: TodoProjectStatus;
  open_count: number;
  created_at: string;
  updated_at: string;
}

export interface TodoFilters {
  contexts: string[];
  tags: string[];
  status_counts: Record<TodoStatus, number>;
  /** The install's IANA timezone (#259) — every client "today" derives from it. */
  tz?: string;
}
