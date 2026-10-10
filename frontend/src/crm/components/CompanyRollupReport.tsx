/**
 * The company rollup itself (issue #144) — one continuous scroll, no inner tabs.
 *
 * Deliberately not tabbed: tabs would reintroduce exactly the clicking-around this report
 * exists to remove. Header and chips, then every deal, then every contact, then the merged
 * timeline, in that order, all on one page.
 *
 * Every sub-component is declared at MODULE scope. Declaring them inside the page component
 * gives them a new identity on every render, which remounts the whole subtree — so an
 * unrelated state change would collapse every row the reader had opened.
 *
 * Expanded rows render entirely from the rollup payload: custom fields and open todos ride
 * it, batched server-side. Opening a row therefore costs no request, which is what makes
 * "Expand all" safe and what makes the row's field list complete — including the fields
 * nobody has filled in, which is the issue's load-bearing requirement.
 *
 * "Every field" is meant literally, derived timestamps included: a lead score or a touch
 * count without the time it was computed is half a fact. The ONE deliberate omission is
 * `company_id`, on both deals and contacts — it is the company whose report this is, so
 * printing it would be a row that reads the same on every record of every account.
 */
import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../../core/api/client';
import type { CrmActivity, CrmCompanyRollup, CrmRollupField, CrmTodo } from '../../core/types';
import {
  EXPAND_ALL_MAX, isArchivedContact, isArchivedDeal, partitionArchived,
} from '../companyRollup';
import { STAGE_COLORS } from '../constants';
import { formatDate } from '../../shared/formatDate';
import { useIsMobile } from '../../shared/useIsMobile';
import { LoadError } from '../../shared/LoadError';
import {
  ACCENT_TEXT, FONT_DISPLAY, INK, INK_DIM, INK_MUTE, LINE, LINE_STRONG, formatNumber, mono, tint,
} from '../../shared/styles';
import { btnSmall, cardStyle, sectionHeading } from '../styles';
import { PriorityBadge, ScorePill, StatusBadge, TouchCountPill } from './badges';
import { displayValue } from './CustomFieldsSection';
import { OwnerName } from './OwnerName';
import { CompanyTimeline } from './CompanyTimeline';

interface Props {
  companyId: number;
  /** Hands the loaded company's name back so a deep link can label the closed picker. */
  onCompanyName?: (name: string) => void;
}

/** The loaded rollup, tagged with the request it belongs to (see the fetch below). */
interface RollupState {
  key: string;
  data: CrmCompanyRollup | null;
  failed: boolean;
}

const EMPTY: ReadonlySet<number> = new Set();
/**
 * Money in the currency it is actually denominated in.
 *
 * `deals.currency` is user-writable free text, so this must not assume USD and must not
 * assume the code is even valid — `Intl` throws a RangeError on an unknown one, and a
 * report is not allowed to blank itself over a typo in a currency field.
 */
const money = (n: number, currency: string): string => {
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency', currency, maximumFractionDigits: 0,
    }).format(n);
  } catch {
    return `${currency} ${Math.round(n).toLocaleString()}`;
  }
};
const dash = '—';

const toggle = (set: ReadonlySet<number>, id: number): ReadonlySet<number> => {
  const next = new Set(set);
  if (!next.delete(id)) next.add(id);
  return next;
};

// ── Small presentational pieces (module scope — see the file docstring) ─────────────

function InfoRow({ label, children }: { label: string; children?: React.ReactNode }) {
  // An unset field still renders its row: "every field" means the row exists and says so,
  // not that the empty ones quietly disappear.
  return (
    <div style={{ display: 'flex', gap: 12, padding: '4px 0', alignItems: 'baseline' }}>
      <span style={{ ...mono(10), minWidth: 130, flexShrink: 0 }}>{label}</span>
      <span style={{ fontSize: 13, color: INK, wordBreak: 'break-word' }}>
        {children === null || children === undefined || children === '' ? (
          <span style={{ color: INK_DIM }}>{dash}</span>
        ) : children}
      </span>
    </div>
  );
}

