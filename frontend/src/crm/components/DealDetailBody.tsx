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
 *    row. Copy-link builds the URL from the record id through `crm/dealDeepLink.ts` — the same
 *    module the backend's `crm/links.py` is pinned against — never from `window.location`: the
 *    panel opens from a board card and a list row far more often than from a link, and on both of
 *    those the address bar names the board, not the deal.
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
 * Three lifecycle behaviours arrived from `DealDetailSheet` when this component replaced it, and
 * each is load-bearing rather than decorative: the #83 ARCHIVED banner and Restore (the only route
 * back on a keyless install), the #128 Mark Lost reason dialog with the awaited close-out latch
 * behind it, and the #128 `OwnerName` row. See each one at its site.
 *
 * Ported from the CAKE OS blueprint's `DealDetailBody`. Its `ImageGallery` (no media store here),
 * `DealTodos` (a different directory model) and `ScoreBreakdown` (no per-factor endpoint) are
 * deliberately absent. `LostReasonModal` was too, on the premise that the REST path could not set
 * `lost_reason`; #128 added `POST /deals/:id/mark-lost`, so it is here.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../core/api/client';
import type { CrmActivity, CrmCompany, CrmContact, CrmDeal } from '../../core/types';
import { confirmDiscardOn, type DetailCloseReason, type DetailRenderContext } from '../../shared/collection';
import {
  ACCENT, ACCENT_INK, CORAL_TEXT, FONT_DISPLAY, GOLD_TEXT, INK, INK_DIM, INK_MUTE, LINE,
  LINE_STRONG, ON_STATUS, SAGE_FILL, inputStyle, labelStyle, mono,
} from '../../shared/styles';
import { toast } from '../../shared/toast';
import { OPEN_STAGES, STAGE_COLORS, STAGE_ORDER } from '../constants';
import { dealDeepLink } from '../dealDeepLink';
import {
  companyLabelOf, companyNameOf, companySublabelOf, contactLabelOf, contactSublabelOf,
  createCompany, createContact, recordId, searchCompanies, searchContacts,
} from '../dealLinkPickers';
import { btnDanger, btnPrimary, btnSecondary } from '../styles';
import { usePublishActiveRecord } from '../RecordContext';
import { useProvenance } from '../useProvenance';
import { ActivityTimeline } from './ActivityTimeline';
import { AiTouchDetail } from './AiTouchDetail';
import { ScorePill } from './badges';
import { CustomFieldsSection } from './CustomFieldsSection';
import { LostReasonModal } from './LostReasonModal';
import { RecordCombobox } from './RecordCombobox';
import { NotesThread } from './NotesThread';
import DealTemperatureIcon from './DealTemperatureIcon';
import { OwnerName } from './OwnerName';
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
  | 'expected_close_date' | 'probability' | 'notes' | 'stage'
  // Issue #125. Not a form field — the temperature is written by its own one-click control,
  // here and on the board, so it never joins `formFields` or the dirty comparison. It is in
  // this type because it travels the same `writeDeal` PUT as everything else.
  | 'deal_temperature'>>;

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
  /** The linked records' DISPLAY names, carried in form state rather than looked up from a
   *  fetched list. That is what ends the capped-page hazard the two `<select>`s used to carry
   *  (#123): an out-of-page link had no `<option>` and rendered blank, reading as "none".
   *  Excluded from the dirty comparison — see `formFields`. */
  contact_label: string;
  company_label: string;
}

/** The columns the dirty check and the patch look at. The two `*_label` fields are display state
 *  that always moves WITH its id, so comparing them would only ever double-count a change — and
 *  would report a spurious edit for a deal whose `company_name` the server later joins
 *  differently. */
