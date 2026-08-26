/**
 * The deal detail BODY — the interior of the deleted `DealDetailSheet`, now rendered inside
 * `shared/collection`'s `CollectionDetail`.
 *
 * The layer owns the shell: title chrome, ‹ › record navigation, the focus trap, and the close
 * CONTRACT (`denyEscapeBackdrop` keeps the CRM's pinned "Escape and a backdrop click do not
 * close" policy). This body owns the form — and one close guard.
 *
 * Three things moved rather than changed:
 *
 *  • **Copy-link and Edit are body actions now.** `CollectionDetail`'s header carries only the
 *    ‹ › arrows, so the buttons that used to ride the sheet's footer sit in the body's own action
 *    row. The address bar is NOT a copy source — `PipelinePage` strips `?deal=` the moment it
 *    reads it — so this button is the only way a rep hands someone a link to the deal they are
 *    looking at. The shape lives in `crm/dealDeepLink.ts`, which both halves import.
 *
 *  • **`editing` / `form` are body-local.** The layer keys the body by record id and remounts on
 *    ‹ › nav, so a half-typed edit can never follow the user to the next deal. That is also why
 *    there is no reset-on-id effect here: a fresh mount already has fresh state. Editing used to
 *    open the separate `DealForm` modal; that form is create-only now.
 *
 *  • **ONE close guard, composing every dirty source this body has.** `registerCloseGuard`
 *    REPLACES rather than stacks, so a body registers one guard for all of its drafts, not one
 *    per draft. It goes through `confirmDiscardOn`, which prompts only for the reasons the app
 *    guard will actually honour: the layer asks the BODY first, so an unconditional prompt would
 *    put "Discard unsaved changes?" on screen for an Escape keypress and then decline to close
 *    whatever the user answered.
 *
 *    That guard covers the shell's leave paths. The body's OWN leave paths — Mark Won, Mark Lost,
 *    and the contact/company links — never reach `CollectionDetail.request()`, so they ask the
 *    same `canLeave` directly. One function, every exit.
 *
 *    KNOWN GAP, disclosed rather than glossed: it covers this body's edit form and quick-log
 *    draft, and nothing else. `CustomFieldsSection` and `NotesThread`'s composer hold private
 *    draft state behind no exposed dirty signal (both are shared components), so ‹ › nav can
 *    still discard one of those. Not a regression — before this migration no CRM surface had any
 *    guard and there was no ‹ › nav at all — but it is a new way to lose a draft, and plumbing a
 *    dirty channel through those two is its own piece of work.
 *
 * `onBoard === false` means the layer resolved this deal through `DetailConfig.loadById` rather
 * than finding it in the host's array — an archived deal, or one a shared link opened before the
 * board holds it. Such a deal needs its own READ channel: the prop is `loadById`'s one-shot
 * result that no host array will ever replace, so without `fetched` a save would repaint the
 * panel with pre-save values. `stageWritable` is a SEPARATE flag rather than `!onBoard`, because
 * the two questions differ per host: the pipeline hides board-position writes for a deal its
 * board is not showing, while the dashboard has no board to be off and every deal it can open is
 * live, so hiding Mark Lost on its "Needs a touch" list would break a real flow.
 *
 * Residual, disclosed: the overlay's TITLE is the layer's, taken from the record it resolved, so
 * renaming an off-board deal leaves the header stale until the panel is reopened. Closing that is
 * a change to the shared detail contract, not a change here.
 *
 * Ported from the CAKE OS blueprint's `DealDetailBody`. Its `ImageGallery` (no media store here),
 * `DealTodos` (a different directory model), `ScoreBreakdown` (no per-factor endpoint) and
 * `LostReasonModal` (the REST route cannot set `lost_reason`) are deliberately absent.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../core/api/client';
import type { CrmActivity, CrmCompany, CrmContact, CrmDeal } from '../../core/types';
import { confirmDiscardOn, type DetailCloseReason, type DetailRenderContext } from '../../shared/collection';
import {
  ACCENT, ACCENT_INK, CORAL, FONT_DISPLAY, GOLD, INK, INK_DIM, INK_MUTE, LINE, LINE_STRONG,
  SAGE, inputStyle, labelStyle, mono,
} from '../../shared/styles';
import { toast } from '../../shared/toast';
import { OPEN_STAGES, STAGE_COLORS, STAGE_ORDER } from '../constants';
import { dealDeepLink } from '../dealDeepLink';
import { btnDanger, btnPrimary, btnSecondary } from '../styles';
import { usePublishActiveRecord } from '../RecordContext';
import { useProvenance } from '../useProvenance';
import { useUsers } from '../useUsers';
import { ActivityTimeline } from './ActivityTimeline';
import { AiTouchDetail } from './AiTouchDetail';
import { ScorePill } from './badges';
import { CustomFieldsSection } from './CustomFieldsSection';
import { NotesThread } from './NotesThread';
import { OwnerSelect } from './OwnerSelect';
import { ProvenanceBadge } from './ProvenanceBadge';

/**
 * The columns an inline save may write. `stage` is one of them on purpose: it rides the SAME PUT
 * as the rest, because the backend settles `probability` to 100/0 inside the transaction that
 * changes the stage. Split across two requests, a "Stage → won, Probability → 50" save is a race
 * whose loser silently wins.
 */
