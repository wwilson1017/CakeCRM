/**
 * The Contact and Company picker contract a deal form uses — the queries, the quick-creates and
 * the label/match accessors, in one place (issues #123, #126, #75).
 *
 * Two forms link a deal to a contact and a company: `DealForm` on CREATE and `DealDetailBody`'s
 * inline editor on EDIT. They are separate components by design — creating opens a modal, editing
 * happens in the detail panel — but the rules below are not about which form is on screen, and a
 * second copy of them is how the two drift apart (a quick-create that stops going through
 * `/resolve`, a company label that loses its archived marker on one side only).
 *
 * Module scope, and it must stay there: `RecordCombobox` lists `search` in an effect's
 * dependencies, so an inline arrow would re-fire the query on every render.
 */
import { api } from '../core/api/client';
import type { CrmCompany, CrmContact } from '../core/types';

// How many rows the pickers request per query. Small on purpose: this is a search-as-you-type
// list a human reads, not a page to browse — the old limit=200 fetch existed only because a
// <select> had to contain every option it could ever show.
//
// Accepted consequence at single-install scale: the combobox suppresses `Create "…"` on an exact
// name match it can SEE, so a contact whose name matches exactly can in principle be pushed off
// this page by 20 fresher rows that merely mention the same text in their company or notes
// (contact search is ILIKE over several columns, ordered updated_at DESC), and the picker would
// then offer to create a duplicate. Companies are immune by construction — quick-create resolves
// through uq_companies_name_ci server-side — so this is a contact-only, low-frequency
// data-quality risk, not a correctness hole. The fix if it ever bites is server-side: rank exact
// name matches first.
export const PICKER_LIMIT = 20;

export const searchContacts = (query: string) =>
  api<{ contacts: CrmContact[] }>(
    `/api/crm/contacts?limit=${PICKER_LIMIT}${query ? `&q=${encodeURIComponent(query)}` : ''}`,
  ).then(d => d.contacts);

export const searchCompanies = (query: string) =>
  api<{ companies: CrmCompany[] }>(
    `/api/crm/companies?limit=${PICKER_LIMIT}${query ? `&q=${encodeURIComponent(query)}` : ''}`,
  ).then(d => d.companies);

// Quick-create sends the NAME ONLY. Omitting owner_id is what lets the server assign the caller
// (_create_payload distinguishes absent from explicit null), which is the same rule the untouched
// deal create relies on — the client never names an owner it wasn't asked for.
export const createContact = (name: string) =>
  api<CrmContact>('/api/crm/contacts', { method: 'POST', body: JSON.stringify({ name }) });

// Not POST /companies: that route INSERTs unconditionally and 400s on a name that already exists
// case/whitespace-insensitively. /resolve is the #35 get-or-create primitive, so typing an
// existing company's name here links to it instead of failing (issue #123).
export const createCompany = (name: string) =>
  api<CrmCompany>('/api/crm/companies/resolve', { method: 'POST', body: JSON.stringify({ name }) });

export const contactLabelOf = (c: CrmContact) => c.name;
export const contactSublabelOf = (c: CrmContact) => c.company_name || c.company || '';

// Display carries the archived marker; MATCHING must not, or typing an archived company's real
// name reports no exact match and the list offers to create the row above it.
export const companyLabelOf = (co: CrmCompany) =>
  (co.status === 'archived' ? `${co.name} (archived)` : co.name);
export const companyNameOf = (co: CrmCompany) => co.name;
export const companySublabelOf = (co: CrmCompany) => co.domain || '';

export const recordId = (r: { id: number }) => r.id;