function SummaryChip({ label, value }: { label: string; value: string }) {
  return (
    <div style={{
      // 5% matches the ink chips elsewhere in the app, and it is one of the two
      // percentages `core/theme/inkContrast.test.ts` cross-products against every ink
      // token in both themes (#68). A new percentage would be an unmeasured surface.
      background: tint(INK, 5), border: `1px solid ${LINE}`, borderRadius: 8,
      padding: '10px 14px', minWidth: 120,
    }}>
      <div style={mono(10)}>{label}</div>
      <div style={{ fontFamily: FONT_DISPLAY, fontSize: 20, color: INK, marginTop: 2 }}>{value}</div>
    </div>
  );
}

function TruncationNotice({ shown, noun }: { shown: number; noun: string }) {
  // Deliberately not "older ones": deals are capped newest-updated-first but contacts are
  // capped alphabetically, so "older" would be wrong for half the callers.
  return (
    <p style={{ fontSize: 12, color: INK_DIM, margin: '10px 0 0' }}>
      Showing the first {shown} {noun} on this company. The rest are not on this page.
    </p>
  );
}

function CustomFields({ fields }: { fields: CrmRollupField[] }) {
  if (fields.length === 0) return null;
  return (
    <div style={{ marginTop: 12 }}>
      <div style={sectionHeading()}>Custom fields</div>
      {fields.map(f => (
        <InfoRow key={f.field_key} label={f.name}>
          {/* One definition of the wire-format-to-label rule, shared with the detail
              pages' editor — a second copy drifts the day another type gets a display
              form. `displayValue` renders an unset field as an em dash, which InfoRow
              already does, so only a set value is passed through it. */}
          {f.value == null || f.value === '' ? null : displayValue(f)}
        </InfoRow>
      ))}
    </div>
  );
}

function ActivityBlock({ activities, truncated }: { activities: CrmActivity[]; truncated: boolean }) {
  return (
    <div style={{ marginTop: 12 }}>
      <div style={sectionHeading()}>Activity</div>
      {activities.length === 0
        ? <p style={{ fontSize: 13, color: INK_DIM, margin: 0 }}>No activity logged.</p>
        : activities.map(a => (
            <div key={a.id} style={{ padding: '4px 0', fontSize: 13, color: INK }}>
              <span style={{ color: ACCENT_TEXT }}>{a.activity}</span>
              {a.note ? <span style={{ color: INK_MUTE }}> · {a.note}</span> : null}
              <span style={{ color: INK_DIM, fontSize: 12 }}> · {formatDate(a.created_at)}</span>
            </div>
          ))}
      {truncated && (
        <p style={{ fontSize: 12, color: INK_DIM, margin: '6px 0 0' }}>
          Only the most recent activity on this record is shown.
        </p>
      )}
    </div>
  );
}

function NotesBlock({ notes }: { notes: string }) {
  if (!notes) return null;
  return (
    <div style={{ marginTop: 12 }}>
      <div style={sectionHeading()}>Notes</div>
      <p style={{ fontSize: 13, color: INK, whiteSpace: 'pre-wrap', margin: 0 }}>{notes}</p>
    </div>
  );
}

function TodoList({ todos, truncated }: { todos: CrmTodo[]; truncated: boolean }) {
  return (
    <div style={{ marginTop: 12 }}>
      <div style={sectionHeading()}>Open todos</div>
      {todos.length === 0
        ? <p style={{ fontSize: 13, color: INK_DIM, margin: 0 }}>No open todos.</p>
        : todos.map(t => (
            <div key={t.id} style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '3px 0' }}>
              <span style={{ fontSize: 13, color: INK }}>{t.title}</span>
              <PriorityBadge priority={t.priority} />
              {t.due_date && <span style={{ fontSize: 12, color: INK_DIM }}>due {t.due_date}</span>}
            </div>
          ))}
      {truncated && (
        <p style={{ fontSize: 12, color: INK_DIM, margin: '6px 0 0' }}>
          Only the first open todos on this deal are shown.
        </p>
      )}
    </div>
  );
}

