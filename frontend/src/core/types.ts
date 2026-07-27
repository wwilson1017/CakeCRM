// Shared CRM API types (mirrors the backend crm/service.py response shapes).

export interface CrmContact {
  id: number;
  name: string;
  email: string;
  phone: string;
  company: string;
  company_id: number | null;
  company_name?: string; // detail joins only (LEFT JOIN companies)
  title: string;
  source: string;
  status: string;
  tags: string;
  notes: string;
  created_at: string;
  updated_at: string;
  lead_score?: number | null;           // computed lead score 0-100 (issue #18); null = never scored
  lead_score_at?: string | null;
  // Detail view extras
  deals?: CrmDeal[];
  tasks?: CrmTask[];
  activity?: CrmActivity[];
}

export interface CrmDeal {
  id: number;
  contact_id: number | null;
  contact_name?: string;
  company_id: number | null;
  company_name?: string; // detail joins only (LEFT JOIN companies)
  title: string;
  stage: string;
  value: number;
  notes: string;
  expected_close_date: string;
  probability: number;
  currency: string;
  created_at: string;
  updated_at: string;
  ai_touch_count?: number | null;       // AI-estimated touch count (issue #16); null = uncomputed
  ai_touch_count_at?: string | null;
  lead_score?: number | null;           // computed lead score 0-100 (issue #18); null = never scored
  lead_score_at?: string | null;
  activity?: CrmActivity[];
}

// Field provenance (issue #16): which standard fields the AI assistant wrote. A row is a
// live badge only while unconfirmed AND not stale (snapshot still equals the live value).
export interface FieldProvenance {
  id: number;
  entity_type: string;
  entity_id: number;
  field_name: string;
  value_snapshot: string | null;
  source: string;
  source_detail: string | null;
  confidence: number | null;
  populated_at: string;
  confirmed_at: string | null;
  stale?: boolean;
}

export interface CrmCompany {
  id: number;
  name: string;
  domain: string;
  industry: string;
  phone: string;
  address: string;
  notes: string;
  source: string;
  status: string;
  created_at: string;
  updated_at: string;
  // Detail view extras (rolled-up)
  contacts?: CrmContact[];
  deals?: CrmDeal[];
  activity?: CrmActivity[];
  open_deal_value?: number;
}

export interface CrmTask {
  id: number;
  contact_id: number | null;
  deal_id: number | null;
  contact_name?: string;
  deal_title?: string;
  title: string;
  description: string;
  due_date: string;
  completed: number;
  priority: string;
  created_at: string;
  updated_at: string;
}

export interface CrmActivity {
  id: number;
  contact_id: number | null;
  deal_id: number | null;
  contact_name?: string;
  deal_title?: string;
  activity: string;
  note: string;
  created_at: string;
}

export interface CrmNote {
  id: number;
  entity_type: string;
  entity_id: number;
  message: string;
  created_at: string;
  updated_at: string | null;
  archived: number;
}

// Custom fields (issue #19). A definition is the user-authored schema; a value row
// is one definition joined to a specific entity's value (null when unset).
export interface CrmFieldDefinition {
  id: number;
  entity_type: string;              // 'contact' | 'company' | 'deal'
  name: string;
  field_key: string;
  field_type: string;               // 'text' | 'number' | 'boolean' | 'date' | 'select'
  dropdown_options: string[] | null;
  is_required: number;              // 0 | 1 (backend INTEGER flag)
  display_order: number;
  created_at: string;
  updated_at: string;
}

export interface CrmFieldValue {
  field_id: number;
  name: string;
  field_key: string;
  field_type: string;
  dropdown_options: string[] | null;
  is_required: number;
  value: string | null;
  value_updated_at: string | null;
  updated_by_email: string | null;
}

// PUT /{entity}/{id}/fields response.
export interface CrmFieldValuesResult {
  ok: boolean;
  updated: number;
  errors: string[];
}

export interface CrmDashboard {
  total_contacts: number;
  contacts_by_status: Record<string, number>;
  pipeline_by_stage: { stage: string; count: number; total_value: number }[];
  total_pipeline_value: number;
  overdue_tasks: number;
  pending_tasks: number;
  recent_activity: CrmActivity[];
  top_deals: CrmDeal[];
}