export type DealPatch = Partial<Pick<CrmDeal,
  'title' | 'value' | 'contact_id' | 'company_id' | 'owner_id'
  | 'expected_close_date' | 'probability' | 'notes' | 'stage'>>;

interface DealFormState {
  title: string;
  stage: string;
  value: string;
  probability: string;
  expected_close_date: string;
  notes: string;
  contact_id: number | null;
  company_id: number | null;
  owner_id: number | null;
}

/** Snapshot a record into form state, normalising every blank to `''`.
 *
 *  Both the working copy and the baseline come through here, which is what makes the dirty
 *  comparison honest: a column that is `null` on the record and `''` in the form would otherwise
 *  read as an edit, and merely opening the form over a deal with any blank column would prompt
 *  "Discard unsaved changes?" on the way out. */
function toDealForm(deal: CrmDeal): DealFormState {
  return {
    title: deal.title ?? '',
    stage: deal.stage ?? 'lead',
    value: deal.value == null ? '' : String(deal.value),
    probability: deal.probability == null ? '' : String(deal.probability),
    expected_close_date: deal.expected_close_date ?? '',
    notes: deal.notes ?? '',
    contact_id: deal.contact_id ?? null,
    company_id: deal.company_id ?? null,
    owner_id: deal.owner_id ?? null,
  };
}

/** The changed columns only. An unchanged field is never sent — the route applies
 *  `exclude_unset`, so sending everything would also re-stamp fields nobody touched. */
function buildPatch(form: DealFormState, baseline: DealFormState, stageWritable: boolean): DealPatch {
  const patch: DealPatch = {};
  if (form.title !== baseline.title) patch.title = form.title.trim();
  if (form.value !== baseline.value) patch.value = parseFloat(form.value) || 0;
  if (form.probability !== baseline.probability) {
    patch.probability = Math.min(100, Math.max(0, parseInt(form.probability, 10) || 0));
  }
  // Sent as `''` rather than null to CLEAR: the route keeps `null` only for the three FK
  // columns and drops it for everything else, so a null here would be a silent no-op.
  if (form.expected_close_date !== baseline.expected_close_date) {
    patch.expected_close_date = form.expected_close_date;
  }
  if (form.notes !== baseline.notes) patch.notes = form.notes;
  if (form.contact_id !== baseline.contact_id) patch.contact_id = form.contact_id;
  if (form.company_id !== baseline.company_id) patch.company_id = form.company_id;
  if (form.owner_id !== baseline.owner_id) patch.owner_id = form.owner_id;
  if (stageWritable && form.stage !== baseline.stage) patch.stage = form.stage;
  return patch;
}

/** A label/value row. Renders nothing for a blank value — load-bearing, because the sections
 *  below list their rows unconditionally and rely on the row itself to disappear. */