function SectionShell({
  title, count, rowIds, open, setOpen, children,
}: {
  title: string;
  count: number;
  rowIds: number[];
  open: ReadonlySet<number>;
  setOpen: (next: ReadonlySet<number>) => void;
  children: React.ReactNode;
}) {
  // "Expand all" is WITHHELD above the ceiling rather than disabled — a control that is
  // present but permanently dead is worse than one that is absent. Collapse all and per-row
  // expansion stay available at any count.
  const canExpandAll = rowIds.length > 0 && rowIds.length <= EXPAND_ALL_MAX;
  // Ask whether any row ON SCREEN is open, not whether the Set is non-empty: expanding an
  // archived deal and then switching the filter off leaves its id behind, which would show
  // a "Collapse all" control with nothing to collapse. Same stale-id class the pipeline
  // board's `applicableBulkIds` intersection exists to prevent.
  const anyOpen = rowIds.some(id => open.has(id));
  return (
    <section style={{ borderTop: `1px solid ${LINE_STRONG}`, paddingTop: 24, marginTop: 24 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <h2 style={{ fontFamily: FONT_DISPLAY, fontSize: 18, color: INK, margin: 0 }}>
          {title} ({count})
        </h2>
        {canExpandAll && (
          <button type="button" style={btnSmall} onClick={() => setOpen(new Set(rowIds))}>
            Expand all
          </button>
        )}
        {anyOpen && (
          <button type="button" style={btnSmall} onClick={() => setOpen(new Set())}>
            Collapse all
          </button>
        )}
      </div>
      <div style={{ marginTop: 12 }}>{children}</div>
    </section>
  );
}

function RowShell({
  id, open, onToggle, header, children,
}: {
  id: number;
  open: boolean;
  onToggle: (id: number) => void;
  header: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div style={{ borderBottom: `1px solid ${LINE}`, padding: '10px 0' }}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => onToggle(id)}
        style={{
          display: 'flex', alignItems: 'center', gap: 10, width: '100%', textAlign: 'left',
          background: 'none', border: 'none', padding: 0, cursor: 'pointer', font: 'inherit',
        }}
      >
        <span style={{
          color: INK_DIM, fontSize: 12, flexShrink: 0,
          transform: open ? 'rotate(90deg)' : undefined, transition: 'transform 120ms',
        }}>▶</span>
        <span style={{ flex: 1, minWidth: 0 }}>{header}</span>
      </button>
      {open && <div style={{ padding: '8px 0 4px 22px' }}>{children}</div>}
    </div>
  );
}

type RollupDeal = CrmCompanyRollup['deals'][number];
type RollupContact = CrmCompanyRollup['contacts'][number];

function DealRow({ deal, open, onToggle }: {
  deal: RollupDeal; open: boolean; onToggle: (id: number) => void;
}) {
  const archived = isArchivedDeal(deal);
  const stage = STAGE_COLORS[deal.stage];
  return (
    <RowShell
      id={deal.id}
      open={open}
      onToggle={onToggle}
      header={
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 14, color: INK, fontWeight: 600 }}>{deal.title}</span>
            <span style={{
              ...mono(10), color: stage?.text ?? INK_DIM,
              background: stage?.bg, borderRadius: 4, padding: '2px 6px',
            }}>{deal.stage}</span>
            <span style={{ fontSize: 13, color: INK_MUTE }}>{money(deal.value, deal.currency)}</span>
            <TouchCountPill count={deal.ai_touch_count} />
            <ScorePill score={deal.lead_score} compact />
            {archived && <StatusBadge status="archived" />}
          </div>
          <div style={{ fontSize: 12, color: INK_DIM, marginTop: 2 }}>
            {deal.last_activity_at
              ? `Last activity ${formatDate(deal.last_activity_at)}`
              : 'No activity logged'}
          </div>
        </>
      }
    >
      <InfoRow label="Deal ID">{deal.id}</InfoRow>
      <InfoRow label="Stage">{deal.stage}</InfoRow>
      <InfoRow label="Value">{money(deal.value, deal.currency)}</InfoRow>
      <InfoRow label="Probability">{`${deal.probability}%`}</InfoRow>
      <InfoRow label="Currency">{deal.currency}</InfoRow>
      <InfoRow label="Forecasted close">{deal.expected_close_date}</InfoRow>
      <InfoRow label="Owner"><OwnerName ownerId={deal.owner_id} /></InfoRow>
      <InfoRow label="Contact">
        {deal.contact_id != null
          ? <Link to={`/crm/contacts/${deal.contact_id}`} style={{ color: ACCENT_TEXT }}>
              {deal.contact_name || `contact #${deal.contact_id}`}
            </Link>
          : null}
      </InfoRow>
      <InfoRow label="Lost reason">{deal.lost_reason}</InfoRow>
      {/* A derived number without its derivation time is half a fact: a lead score or a
          touch count is only as trustworthy as how recently it was computed, and both
          columns ride the payload already. `ai_touch_evidence_count` is how many evidence
          lines #56 judged, which is what makes the count auditable. */}
      <InfoRow label="Lead score">{deal.lead_score ?? null}</InfoRow>
      <InfoRow label="Lead score at">
        {deal.lead_score_at ? formatDate(deal.lead_score_at) : null}
      </InfoRow>
      <InfoRow label="AI touches">{deal.ai_touch_count ?? null}</InfoRow>
      <InfoRow label="AI touches at">
        {deal.ai_touch_count_at ? formatDate(deal.ai_touch_count_at) : null}
      </InfoRow>
      <InfoRow label="AI evidence lines">{deal.ai_touch_evidence_count ?? null}</InfoRow>
      <InfoRow label="Archived at">{deal.archived_at ? formatDate(deal.archived_at) : null}</InfoRow>
      <InfoRow label="Created">{formatDate(deal.created_at)}</InfoRow>
      <InfoRow label="Updated">{formatDate(deal.updated_at)}</InfoRow>
      <NotesBlock notes={deal.notes} />
      <CustomFields fields={deal.custom_fields} />
      <TodoList todos={deal.todos} truncated={deal.todos_truncated} />
      <ActivityBlock activities={deal.activities} truncated={deal.activities_truncated} />
    </RowShell>
  );
}