function formFields(f: DealFormState) {
  return {
    title: f.title, stage: f.stage, value: f.value, probability: f.probability,
    expected_close_date: f.expected_close_date, notes: f.notes,
    contact_id: f.contact_id, company_id: f.company_id, owner_id: f.owner_id,
  };
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
    contact_label: deal.contact_name ?? '',
    company_label: deal.company_name ?? '',
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
  form, onChange, onPickContact, onPickCompany, stageWritable, onContactBusy, onCompanyBusy,
  saving, exiting, error, onSave, onCancel,
}: {
  form: DealFormState;
  onChange: (patch: Partial<DealFormState>) => void;
  onPickContact: (record: CrmContact | null) => void;
  onPickCompany: (record: CrmCompany | null) => void;
  stageWritable: boolean;
  onContactBusy: (busy: boolean) => void;
  onCompanyBusy: (busy: boolean) => void;
  saving: boolean;
  /** An exit is in flight, so the whole form goes read-only. NOT discarded: the write may still
   *  be refused, in which case the panel stays and so must the draft — throwing it away on the
   *  strength of a consent given for a leave that then did not happen would be worse than the
   *  silent loss this prevents. */
  exiting: boolean;
  error: string;
  onSave: () => void;
  onCancel: () => void;
}) {
  return (
    <form onSubmit={e => { e.preventDefault(); onSave(); }}>
      {/* A native `<fieldset disabled>` rather than nine `disabled=` props: it covers every
          control inside, the two comboboxes and their × buttons included, and a field added later
          cannot forget it. */}
      <fieldset
        disabled={exiting}
        style={{
          display: 'flex', flexDirection: 'column', gap: 14,
          // A fieldset's UA styling would otherwise draw a box around the form; `minWidth: 0` is
          // the well-known fix for its refusal to shrink inside a flex parent.
          border: 'none', padding: 0, margin: 0, minWidth: 0,
        }}
      >
      {error && <p style={{ color: CORAL_TEXT, fontSize: 12, margin: 0 }}>{error}</p>}
      <div>
        <label style={labelStyle} htmlFor="deal-title">Title *</label>
        <input id="deal-title" value={form.title} onChange={e => onChange({ title: e.target.value })} style={inputStyle} />
      </div>
      {/* The SAME pickers `DealForm` uses, from the same contract module (#123/#126): server
          search as you type, and a `Create "…"` row that resolves a company through the #35
          get-or-create primitive. A capped `<select>` here would have kept the edit path on the
          first 200 rows with no way to link a record outside them. */}
      <RecordCombobox<CrmContact>
        label="Contact"
        id="deal-contact"
        value={form.contact_id}
        valueLabel={form.contact_label}
        emptyLabel="No contact"
        search={searchContacts}
        create={createContact}
        getId={recordId}
        getLabel={contactLabelOf}
        getSublabel={contactSublabelOf}
        onSelect={onPickContact}
        onBusyChange={onContactBusy}
      />
      <RecordCombobox<CrmCompany>
        label="Company"
        id="deal-company"
        value={form.company_id}
        valueLabel={form.company_label}
        emptyLabel="No company"
        search={searchCompanies}
        create={createCompany}
        getId={recordId}
        getLabel={companyLabelOf}
        getMatchText={companyNameOf}
        getSublabel={companySublabelOf}
        onSelect={onPickCompany}
        onBusyChange={onCompanyBusy}
      />
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
      </fieldset>
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
  selected, note, busy, exiting, onSelect, onNote, onLog, onClear,
}: {
  selected: string;
  note: string;
  busy: boolean;
  /** An exit is in flight, so this row accepts no NEW draft — the host dismisses the panel when
   *  the write settles, and by then a draft started here would be discarded with the close guard
   *  never having seen it (it ran at click time, before the draft existed). */
  exiting: boolean;
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
          <button key={type} type="button" onClick={() => onSelect(type)} disabled={exiting} style={{
            padding: '5px 12px', borderRadius: 4, fontSize: 12, textTransform: 'capitalize',
            background: selected === type ? ACCENT : 'transparent',
            color: selected === type ? ACCENT_INK : INK_MUTE,
            border: selected === type ? 'none' : `1px solid ${LINE_STRONG}`,
            cursor: exiting ? 'default' : 'pointer',
            opacity: exiting ? 0.5 : 1,
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
            value={note} onChange={e => onNote(e.target.value)} disabled={exiting}
            style={{ ...inputStyle, flex: 1, width: undefined, fontSize: 13 }} />
          <button type="button" onClick={onLog} disabled={busy || exiting}
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
  /** May return a promise; this body AWAITS it, which is what keeps the close-out pair disabled
   *  for the whole write (#128). A host that resolves synchronously is unaffected. */
  onMarkWon: (deal: CrmDeal) => void | Promise<void>;
  /** `lostReason` is present ONLY for a Mark Lost taken through the reason dialog — a string,
   *  possibly empty. Every other close leaves it undefined, which is what tells the host to use
   *  the plain stage PUT rather than the mark-lost verb (see `crm/dealStageWrite.ts`). */
  onMarkLost: (deal: CrmDeal, lostReason?: string) => void | Promise<void>;
  /** ONE save for stage and columns together — see `DealPatch`. Rejects so the form can stay open.
   *  RESOLVES WITH THE SERVER'S ROW: the route derives `probability` from the stage (100/0), so
   *  the patch that went out is not what was stored, and this body folds the answer into its own
   *  read channel rather than guessing. */
  onSaveDeal: (deal: CrmDeal, patch: DealPatch) => Promise<CrmDeal>;
  /** A deal was un-archived here (issue #83). Receives the row the SERVER returned so the host can
   *  patch it in place — a silent refetch can fail invisibly, which would leave the host still
   *  showing the deal as archived after a restore that actually happened.
   *
   *  PATCHING ONLY. Dismissal is `onClose`'s, for the reason stated there. */
  onRestored?: (deal: CrmDeal) => void;
  /**
   * Take this panel away — the deal's work here is done (closed out, or restored).
   *
   * THE BODY decides this, never the host, and that is the whole point. A close-out is awaited,
   * so between the click and the answer the user can walk to another record with `‹ ›`, follow a
   * deep link, or be remounted by the board emptying underneath them. A host dismissing on its
   * own has to guess whether the panel in front of it is still the one that asked — and every way
   * of guessing has a hole: the deal id repeats on A → B → A, and a session counter has to be
   * bumped by every path that ends a session, of which there are more than anyone enumerates.
   *
   * Called only while this body is still MOUNTED, which answers the question exactly: a mounted
   * body IS the open panel, so "close the open panel" and "close mine" cannot differ. A body that
   * was replaced simply never calls it, and its successor keeps whatever draft it holds.
   */
  onClose: () => void;
}

export function DealDetailBody({
  deal, onBoard, stageWritable, ctx, onMarkWon, onMarkLost, onSaveDeal, onRestored, onClose,
}: Props) {
  const navigate = useNavigate();

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
  // A quick-create is in flight in one of the pickers. Closing a picker deliberately does NOT
  // abandon its create (the record is being written either way), so saving underneath one would
  // write the deal without a link that is about to exist, and orphan the new record.
  const [contactBusy, setContactBusy] = useState(false);
  const [companyBusy, setCompanyBusy] = useState(false);
  const [logActivity, setLogActivity] = useState('');
  const [logNote, setLogNote] = useState('');
  const [logging, setLogging] = useState(false);
  // Mark Lost opens the reason dialog instead of closing the deal immediately (issue #128).
  const [askingLostReason, setAskingLostReason] = useState(false);
  // A close-out is in flight. The two hosts dismiss differently — the pipeline clears its
  // selection only once the write succeeds, while a refusal keeps the panel open so the user can
  // retry — so without this the buttons stay live during the request, and a second Mark Lost
  // writes a second "Deal lost —" note (`mark_deal_lost` appends one on every call that finds the
  // deal, a no-op write included). The dialog's own latch cannot cover it: that unmounts as soon
  // as the first confirm lands.
  const [closing, setClosing] = useState(false);
  const [restoring, setRestoring] = useState(false);
  // Has the user spoken for the company field during THIS edit session? An emptiness test cannot
  // answer that: it reads a company the user just CLEARED as one that was never set, so the next
  // contact pick silently puts the link back. `DealForm` carries the same ref for the same
  // reason. Seeded per session in `startEditing` — a company already on the deal IS a deliberate
  // choice, just an earlier one.
  const companyTouched = useRef(false);

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

  // The two LIFECYCLE columns are read from the detail fetch when there is one, and from the prop
  // only until then — deliberately NOT through `view`, where the host row wins.
  //
  // Both are about this panel's OWN actions, and the fetch is by construction the latest statement
  // about them: `loadDetail` runs on mount, after an inline save, and after a close whose outcome
  // was ambiguous. Two cases need it. A deal archived AFTER the board loaded carries
  // `archived_at: null` in the host row, so a `view`-based banner would never appear and the panel
  // would keep offering close-out actions the server refuses outright. And a close whose response
  // was LOST may already have committed — re-offering Mark Lost there appends a second
  // "Deal lost —" note on the retry.
  //
  // The host row cannot be dragged out from under an OPEN panel (the panel is a modal over the
  // board), so the only writer that can move these behind our back is another seat or the
  // assistant, arriving on a board refresh. That case reads stale here until the panel is
  // reopened — the same trade `DealDetailSheet` made, stated rather than inherited silently.
  // A `??` chain would be wrong in the other direction: `null` is the value a restore WRITES.
  const archivedAt = fetched ? fetched.archived_at : deal.archived_at;
  const closeStage = fetched ? fetched.stage : deal.stage;

  // The record this body RENDERS and snapshots into its edit form: `view`, with those two
  // columns swapped in. Without it the panel contradicts itself — the close-out pair would
  // vanish on a fetched 'lost' while the heading, the stage chip and the form's Stage select all
  // still read the host row's 'lead', and picking 'lead' there would produce NO patch because it
  // matches a baseline the server never held. Identity-stable when the two agree, which is every
  // render but the ones this exists for.
  const record: CrmDeal = useMemo(
    () => (view.archived_at === archivedAt && view.stage === closeStage
      ? view
      : { ...view, archived_at: archivedAt, stage: closeStage }),
    [view, archivedAt, closeStage],
  );

  // Publish the open deal for the assistant drawer (#14). Unconditional and first: deals have no
  // route, so this IS the deal open/close signal, and the drawer goes context-blind without it.
  usePublishActiveRecord('deal', record.id, record.title);

  const { byField, confirm, confirming, refresh: refreshProvenance } = useProvenance('deal', record.id);
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



  // Stage is not writable on an archived deal at all: the server refuses the change
  // (`_classify_deal_update` raises → 400) and rejects the WHOLE update with it, so an editable
  // Stage field would discard every other column the user had just typed.
  const stageEditable = stageWritable && !archivedAt;

  // Whether this panel is still on screen when an awaited write settles — see `closeOut`.
  //
  // Re-ARMED in setup, not merely cleared in cleanup. `main.tsx` renders under <StrictMode>,
  // whose development cycle is setup → cleanup → setup, so a cleanup-only version leaves this
  // false for the life of the mount — and every close-out and restore then silently declines to
  // dismiss the panel, in development only, which is exactly where it would be met and mistaken
  // for a broken endpoint. `WeeklyTouchesDetailPage` carried this same trap and its own note
  // about it before #75 retired that code.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const formDirty = editing && JSON.stringify(formFields(form)) !== JSON.stringify(formFields(baseline));
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

  // Choosing a contact fills the company from that contact ONLY when none is set yet — the rule
  // `DealForm` has always applied, and losing it in the move to an inline editor would quietly
  // leave newly-linked deals out of their company's rollups. Deal↔company links are independent,
  // so a company the user picked deliberately is never overwritten (or nulled) by a contact change.
  // Takes the RECORD, not an id: `RecordCombobox` already holds the contact it just resolved, so
  // the old lookup into a capped list is gone — and with it the case where an out-of-page contact
  // silently skipped the auto-fill.
  //
  // The company is filled from that contact ONLY when the user has not spoken for the company
  // field — the rule `DealForm` has always applied, guarded the way `DealForm` guards it. Deal↔
  // company links are independent, so neither a company already on the deal nor one the user
  // just cleared is overwritten by a contact change.
  function pickContact(c: CrmContact | null) {
    setForm(prev => {
      if (c === null) return { ...prev, contact_id: null, contact_label: '' };
      if (companyTouched.current) {
        return { ...prev, contact_id: c.id, contact_label: c.name };
      }
      // An auto-derived company FOLLOWS the contact it was derived from — INCLUDING to nothing.
      // Filling only when the new contact HAS one would strand the previous contact's company on
      // the deal, silently linking it to an organisation neither the user nor the current
      // contact ever named. Clearing the contact outright is left alone above: there is no new
      // contact to derive from, and the company may be all the user has.
      return {
        ...prev,
        contact_id: c.id,
        contact_label: c.name,
        company_id: c.company_id ?? null,
        company_label: c.company_id != null ? (c.company_name || c.company || '') : '',
      };
    });
  }

  /** The user speaking for the company field — a pick, a create, or the × clear. Sets the ref,
   *  which is the only thing that tells a CLEARED company from an unset one. */
  function pickCompany(co: CrmCompany | null) {
    companyTouched.current = true;
    setForm(prev => ({
      ...prev,
      company_id: co?.id ?? null,
      // The DECORATED label, so an archived company keeps its marker in the closed control.
      company_label: co ? companyLabelOf(co) : '',
    }));
  }

  // No list fetch here any more: both pickers search the server as the user types, so opening a
  // deal to read it still costs no extra requests and editing one is no longer limited to the
  // first 200 rows.
  function startEditing() {
    // A company already on the deal counts as spoken for; clearing it in `DealEditForm` sets the
    // flag too, which is the case an emptiness test gets wrong.
    companyTouched.current = record.company_id != null;
    const snapshot = toDealForm(record);
    setForm(snapshot);
    setBaseline(snapshot);
    setFormError('');
    setEditing(true);
  }

  async function handleSave() {
    if (!form.title.trim()) { setFormError('Title is required'); return; }
    if (contactBusy || companyBusy) {
      setFormError('Still creating a linked record — one moment.');
      return;
    }
    const patch = buildPatch(form, baseline, stageEditable);
    if (Object.keys(patch).length === 0) { setEditing(false); return; }
    setSaving(true);
    setFormError('');
    try {
      const updated = await onSaveDeal(record, patch);
      setEditing(false);
      // Fold the SERVER's row into `fetched` before the refetch. `archivedAt`/`closeStage` read
      // from there, so a save that changes the stage would otherwise show the PRE-save value —
      // and hide or offer the close-out pair on it — until the request below lands. The row, not
      // the patch: `_classify_deal_update` overrides `probability` to 100/0 inside the same
      // transaction, so folding what was SENT would paint "Won · 50%" until the refetch, and
      // leave it there for good if the refetch failed.
      setFetched(prev => (prev ? { ...prev, ...updated } : prev));
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
          contact_id: record.contact_id ?? null,
          deal_id: record.id,
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
      await navigator.clipboard.writeText(`${window.location.origin}${dealDeepLink(record.id)}`);
      toast.success('Link copied.');
    } catch {
      // No "select it manually" fallback offered, because there is nothing on screen to select:
      // this panel renders no link, and the address bar names the deal only when the panel was
      // itself opened from one. Naming the cause is the only instruction the user can act on.
      toast.error('Could not copy the link — the browser blocked clipboard access.');
    }
  }

  /**
   * Close the deal out (issue #128). AWAITS the host so both buttons stay `disabled` for the whole
   * write — that attribute IS the re-entry guard, and it covers the dialog path too, since the
   * only way back into the dialog is the Mark Lost button.
   *
   * `closing` is reset on BOTH paths, for the reason `restoreDeal` is: a host that keeps the panel
   * open when the write fails (the dashboard does) would otherwise leave the user unable to retry
   * the close they just watched fail.
   */
  async function closeOut(toStage: 'won' | 'lost', lostReason?: string) {
    setClosing(true);
    try {
      if (toStage === 'won') await onMarkWon(record);
      else await onMarkLost(record, lostReason);
      // Written. The deal is closed, so the panel goes — see `onClose` for why that decision is
      // made HERE. A refused or failed write rejects instead, and the host has already said so.
      if (mountedRef.current) onClose();
    } catch {
      // Swallowed DELIBERATELY, and it is the only thing this branch does. Both buttons fire
      // this without awaiting it, so a host rejection escaping here is an unhandled rejection —
      // and the report is not ours to make twice: `writeDeal` toasts a failed stage PUT itself
      // and the host adds the bulk-lock refusal. What matters to this body is only that the
      // panel stays open, which is what NOT calling `onClose` above already accomplishes.
    } finally {
      setClosing(false);
      // Still mounted means the host did NOT dismiss us — its failure path. The outcome of that
      // write is genuinely UNKNOWN: a dropped connection or a 5xx can arrive after the server has
      // already committed. So reconcile against the server before offering a retry — a close that
      // did land re-reads as stage 'lost' and these buttons disappear, instead of inviting a
      // second `mark_deal_lost` and a second note.
      if (mountedRef.current) void loadDetail();
    }
  }

  /**
   * Restore an archived deal (issue #83) — the ONLY way back on an install with no AI provider,
   * since the assistant's `crm_archive_deal(archived=false)` needs a key and this does not.
   *
   * The authoritative row goes UP to the host rather than being thrown away in favour of a
   * refetch that can fail silently. That is why `POST /restore` returns the deal instead of
   * `{"ok": true}`.
   */
  async function restoreDeal() {
    setRestoring(true);
    try {
      const restored = await api<CrmDeal>(`/api/crm/deals/${record.id}/restore`, { method: 'POST' });
      // Patch our OWN read channel too, not just the host's. `archivedAt` reads the fetch when
      // there is one, so without this the banner would survive its own restore on any host that
      // keeps the panel open.
      setFetched(prev => (prev ? { ...prev, ...restored } : restored));
      // The row goes up UNCONDITIONALLY — the board must show the deal as live whether or not
      // this panel is still on screen, and that patch is what makes it correct. Only the
      // DISMISSAL is conditional, and for the reason `onClose` gives.
      onRestored?.(restored);
      if (mountedRef.current) onClose();
    } catch {
      toast.error('Failed to restore deal.');
    } finally {
      // Reset on BOTH paths. A host that closes the panel on `onRestored` unmounts this anyway,
      // but `onRestored` is optional and the banner renders on every host — a latched flag would
      // strand the button on "Restoring…" for any host that keeps the panel open.
      setRestoring(false);
    }
  }

  const closable = stageWritable && !archivedAt && OPEN_STAGES.includes(closeStage);
  // Disable the close-out pair while EITHER a write is in flight or the reason dialog is open.
  // The dialog half is defence in depth behind its focus trap: this panel stays a live DOM subtree
  // underneath, and Mark Won sitting one stray Tab away from an open Mark Lost dialog is a wrong
  // write, not just an a11y lapse.
  const closeOutDisabled = closing || askingLostReason;
  // An exit this body started is in flight. While it is, the body accepts NO new draft: the host
  // dismisses the panel when the write settles, and the close guard already ran — at click time,
  // before any such draft existed — so anything typed underneath would be discarded in silence.
  // One rule, three controls: Edit, the quick-log chips and its note.
  const exiting = closeOutDisabled || restoring;

  return (
    <div style={{ padding: 20 }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end', marginBottom: 12 }}>
        <button type="button" onClick={() => void copyLink()} style={actionButtonStyle}>Copy link</button>
        {/* Refused while an exit is in flight. The host dismisses this panel when the write
            settles, and by then a draft started underneath would be discarded without the close
            guard ever seeing it — the id check that stops a settling write closing SOMEONE
            ELSE's panel cannot help here, because it really is this deal. */}
        {!editing && (
          <button
            type="button"
            onClick={startEditing}
            disabled={exiting}
            style={{
              ...actionButtonStyle,
              cursor: exiting ? 'default' : 'pointer',
              opacity: exiting ? 0.5 : 1,
            }}
          >Edit</button>
        )}
        {!editing && closable && (
          <>
            <button
              type="button"
              onClick={() => void leaveVia(() => { void closeOut('won'); })}
              disabled={closeOutDisabled}
              style={{
                ...actionButtonStyle, background: SAGE_FILL, color: ON_STATUS,
                border: 'none', fontWeight: 500,
                // #128's disabled affordance alongside #119's tokens — the two are orthogonal
                // (interactivity vs. hue). The dimming is not a #119 violation: that rule governs
                // a container holding a chip, and WCAG 1.4.3 exempts inactive controls.
                cursor: closeOutDisabled ? 'default' : 'pointer',
                opacity: closeOutDisabled ? 0.5 : 1,
              }}
            >Mark Won</button>
            {/* Ask for the reason FIRST (issue #128). `lost_reason` has no other human writer — it
                is excluded from `_DEAL_USER_WRITABLE` — so before this the field could be read on
                this very panel and only ever written by the assistant. The draft guard runs here,
                on the way INTO the dialog, so a dirty edit is confirmed once rather than after the
                user has already typed a reason. */}
            <button
              type="button"
              onClick={() => void leaveVia(() => setAskingLostReason(true))}
              disabled={closeOutDisabled}
              style={{
                ...btnDanger, padding: '6px 12px', borderRadius: 6, fontSize: 12,
                cursor: closeOutDisabled ? 'default' : 'pointer',
                opacity: closeOutDisabled ? 0.5 : 1,
              }}
            >Mark Lost</button>
          </>
        )}
      </div>

      {/* The archived banner #22 Phase 1 left out, now that the pipeline's Archived facet (#83)
          makes an archived deal reachable. Neutral dashed border rather than a danger tint —
          archived is a state, not a problem. The copy is deliberately narrow: archived deals leave
          pipeline totals and deal rollups, but their history stays in the activity feed by
          design. */}
      {archivedAt && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
          border: `1px dashed ${LINE_STRONG}`, borderRadius: 6,
          padding: '10px 12px', marginBottom: 16,
        }}>
          <span style={{ ...mono(10), color: INK_DIM }}>ARCHIVED</span>
          <span style={{ fontSize: 13, color: INK_MUTE, flex: 1, lineHeight: 1.5 }}>
            Archived {new Date(archivedAt).toLocaleDateString()} — excluded from pipeline
            totals and deal rollups.
          </span>
          <button
            type="button"
            // Through the SAME guard as every other exit this body owns. Every host dismisses the
            // panel on `onRestored`, and the banner renders over the open edit form — so without
            // this, "edit an archived deal, then restore it" discarded the draft silently.
            onClick={() => void leaveVia(() => { void restoreDeal(); })}
            disabled={restoring}
            style={{
              padding: '8px 14px', borderRadius: 6, border: `1px solid ${LINE_STRONG}`,
              background: 'transparent', color: INK, fontSize: 13,
              cursor: restoring ? 'default' : 'pointer', opacity: restoring ? 0.5 : 1,
            }}
          >{restoring ? 'Restoring…' : 'Restore'}</button>
        </div>
      )}

      {editing ? (
        <DealEditForm
          form={form}
          onChange={patch => setForm(prev => ({ ...prev, ...patch }))}
          onPickContact={pickContact}
          onPickCompany={pickCompany}
          stageWritable={stageEditable}
          onContactBusy={setContactBusy}
          onCompanyBusy={setCompanyBusy}
          saving={saving || contactBusy || companyBusy}
          exiting={exiting}
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
            }}>{record.title}</h3>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexShrink: 0, marginLeft: 12 }}>
              <span style={{ fontFamily: FONT_DISPLAY, fontSize: 20, color: GOLD_TEXT }}>
                ${(record.value ?? 0).toLocaleString()}
              </span>
              {badge('value')}
            </span>
          </div>

          <div style={{
            display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
            fontSize: 12, color: INK_MUTE, marginBottom: 16,
          }}>
            <span style={{ textTransform: 'capitalize' }}>
              Stage: <span style={{ color: STAGE_COLORS[record.stage]?.text || INK }}>{record.stage}</span>
            </span>
            {badge('stage')}
          </div>

          {/* The touch count and, on demand, every event behind it. Renders nothing when the count
              is NULL, so a keyless install sees no affordance at all. */}
          <AiTouchDetail dealId={record.id} count={touchCount} />

          {/* `pre-wrap` because the notes field is a multi-line textarea: collapsing its newlines
              here delivers half of what the user typed. */}
          {record.notes && (
            <p style={{
              fontSize: 14, color: INK_MUTE, marginBottom: 16, lineHeight: 1.5,
              whiteSpace: 'pre-wrap',
            }}>
              {record.notes} {badge('notes')}
            </p>
          )}

          {/* Cleared automatically when a deal leaves `lost`, so this only ever shows on a
              currently-lost deal. */}
          {/* `pre-wrap` for the same reason, and it matters more here: #128 made the reason
              multi-line and this is the ONLY surface that shows it. */}
          {record.lost_reason && (
            <p style={{
              fontSize: 13, color: INK_MUTE, marginBottom: 16, lineHeight: 1.5,
              whiteSpace: 'pre-wrap',
            }}>
              <span style={{ ...mono(10), color: INK_DIM, marginRight: 6 }}>LOST REASON</span>
              {record.lost_reason} {badge('lost_reason')}
            </p>
          )}

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 20 }}>
            <Row label="Deal ID" value={`#${record.id}`} />
            <Row
              label="Contact"
              value={record.contact_id != null && record.contact_name ? (
                // A button, not an <a>: leaving the panel is a leave path like any other, and a
                // link would take a dirty draft with it without asking.
                <button type="button" style={linkButtonStyle}
                  onClick={() => void leaveVia(() => navigate(`/crm/contacts/${record.contact_id}`))}>
                  {record.contact_name}
                </button>
              ) : record.contact_name}
            />
            <Row
              label="Company"
              value={record.company_id != null && record.company_name ? (
                <button type="button" style={linkButtonStyle}
                  onClick={() => void leaveVia(() => navigate(`/crm/companies/${record.company_id}`))}>
                  {record.company_name}
                </button>
              ) : record.company_name}
            />
            {/* `OwnerName`, not a bare name string: an UNASSIGNED owner is a real state (#60) and
                the muted italic is what keeps the word "Unassigned" from reading as somebody's
                name. A JSX element is never blank, so `Row`'s hide-when-empty rule — which is
                exactly the shape that bug takes — cannot suppress it. */}
            <Row label="Owner" value={<OwnerName ownerId={record.owner_id} />} />
            {/* Issue #125, and it renders UNCONDITIONALLY for the same reason the Owner row
                above does: "not set" is a real state, and a hide-when-blank wrapper is exactly
                what makes it unreadable as one. A JSX element is never blank, so `Row`'s
                hide-when-empty rule cannot suppress it.
                Writing through `onSaveDeal` rather than a fetch of its own puts it on the host's
                per-deal write chain, so it orders against a drag or an inline save of the same
                deal instead of racing them. `DealTemperatureIcon` holds the optimistic glyph
                until this settles. */}
            <Row
              label="Temperature"
              value={
                <DealTemperatureIcon
                  value={record.deal_temperature}
                  // Archived: readable, not workable (issue #83), like Mark Won/Lost below.
                  disabled={archivedAt != null}
                  onCycle={async next => {
                    try {
                      await onSaveDeal(record, { deal_temperature: next });
                    } catch {
                      // The host toasts a stage failure itself but stays silent on a
                      // fields-only write, and this control has no inline form to show an
                      // error beside.
                      toast.error('Failed to update deal temperature.');
                    }
                    // Either way: on success for the `lead_score` this just moved, on failure
                    // to put the row back from server truth.
                    await loadDetail();
                  }}
                />
              }
            />
            <Row
              label="Probability"
              value={record.probability > 0 ? `${record.probability}%` : ''}
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
              value={record.expected_close_date}
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
        exiting={exiting}
        onSelect={setLogActivity}
        onNote={setLogNote}
        onLog={() => void handleLog()}
        onClear={clearLog}
      />

      {/* Renders nothing when no deal fields are defined. No `key` here: the shell keys the whole
          body by record id, so ‹ › nav already remounts this. */}
      <CustomFieldsSection
        entityType="deal"
        entityId={record.id}
        sectionStyle={{ borderTop: `1px solid ${LINE}`, paddingTop: 16, marginBottom: 20 }}
      />

      <div style={{ borderTop: `1px solid ${LINE}`, paddingTop: 16, marginBottom: 20 }}>
        <span style={{ ...mono(10, INK_DIM), display: 'block', marginBottom: 12 }}>Activity History</span>
        <ActivityTimeline activities={activity} onUpdate={loadDetail} />
      </div>

      <div style={{ borderTop: `1px solid ${LINE}`, paddingTop: 16 }}>
        <span style={{ ...mono(10, INK_DIM), display: 'block', marginBottom: 12 }}>Chatter</span>
        <NotesThread entityType="deal" entityId={record.id} />
      </div>

      {/* Rendered through a PORTAL by the modal itself, but kept a React CHILD of this body: React
          propagates events along the React tree, not the DOM tree, so staying a child is what keeps
          a click inside the dialog from reaching whatever this panel's own handlers would do with
          it. It also unmounts with the panel. `DetailModal` deliberately renders no portal of its
          own, which is why the dialog needs one. */}
      {askingLostReason && (
        <LostReasonModal
          dealTitle={record.title}
          onCancel={() => setAskingLostReason(false)}
          onConfirm={reason => {
            setAskingLostReason(false);
            // ALWAYS a string, never undefined — that is what routes this to the mark-lost verb
            // even when the rep left the box empty. See `crm/dealStageWrite.ts`.
            void closeOut('lost', reason);
          }}
        />
      )}
    </div>
  );
}