function Row({ label, value, badge }: { label: string; value: ReactNode; badge?: ReactNode }) {
  if (value === null || value === undefined || value === '') return null;
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
      <span style={{ ...mono(10), color: INK_DIM }}>{label}</span>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, fontSize: 13, color: INK }}>
        {value}
        {badge}
      </span>
    </div>
  );
}

const linkButtonStyle = {
  background: 'none', border: 'none', padding: 0,
  font: 'inherit', fontSize: 13, color: INK,
  textDecoration: 'underline', cursor: 'pointer',
} as const;

const actionButtonStyle = {
  padding: '6px 12px', borderRadius: 6,
  border: `1px solid ${LINE_STRONG}`, background: 'transparent',
  color: INK, fontSize: 12, cursor: 'pointer',
} as const;

/**
 * The inline edit form. Module scope on purpose: a component declared inside the body would be a
 * NEW type on every render, so React would unmount and remount every input and the caret would
 * jump to the end of the field on each keystroke.
 */
function DealEditForm({
  form, onChange, stageWritable, contacts, companies, saving, error, onSave, onCancel,
}: {
  form: DealFormState;
  onChange: (patch: Partial<DealFormState>) => void;
  stageWritable: boolean;
  contacts: CrmContact[];
  companies: CrmCompany[];
  saving: boolean;
  error: string;
  onSave: () => void;
  onCancel: () => void;
}) {
  return (
    <form
      onSubmit={e => { e.preventDefault(); onSave(); }}
      style={{ display: 'flex', flexDirection: 'column', gap: 14 }}
    >
      {error && <p style={{ color: CORAL, fontSize: 12, margin: 0 }}>{error}</p>}
      <div>
        <label style={labelStyle} htmlFor="deal-title">Title *</label>
        <input id="deal-title" value={form.title} onChange={e => onChange({ title: e.target.value })} style={inputStyle} />
      </div>
      <div>
        <label style={labelStyle} htmlFor="deal-contact">Contact</label>
        <select
          id="deal-contact"
          value={form.contact_id ?? ''}
          onChange={e => onChange({ contact_id: e.target.value ? Number(e.target.value) : null })}
          style={inputStyle}
        >
          <option value="">No contact</option>
          {contacts.map(c => (
            <option key={c.id} value={c.id}>
              {c.name}{(c.company_name || c.company) ? ` (${c.company_name || c.company})` : ''}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label style={labelStyle} htmlFor="deal-company">Company</label>
        <select
          id="deal-company"
          value={form.company_id ?? ''}
          onChange={e => onChange({ company_id: e.target.value ? Number(e.target.value) : null })}
          style={inputStyle}
        >
          <option value="">No company</option>
          {companies.map(co => (
            <option key={co.id} value={co.id}>{co.name}{co.status === 'archived' ? ' (archived)' : ''}</option>
          ))}
        </select>
      </div>
      <OwnerSelect value={form.owner_id} onChange={v => onChange({ owner_id: v })} id="deal-owner" />
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        {/* All six stages, not just the open ones: a closed deal must be able to show its own
            stage, and reopening one is a legitimate edit. Hidden entirely when the host says
            board-position writes are not on offer. */}
        {stageWritable && (
          <div>
            <label style={labelStyle} htmlFor="deal-stage">Stage</label>
            <select
              id="deal-stage"
              value={form.stage}
              onChange={e => onChange({ stage: e.target.value })}
              style={{ ...inputStyle, textTransform: 'capitalize' }}
            >
              {STAGE_ORDER.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
        )}
        <div>
          <label style={labelStyle} htmlFor="deal-value">Value ($)</label>
          <input id="deal-value" type="number" step="any" min="0" value={form.value}
            onChange={e => onChange({ value: e.target.value })} style={inputStyle} />
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div>
          <label style={labelStyle} htmlFor="deal-probability">Probability (%)</label>
          <input id="deal-probability" type="number" min="0" max="100" value={form.probability}
            onChange={e => onChange({ probability: e.target.value })} style={inputStyle} />
        </div>
        <div>
          <label style={labelStyle} htmlFor="deal-close">Expected Close</label>
          <input id="deal-close" type="date" value={form.expected_close_date}
            onChange={e => onChange({ expected_close_date: e.target.value })} style={inputStyle} />
        </div>
      </div>
      <div>
        <label style={labelStyle} htmlFor="deal-notes">Notes</label>
        <textarea id="deal-notes" value={form.notes} rows={2}
          onChange={e => onChange({ notes: e.target.value })} style={{ ...inputStyle, resize: 'none' }} />
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        {/* Cancel discards deliberately and says so by being labelled Cancel; the close guard is
            for LEAVING the panel with a draft the user may not remember having. */}
        <button type="button" onClick={onCancel} style={{ ...btnSecondary, flex: 1 }}>Cancel</button>
        <button type="submit" disabled={saving} style={{ ...btnPrimary, flex: 1, opacity: saving ? 0.5 : 1 }}>
          {saving ? 'Saving...' : 'Save'}
        </button>
      </div>
    </form>
  );
}

/** Keys a dynamic merge must never copy — see the `view` memo below for why this exists. */
const UNSAFE_MERGE_KEYS = new Set(['__proto__', 'constructor', 'prototype']);

const LOG_TYPES = ['call', 'email', 'meeting', 'note'] as const;

/**
 * Log a call / email / meeting / note against THIS deal — the blueprint's quick-action row, minus
 * its modal. Inline because the CRM's detail close policy warns that a plain `fixed inset-0`
 * sibling of the panel has no Escape handling of its own; a row that is part of the body cannot
 * have that problem. Its own draft is one of the sources the body's close guard composes.
 */
function LogActivityRow({
  selected, note, busy, onSelect, onNote, onLog, onClear,
}: {
  selected: string;
  note: string;
  busy: boolean;
  onSelect: (type: string) => void;
  onNote: (note: string) => void;
  onLog: () => void;
  onClear: () => void;
}) {
  return (
    <div style={{ borderTop: `1px solid ${LINE}`, paddingTop: 16, marginBottom: 20 }}>
      <span style={{ ...mono(10, INK_DIM), display: 'block', marginBottom: 12 }}>Log Activity</span>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        {LOG_TYPES.map(type => (
          <button key={type} type="button" onClick={() => onSelect(type)} style={{
            padding: '5px 12px', borderRadius: 4, fontSize: 12, textTransform: 'capitalize',
            background: selected === type ? ACCENT : 'transparent',
            color: selected === type ? ACCENT_INK : INK_MUTE,
            border: selected === type ? 'none' : `1px solid ${LINE_STRONG}`,
            cursor: 'pointer',
          }}>{type}</button>
        ))}
        {/* Without this, picking a chip by accident leaves the body permanently dirty with no
            non-destructive way back — every other exit would prompt. */}
        {selected && (
          <button type="button" onClick={onClear} style={{ ...linkButtonStyle, fontSize: 12, color: INK_MUTE }}>
            Clear
          </button>
        )}
      </div>
      {selected && (
        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
          <input id="deal-log-note" aria-label="Activity note" placeholder="Add a note..."
            value={note} onChange={e => onNote(e.target.value)}
            style={{ ...inputStyle, flex: 1, width: undefined, fontSize: 13 }} />
          <button type="button" onClick={onLog} disabled={busy}
            style={{ ...btnPrimary, padding: '8px 16px', fontSize: 13, opacity: busy ? 0.5 : 1 }}>
            {busy ? 'Saving...' : 'Log'}
          </button>
        </div>
      )}
    </div>
  );
}

interface Props {
  /** The record the layer resolved. On the board it stays authoritative; off it, it is the seed. */
  deal: CrmDeal;
  /** Does the host hold this row canonically (and patch it after a write)? False ⇒ it came from
   *  `loadById`, so this body owns the read channel. */
  onBoard: boolean;
  /** Offer the board-position writes: the Stage field, Mark Won and Mark Lost. */
  stageWritable: boolean;
  ctx: DetailRenderContext;
  onMarkWon: (deal: CrmDeal) => void;
  onMarkLost: (deal: CrmDeal) => void;
  /** ONE save for stage and columns together — see `DealPatch`. Rejects so the form can stay open. */
  onSaveDeal: (deal: CrmDeal, patch: DealPatch) => Promise<void>;
}

export function DealDetailBody({ deal, onBoard, stageWritable, ctx, onMarkWon, onMarkLost, onSaveDeal }: Props) {
  const navigate = useNavigate();
  const { nameFor } = useUsers();

  // The detail read: activity, touch count and lead score, plus — off the board — the whole row.
  //
  // SEEDED from an already-detailed prop, which matters on the deep-link path: `?deal=N` for a
  // deal that IS on the board resolves through `loadById` first (one row beats the whole board),
  // then the board lands and the layer swaps `deal` to the canonical row — which carries no
  // `activity`. Without the seed the detail we already hold is dropped on that swap: the timeline
  // empties, and the effect below re-fetches the identical bytes to refill it.
  const [fetched, setFetched] = useState<CrmDeal | null>(() => (deal.activity !== undefined ? deal : null));
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<DealFormState>(() => toDealForm(deal));
  const [baseline, setBaseline] = useState<DealFormState>(() => toDealForm(deal));
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState('');
  const [contacts, setContacts] = useState<CrmContact[]>([]);
  const [companies, setCompanies] = useState<CrmCompany[]>([]);
  const [logActivity, setLogActivity] = useState('');
  const [logNote, setLogNote] = useState('');
  const [logging, setLogging] = useState(false);

  // Canonical values win for every key the host actually carries, so a row patched after a write
  // is authoritative — but keys the host's query never SELECTED (the dashboard's top-deals rows
  // have no `company_name`) fall through to the detail fetch instead of rendering blank.
  //
  // `undefined` is treated as absent, not as a value. Real JSON omits such keys entirely, but a
  // row assembled in memory — an optimistic patch, a spread over a partial — can carry an
  // explicit `undefined`, and letting that win would blank a field the fetch had answered.
  // Clearing a field is `null` or `''` on the wire, never `undefined`, so nothing legitimate is
  // lost by ignoring it.
  const view: CrmDeal = useMemo(() => {
    if (!onBoard) return fetched ?? deal;
    if (!fetched) return deal;
    const merged = { ...fetched };
    for (const [key, value] of Object.entries(deal)) {
      // Defence in depth, not a live hole: `CrmDeal` is a closed interface fed by a fixed Pydantic
      // response, so no such key can arrive today. But the cast below discards every compile-time
      // check, so the day anything spreads dynamic keys onto a deal this becomes a prototype sink
      // with no warning. Three names is cheaper than that discovery.
      if (UNSAFE_MERGE_KEYS.has(key)) continue;
      if (value !== undefined) (merged as Record<string, unknown>)[key] = value;
    }
    return merged;
  }, [onBoard, fetched, deal]);

  // Publish the open deal for the assistant drawer (#14). Unconditional and first: deals have no
  // route, so this IS the deal open/close signal, and the drawer goes context-blind without it.
  usePublishActiveRecord('deal', view.id, view.title);

  const { byField, confirm, confirming, refresh: refreshProvenance } = useProvenance('deal', view.id);
  const badge = (f: string) => (
    <ProvenanceBadge prov={byField[f]} onConfirm={() => confirm(f)} confirming={confirming === f} />
  );

  const dealId = deal.id;
  const reqRef = useRef(0);
  const loadDetail = useCallback(async () => {
    const reqId = ++reqRef.current;
    try {
      const detail = await api<CrmDeal>(`/api/crm/deals/${dealId}`);
      if (reqId !== reqRef.current) return;
      setFetched(detail);
    } catch {
      // Non-fatal: the panel still shows the record it was given, plus chatter and custom fields.
    }
  }, [dealId]);

  // A deal the layer resolved through `loadById` arrives with the full detail payload already —
  // `activity` is the marker, since a board or list row never carries it — and the seed above has
  // already taken it. Fetching again would double every deep link's request count for the same
  // bytes.
  //
  // The decision is made ONCE per mount and latched in a ref rather than re-derived from the prop,
  // because the prop's identity changes underneath a mounted body: on the deep-link path the
  // canonical board row replaces the detail payload mid-life, and re-reading `deal.activity` there
  // would fire exactly the fetch this exists to avoid. The body is keyed by record id, so a fresh
  // record is a fresh mount and a fresh latch.
  const detailLoadedRef = useRef(deal.activity !== undefined);
  useEffect(() => {
    if (detailLoadedRef.current) return;
    detailLoadedRef.current = true;
    queueMicrotask(loadDetail);
  }, [loadDetail]);

  const activity: CrmActivity[] = fetched?.activity ?? deal.activity ?? [];
  const touchCount = fetched?.ai_touch_count ?? deal.ai_touch_count;
  const leadScore = fetched?.lead_score ?? deal.lead_score;

  const formDirty = editing && JSON.stringify(form) !== JSON.stringify(baseline);
  const logDirty = logActivity !== '' || logNote.trim() !== '';
  const dirty = formDirty || logDirty;

  // ONE guard over EVERY dirty source, used by the shell and by this body's own exits alike.
  // Re-created when `dirty` flips (registration REPLACES, so re-registering is the contract, not
  // a leak); splitting the sources into two registrations would mean the second silently
  // discarded the first.
  const canLeave = useCallback(
    (reason: DetailCloseReason) => confirmDiscardOn(reason, () => dirty),
    [dirty],
  );
  useEffect(() => ctx.registerCloseGuard(canLeave), [ctx, canLeave]);

  /**
   * A leave this body performs itself, so the layer never sees it — ask the same guard, and hold
   * the same ONE-IN-FLIGHT lock `CollectionDetail.request`'s `pendingRef` holds. The body's own
   * exits bypass `request()` entirely, so without this they were the one unlocked leave path:
   * `canLeave` is async, and when the body is NOT dirty it resolves in a microtask with no dialog
   * at all, so two fast clicks both cleared it and ran the action twice — a double stage write,
   * or two navigations.
   */
  const leavingRef = useRef(false);
  const leaveVia = useCallback(async (run: () => void) => {
    if (leavingRef.current) return;
    leavingRef.current = true;
    try {
      if (await canLeave('button')) run();
    } finally {
      leavingRef.current = false;
    }
  }, [canLeave]);

  function startEditing() {
    const snapshot = toDealForm(view);
    setForm(snapshot);
    setBaseline(snapshot);
    setFormError('');
    setEditing(true);
    // Either picker failing degrades to a list holding only this deal's own linked record — which
    // is indistinguishable, on screen, from "this install has no other contacts". Say so once, so a
    // network blip cannot be misread as data. One flag for both requests: the two fail together far
    // more often than separately, and two stacked toasts describe one outage twice.
    let reported = false;
    const reportPickerFailure = () => {
      if (reported) return;
      reported = true;
      toast.error('Could not load the contact and company lists — only this deal’s links are shown.');
    };
    // Fetched on Edit, not on mount: opening a deal to read it should cost no extra requests.
    api<{ contacts: CrmContact[] }>('/api/crm/contacts?limit=200')
      .then(d => setContacts(withFallbackContact(d.contacts, view)))
      .catch(() => { setContacts(withFallbackContact([], view)); reportPickerFailure(); });
    api<{ companies: CrmCompany[] }>('/api/crm/companies?limit=200')
      .then(d => setCompanies(withFallbackCompany(d.companies, view)))
      .catch(() => { setCompanies(withFallbackCompany([], view)); reportPickerFailure(); });
  }

  async function handleSave() {
    if (!form.title.trim()) { setFormError('Title is required'); return; }
    const patch = buildPatch(form, baseline, stageWritable);
    if (Object.keys(patch).length === 0) { setEditing(false); return; }
    setSaving(true);
    setFormError('');
    try {
      await onSaveDeal(view, patch);
      setEditing(false);
      void loadDetail();          // score, activity, and the off-board row itself
      void refreshProvenance();   // a human edit retires the badge the assistant left
    } catch (err: unknown) {
      // Stay in edit mode: the draft is the only copy of what the user typed.
      setFormError(err instanceof Error ? err.message : 'Failed to save');
    } finally {
      setSaving(false);
    }
  }

  function clearLog() {
    setLogActivity('');
    setLogNote('');
  }

  async function handleLog() {
    if (!logActivity) return;
    setLogging(true);
    try {
      await api('/api/crm/activity', {
        method: 'POST',
        body: JSON.stringify({
          activity: logActivity,
          note: logNote,
          contact_id: view.contact_id ?? null,
          deal_id: view.id,
        }),
      });
      clearLog();
      void loadDetail();
    } catch {
      toast.error('Failed to log activity.');
    } finally {
      setLogging(false);
    }
  }

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(`${window.location.origin}${dealDeepLink(view.id)}`);
      toast.success('Link copied.');
    } catch {
      // No "select it manually" fallback offered, because there is nothing to select: this panel
      // renders no link, and the address bar has had `?deal=` stripped by design. Naming the
      // cause is the only instruction the user can actually act on.
      toast.error('Could not copy the link — the browser blocked clipboard access.');
    }
  }

  const closable = stageWritable && OPEN_STAGES.includes(view.stage);

  return (
    <div style={{ padding: 20 }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end', marginBottom: 12 }}>
        <button type="button" onClick={() => void copyLink()} style={actionButtonStyle}>Copy link</button>
        {!editing && <button type="button" onClick={startEditing} style={actionButtonStyle}>Edit</button>}
        {!editing && closable && (
          <>
            <button type="button" onClick={() => void leaveVia(() => onMarkWon(view))} style={{
              ...actionButtonStyle, background: SAGE, color: ACCENT_INK, border: 'none', fontWeight: 500,
            }}>Mark Won</button>
            <button type="button" onClick={() => void leaveVia(() => onMarkLost(view))} style={{
              ...btnDanger, padding: '6px 12px', borderRadius: 6, fontSize: 12,
            }}>Mark Lost</button>
          </>
        )}
      </div>

      {editing ? (
        <DealEditForm
          form={form}
          onChange={patch => setForm(prev => ({ ...prev, ...patch }))}
          stageWritable={stageWritable}
          contacts={contacts}
          companies={companies}
          saving={saving}
          error={formError}
          onSave={() => void handleSave()}
          onCancel={() => setEditing(false)}
        />
      ) : (
        <>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
            <h3 style={{
              fontFamily: FONT_DISPLAY, fontSize: 20, fontWeight: 400,
              letterSpacing: '-0.01em', color: INK, margin: 0, flex: 1,
            }}>{view.title}</h3>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexShrink: 0, marginLeft: 12 }}>
              <span style={{ fontFamily: FONT_DISPLAY, fontSize: 20, color: GOLD }}>
                ${(view.value ?? 0).toLocaleString()}
              </span>
              {badge('value')}
            </span>
          </div>

          <div style={{
            display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
            fontSize: 12, color: INK_MUTE, marginBottom: 16,
          }}>
            <span style={{ textTransform: 'capitalize' }}>
              Stage: <span style={{ color: STAGE_COLORS[view.stage]?.color || INK }}>{view.stage}</span>
            </span>
            {badge('stage')}
          </div>

          {/* The touch count and, on demand, every event behind it. Renders nothing when the count
              is NULL, so a keyless install sees no affordance at all. */}
          <AiTouchDetail dealId={view.id} count={touchCount} />

          {view.notes && (
            <p style={{ fontSize: 14, color: INK_MUTE, marginBottom: 16, lineHeight: 1.5 }}>
              {view.notes} {badge('notes')}
            </p>
          )}

          {/* Cleared automatically when a deal leaves `lost`, so this only ever shows on a
              currently-lost deal. */}
          {view.lost_reason && (
            <p style={{ fontSize: 13, color: INK_MUTE, marginBottom: 16, lineHeight: 1.5 }}>
              <span style={{ ...mono(10), color: INK_DIM, marginRight: 6 }}>LOST REASON</span>
              {view.lost_reason} {badge('lost_reason')}
            </p>
          )}

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 20 }}>
            <Row label="Deal ID" value={`#${view.id}`} />
            <Row
              label="Contact"
              value={view.contact_id != null && view.contact_name ? (
                // A button, not an <a>: leaving the panel is a leave path like any other, and a
                // link would take a dirty draft with it without asking.
                <button type="button" style={linkButtonStyle}
                  onClick={() => void leaveVia(() => navigate(`/crm/contacts/${view.contact_id}`))}>
                  {view.contact_name}
                </button>
              ) : view.contact_name}
            />
            <Row
              label="Company"
              value={view.company_id != null && view.company_name ? (
                <button type="button" style={linkButtonStyle}
                  onClick={() => void leaveVia(() => navigate(`/crm/companies/${view.company_id}`))}>
                  {view.company_name}
                </button>
              ) : view.company_name}
            />
            <Row label="Owner" value={nameFor(view.owner_id)} />
            <Row
              label="Probability"
              value={view.probability > 0 ? `${view.probability}%` : ''}
              badge={badge('probability')}
            />
            {leadScore != null && (
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ ...mono(10), color: INK_DIM }}>Lead Score</span>
                <ScorePill score={leadScore} />
              </div>
            )}
            <Row
              label="Expected Close"
              value={view.expected_close_date}
              badge={badge('expected_close_date')}
            />
          </div>
        </>
      )}

      {/* Everything below stays mounted while editing — they own their own drafts and save
          independently, so tearing them down mid-edit would discard work the form does not hold. */}
      <LogActivityRow
        selected={logActivity}
        note={logNote}
        busy={logging}
        onSelect={setLogActivity}
        onNote={setLogNote}
        onLog={() => void handleLog()}
        onClear={clearLog}
      />

      {/* Renders nothing when no deal fields are defined. No `key` here: the shell keys the whole
          body by record id, so ‹ › nav already remounts this. */}
      <CustomFieldsSection
        entityType="deal"
        entityId={view.id}
        sectionStyle={{ borderTop: `1px solid ${LINE}`, paddingTop: 16, marginBottom: 20 }}
      />

      <div style={{ borderTop: `1px solid ${LINE}`, paddingTop: 16, marginBottom: 20 }}>
        <span style={{ ...mono(10, INK_DIM), display: 'block', marginBottom: 12 }}>Activity History</span>
        <ActivityTimeline activities={activity} onUpdate={loadDetail} />
      </div>

      <div style={{ borderTop: `1px solid ${LINE}`, paddingTop: 16 }}>
        <span style={{ ...mono(10, INK_DIM), display: 'block', marginBottom: 12 }}>Chatter</span>
        <NotesThread entityType="deal" entityId={view.id} />
      </div>
    </div>
  );
}

/** Keep the linked record selectable even when it falls outside the capped page the picker
 *  fetched — otherwise the `<select>` renders blank, which reads as "no contact". */
function withFallbackContact(contacts: CrmContact[], deal: CrmDeal): CrmContact[] {
  const id = deal.contact_id;
  if (id == null || contacts.some(c => c.id === id)) return contacts;
  return [...contacts, { ...EMPTY_CONTACT, id, name: deal.contact_name || `Contact #${id}` }];
}

function withFallbackCompany(companies: CrmCompany[], deal: CrmDeal): CrmCompany[] {
  const id = deal.company_id;
  if (id == null || companies.some(c => c.id === id)) return companies;
  return [...companies, { ...EMPTY_COMPANY, id, name: deal.company_name || `Company #${id}` }];
}

const EMPTY_CONTACT: CrmContact = {
  id: 0, name: '', email: '', phone: '', company: '', company_id: null, title: '',
  source: '', status: 'active', tags: '', notes: '', created_at: '', updated_at: '',
};

const EMPTY_COMPANY: CrmCompany = {
  id: 0, name: '', domain: '', industry: '', phone: '', address: '', notes: '',
  source: '', status: 'active', created_at: '', updated_at: '',
};