function ContactRow({ contact, open, onToggle }: {
  contact: RollupContact; open: boolean; onToggle: (id: number) => void;
}) {
  return (
    <RowShell
      id={contact.id}
      open={open}
      onToggle={onToggle}
      header={
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 14, color: INK, fontWeight: 600 }}>{contact.name}</span>
            <ScorePill score={contact.lead_score} compact />
            {contact.status !== 'active' && <StatusBadge status={contact.status} />}
          </div>
          <div style={{ fontSize: 12, color: INK_DIM, marginTop: 2 }}>
            {[contact.title, contact.email].filter(Boolean).join(' · ') || dash}
          </div>
        </>
      }
    >
      <InfoRow label="Contact ID">{contact.id}</InfoRow>
      <InfoRow label="Title">{contact.title}</InfoRow>
      <InfoRow label="Email">
        {contact.email
          ? <a href={`mailto:${contact.email}`} style={{ color: ACCENT_TEXT }}>{contact.email}</a>
          : null}
      </InfoRow>
      <InfoRow label="Phone">
        {contact.phone
          ? <a href={`tel:${contact.phone}`} style={{ color: ACCENT_TEXT }}>{contact.phone}</a>
          : null}
      </InfoRow>
      <InfoRow label="Status">{contact.status}</InfoRow>
      <InfoRow label="Source">{contact.source}</InfoRow>
      <InfoRow label="Owner"><OwnerName ownerId={contact.owner_id} /></InfoRow>
      <InfoRow label="Tags">{contact.tags}</InfoRow>
      <InfoRow label="Lead score">{contact.lead_score ?? null}</InfoRow>
      <InfoRow label="Lead score at">
        {contact.lead_score_at ? formatDate(contact.lead_score_at) : null}
      </InfoRow>
      {/* The legacy free-text company field (#35 left it non-authoritative). Shown because
          this is the one page where a value that disagrees with the LINK is visible as a
          disagreement — everywhere else the joined name silently wins. */}
      <InfoRow label="Company (legacy text)">{contact.company}</InfoRow>
      <InfoRow label="Created">{formatDate(contact.created_at)}</InfoRow>
      <InfoRow label="Updated">{formatDate(contact.updated_at)}</InfoRow>
      <div style={{ marginTop: 8 }}>
        <Link to={`/crm/contacts/${contact.id}`} style={{ color: ACCENT_TEXT, fontSize: 13 }}>
          Open contact →
        </Link>
      </div>
      <NotesBlock notes={contact.notes} />
      <CustomFields fields={contact.custom_fields} />
      <ActivityBlock activities={contact.activities} truncated={contact.activities_truncated} />
    </RowShell>
  );
}

// ── The report ──────────────────────────────────────────────────────────────────────

export function CompanyRollupReport({ companyId, onCompanyName }: Props) {
  const isMobile = useIsMobile();
  const [includeArchived, setIncludeArchived] = useState(false);
  // A bump-to-retry counter, part of the request key below.
  const [reloadNonce, setReloadNonce] = useState(0);

  // Row expansion is LOCAL state, and the page mounts this component with key={companyId}.
  // That pairing is the whole design: switching company remounts and so collapses every
  // row (which is what you want — row 3 of one account is not row 3 of another), while
  // toggling the archived filter does not remount, so the expanded rows stay expanded and
  // the previous payload stays on screen while the new one loads (see `rollup` below —
  // keeping the layout mounted is what preserves the scroll position too).
  const [openDeals, setOpenDeals] = useState<ReadonlySet<number>>(EMPTY);
  const [openContacts, setOpenContacts] = useState<ReadonlySet<number>>(EMPTY);

  // One state object tagged with its request, everything else derived during render. The
  // obvious shape — setRollup(null) at the top of the effect — is a cascading render the
  // stricter react-hooks ruleset rejects, and it also lets a response from the previous
  // filter land between the reset and its own fetch.
  const key = `${companyId}:${includeArchived}:${reloadNonce}`;
  const [state, setState] = useState<RollupState | null>(null);
  const current = state && state.key === key ? state : null;
  // While a NEW key is in flight, keep the PREVIOUS payload on screen. Dropping to a bare
  // loading line unmounts every section and the timeline with them, the document collapses
  // to a few hundred pixels, and the browser clamps scrollY to 0 — so ticking the archived
  // checkbox threw the reader back to the top of an account they were reading halfway down.
  // The component is keyed by companyId, so `state` can only ever hold THIS company's data.
  // A failed refetch still falls through to the error card: on failure `current.data` is
  // null and `current` IS `state`, so there is no stale payload to fall back to.
  const rollup = current?.data ?? state?.data ?? null;
  const refreshing = current === null;
  const failed = current?.failed ?? false;

  useEffect(() => {
    let stale = false;
    api<CrmCompanyRollup>(
      `/api/crm/companies/${companyId}/report${includeArchived ? '?include_archived=true' : ''}`,
    ).then(
      data => { if (!stale) setState({ key, data, failed: false }); },
      () => { if (!stale) setState({ key, data: null, failed: true }); },
    );
    return () => { stale = true; };
  }, [companyId, includeArchived, key]);

  // The DECORATED label, so a deep-linked `?company=7` shows "Acme (archived)" in the
  // closed picker exactly as picking it from the list would. Handing back the raw name
  // silently drops the marker on precisely the companies where it matters.
  const label = rollup ? (rollup.company.status === 'archived'
    ? `${rollup.company.name} (archived)` : rollup.company.name) : undefined;
  useEffect(() => { if (label) onCompanyName?.(label); }, [label, onCompanyName]);

  const toggleDeal = useCallback((id: number) => setOpenDeals(prev => toggle(prev, id)), []);
  const toggleContact = useCallback((id: number) => setOpenContacts(prev => toggle(prev, id)), []);

  if (failed && !rollup) {
    return (
      <LoadError
        label="Couldn't load this company report"
        onRetry={() => setReloadNonce(n => n + 1)}
      />
    );
  }
  if (!rollup) {
    return <p style={{ color: INK_DIM, fontSize: 13 }}>Loading company report…</p>;
  }

  const { company, summary } = rollup;
  const deals = partitionArchived(rollup.deals, isArchivedDeal);
  const contacts = partitionArchived(rollup.contacts, isArchivedContact);
  const dealRows = [...deals.live, ...deals.archived];
  const contactRows = [...contacts.live, ...contacts.archived];
  const subline = [company.industry, company.domain, company.phone, company.address]
    .filter(Boolean).join('  ·  ');

  return (
    <div style={{ marginTop: 24 }}>
      <div style={{ ...cardStyle, padding: isMobile ? 14 : 20 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <h2 style={{
            fontFamily: FONT_DISPLAY, fontSize: isMobile ? 20 : 26, color: INK, margin: 0,
          }}>{company.name}</h2>
          <StatusBadge status={company.status} />
          <Link to={`/crm/companies/${company.id}`} style={{ color: ACCENT_TEXT, fontSize: 13 }}>
            Open company →
          </Link>
        </div>
        {subline && (
          <p style={{ fontSize: 13, color: INK_MUTE, margin: '6px 0 0' }}>{subline}</p>
        )}

        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', margin: '18px 0 4px' }}>
          <SummaryChip label="Open deals" value={formatNumber(summary.open_deal_count)} />
          {/* The server reports a single currency only when every open deal agrees on one.
              When they do not, there is no honest total to show — `deals.currency` is
              user-writable, so summing across currencies would state a number that is
              simply false. The per-deal values below are each in their own currency. */}
          <SummaryChip
            label="Open value"
            value={summary.open_deal_currency || summary.open_deal_count === 0
              ? money(summary.open_deal_value, summary.open_deal_currency || 'USD')
              : 'Mixed currencies'}
          />
          {/* Labelled precisely, because the number is precise: the server counts
              `status = 'active'`, so inactive and archived contacts are both out, while
              the section below lists every contact and states its own total. Two different
              questions, each answered honestly — not one number fitting neither label. */}
          <SummaryChip label="Active contacts" value={formatNumber(summary.contact_count)} />
        </div>

        <InfoRow label="Industry">{company.industry}</InfoRow>
        <InfoRow label="Domain">
          {company.domain
            ? <a href={`https://${company.domain}`} target="_blank" rel="noreferrer"
                 style={{ color: ACCENT_TEXT }}>{company.domain}</a>
            : null}
        </InfoRow>
        <InfoRow label="Phone">
          {company.phone
            ? <a href={`tel:${company.phone}`} style={{ color: ACCENT_TEXT }}>{company.phone}</a>
            : null}
        </InfoRow>
        <InfoRow label="Address">{company.address}</InfoRow>
        <InfoRow label="Source">{company.source}</InfoRow>
        <InfoRow label="Owner"><OwnerName ownerId={company.owner_id} /></InfoRow>
        <InfoRow label="Created">{formatDate(company.created_at)}</InfoRow>
        <InfoRow label="Updated">{formatDate(company.updated_at)}</InfoRow>
        <NotesBlock notes={company.notes} />
        <CustomFields fields={rollup.company_custom_fields} />

        <label style={{
          display: 'flex', alignItems: 'center', gap: 8, marginTop: 18,
          fontSize: 13, color: INK_MUTE, cursor: 'pointer',
        }}>
          <input
            type="checkbox"
            checked={includeArchived}
            onChange={e => setIncludeArchived(e.target.checked)}
          />
          Include archived deals and archived notes
          {refreshing && <span style={{ color: INK_DIM }}>· refreshing…</span>}
        </label>
      </div>

      <SectionShell
        title="Deals" count={dealRows.length} rowIds={dealRows.map(d => d.id)}
        open={openDeals} setOpen={setOpenDeals}
      >
        {dealRows.length === 0
          ? <p style={{ fontSize: 13, color: INK_DIM }}>No deals on this company yet.</p>
          : dealRows.map(d => (
              <DealRow key={d.id} deal={d} open={openDeals.has(d.id)} onToggle={toggleDeal} />
            ))}
        {rollup.deals_truncated && <TruncationNotice shown={dealRows.length} noun="deals" />}
      </SectionShell>

      <SectionShell
        title="Contacts" count={contactRows.length} rowIds={contactRows.map(c => c.id)}
        open={openContacts} setOpen={setOpenContacts}
      >
        {contactRows.length === 0
          ? <p style={{ fontSize: 13, color: INK_DIM }}>No contacts on this company yet.</p>
          : contactRows.map(c => (
              <ContactRow key={c.id} contact={c} open={openContacts.has(c.id)} onToggle={toggleContact} />
            ))}
        {rollup.contacts_truncated && <TruncationNotice shown={contactRows.length} noun="contacts" />}
      </SectionShell>

      <CompanyTimeline companyId={companyId} includeArchived={includeArchived} />
    </div>
  );
}
