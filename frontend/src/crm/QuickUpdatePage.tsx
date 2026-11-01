import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { api, ApiError } from '../core/api/client';
import type { CrmDeal } from '../core/types';
import SearchInput from '../shared/search/SearchInput';
import { useIsMobile } from '../shared/useIsMobile';
import { BG_CARD, INK, INK_DIM, LINE, inputStyle, labelStyle } from '../shared/styles';
import { toast } from '../shared/toast';
import { pageHeading, btnPrimary, btnSecondary, btnSmall, btnDanger } from './styles';
import { ClosedOnModal } from './components/ClosedOnModal';
import { LostReasonModal } from './components/LostReasonModal';
import { useUsers } from './useUsers';
import { OPEN_STAGES } from './constants';
import {
  buildBody, closedSuffix, confirmBody, parseValueInput, stageLabel, valueLabel,
  type Fields, type QuickUpdateBody, type QuickUpdatePreview, type TileKey, type Tiles,
} from './quickUpdate';

const EMPTY_TILES: Tiles = { note: false, value: false, stage: false, followup: false };
const EMPTY_FIELDS: Fields = { note: '', value: '', followup: '' };
const TILES: [TileKey, string][] = [['note', 'Note'], ['value', 'Value'], ['stage', 'Stage'], ['followup', 'Follow-up']];
const cardStyle: CSSProperties = { overflowWrap: 'anywhere', background: BG_CARD, border: `1px solid ${LINE}`, borderRadius: 8, padding: 16 };
const fieldStyle = { ...inputStyle, width: '100%' };
function detail(error: unknown, fallback: string): string { return error instanceof ApiError ? error.detail : fallback; }

function DealCard({ deal, owner }: { deal: CrmDeal; owner: string }) {
  return <section style={cardStyle} aria-label="Selected deal">
    <h2 style={{ fontSize: 20, margin: '0 0 8px', overflowWrap: 'anywhere' }}>{deal.title}</h2>
    <p style={{ color: INK_DIM }}>{deal.company_name ?? 'no company'} · owner {owner}</p>
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
      <span style={{ border: `1px solid ${LINE}`, borderRadius: 4, padding: '4px 8px' }}>{stageLabel(deal.stage)}</span>
      <span style={{ border: `1px solid ${LINE}`, borderRadius: 4, padding: '4px 8px' }}>{valueLabel(deal.value, deal.currency)}</span>
    </div>
    {deal.stage === 'won' && deal.closed_on && <p>Closed on {deal.closed_on}</p>}
    {deal.stage === 'lost' && deal.lost_reason && <p>Lost — {deal.lost_reason}</p>}
  </section>;
}

function BakerPreviewCard({ preview, noteLine, setNoteLine, saving, onCancel, onConfirm }: {
  preview: QuickUpdatePreview; noteLine: string; setNoteLine: (value: string) => void;
  saving: boolean; onCancel: () => void; onConfirm: () => void;
}) {
  return <section aria-label="Baker's preview" style={cardStyle}>
    <h3>Baker will save this to {preview.deal.title}</h3>
    <p style={{ color: INK_DIM }}>now in {stageLabel(preview.deal.stage)}</p>
    <ul>{preview.changes.map((line, i) => <li key={i}>{line}</li>)}</ul>
    {preview.warnings.map((line, i) => <p key={i} style={{ color: INK_DIM }}>{line}</p>)}
    {preview.needs.includes('note') && <label style={labelStyle}>What happened? One line for the note
      <input aria-label="What happened? One line for the note" maxLength={4000} style={fieldStyle}
        value={noteLine} disabled={saving} onChange={e => setNoteLine(e.target.value)} />
    </label>}
    <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
      <button style={btnSecondary} disabled={saving} onClick={onCancel}>Cancel</button>
      <button style={btnPrimary} disabled={saving || !confirmBody(preview, noteLine)} onClick={onConfirm}>Confirm</button>
    </div>
  </section>;
}

export function QuickUpdatePage() {
  const mobile = useIsMobile();
  const { nameFor } = useUsers();
  const [q, setQ] = useState('');
  const [searchNonce, setSearchNonce] = useState(0);
  const [search, setSearch] = useState<{ q: string; nonce: number; deals?: CrmDeal[]; error?: string } | null>(null);
  const [deal, setDeal] = useState<CrmDeal | null>(null);
  const [on, setOn] = useState<Tiles>(EMPTY_TILES);
  const [fields, setFields] = useState<Fields>(EMPTY_FIELDS);
  const [stage, setStage] = useState<string | null>(null);
  const [modal, setModal] = useState<'won' | 'lost' | null>(null);
  const [saving, setSaving] = useState(false);
  const saveLock = useRef(false);
  const [saveError, setSaveError] = useState('');
  const [aiReady, setAiReady] = useState(false);
  const [sentence, setSentence] = useState('');
  const [asking, setAsking] = useState(false);
  const [askError, setAskError] = useState('');
  const [preview, setPreview] = useState<QuickUpdatePreview | null>(null);
  const [noteLine, setNoteLine] = useState('');
  const askSeq = useRef(0);

  useEffect(() => {
    let active = true;
    const sequence = askSeq;
    api<{ ai_ready: boolean }>('/api/setup/status').then(r => { if (active) setAiReady(r.ai_ready); }).catch(() => {});
    return () => { active = false; sequence.current++; };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    api<{ deals: CrmDeal[] }>(`/api/crm/quick-update/deals?q=${encodeURIComponent(q)}`, { signal: controller.signal })
      .then(r => { if (!controller.signal.aborted) setSearch({ q, nonce: searchNonce, deals: r.deals }); })
      .catch(e => { if (!controller.signal.aborted) setSearch({ q, nonce: searchNonce, error: detail(e, 'Could not search — try again.') }); });
    return () => controller.abort();
  }, [q, searchNonce]);

  function resetForm() {
    askSeq.current++;
    setOn(EMPTY_TILES); setFields(EMPTY_FIELDS); setStage(null); setModal(null);
    setSaveError(''); setSentence(''); setAsking(false); setAskError(''); setPreview(null); setNoteLine('');
  }
  function backToSearch() {
    resetForm(); setDeal(null); setQ(''); setSearchNonce(n => n + 1);
  }
  async function post(body: QuickUpdateBody) {
    if (!deal || saveLock.current) return;
    saveLock.current = true; setSaving(true); setSaveError(''); setModal(null);
    // A pending preview must not reopen over the result of a manual save.
    askSeq.current++; setAsking(false);
    try {
      await api<{ deal_id: number; saved: string[] }>(`/api/crm/quick-update/deals/${deal.id}`, { method: 'POST', body: JSON.stringify(body) });
      toast.success(`Saved to ${deal.title}.`);
      backToSearch();
    } catch (e) {
      setSaveError(e instanceof ApiError && e.status >= 400 && e.status < 500
        ? `Not saved: ${e.detail}`
        : "Couldn't confirm the save — open the deal and check before trying again.");
    } finally { saveLock.current = false; setSaving(false); }
  }
  async function ask() {
    if (!deal || !sentence.trim() || asking || saving) return;
    const seq = ++askSeq.current;
    setAsking(true); setAskError('');
    try {
      const result = await api<QuickUpdatePreview>(`/api/crm/quick-update/deals/${deal.id}/preview`, { method: 'POST', body: JSON.stringify({ sentence }) });
      if (seq === askSeq.current) { setPreview(result); setNoteLine(''); }
    } catch (e) {
      if (seq === askSeq.current) setAskError(detail(e, 'Baker is unavailable right now — use the buttons above'));
    } finally { if (seq === askSeq.current) setAsking(false); }
  }
  const body = buildBody(on, fields, stage);
  const invalidValue = on.value && parseValueInput(fields.value) === null;
  const locked = saving || preview !== null;
  const currentSearch = search?.q === q && search.nonce === searchNonce ? search : null;
  return <div style={{ maxWidth: 480, margin: '0 auto', padding: mobile ? '20px 16px' : '28px 24px', color: INK }}>
    <h1 style={{ ...pageHeading(mobile), marginBottom: 16 }}>Quick update</h1>
    {!deal ? <>
      <p style={{ color: INK_DIM }}>Find a deal and log what happened.</p>
      <SearchInput value={q} onChange={setQ} placeholder="Search a lead, company or contact" resetNonce={searchNonce} />
      {!currentSearch && <p role="status">Searching…</p>}
      {currentSearch?.error && <p role="alert">{currentSearch.error}</p>}
      {currentSearch?.deals && (currentSearch.deals.length ? <ul style={{ listStyle: 'none', padding: 0 }}>
        {currentSearch.deals.map(row => <li key={row.id} style={{ marginBottom: 8 }}>
          <button style={{ ...cardStyle, width: '100%', textAlign: 'left', color: INK, cursor: 'pointer', opacity: closedSuffix(row.stage) ? 0.45 : 1 }}
            onClick={() => { resetForm(); setDeal(row); }}>
            <strong>{row.title}{closedSuffix(row.stage)}</strong>
            <span style={{ display: 'block', marginTop: 6, color: INK_DIM }}>{stageLabel(row.stage)} · {row.company_name ?? row.contact_name ?? 'no company'} · {valueLabel(row.value, row.currency)}</span>
          </button>
        </li>)}
      </ul> : <p>{q.trim() ? 'No match. Try fewer letters.' : 'No deals yet.'}</p>)}
    </> : <div style={{ display: 'grid', gap: 16 }}>
      <DealCard deal={deal} owner={nameFor(deal.owner_id)} />
      <fieldset disabled={locked} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
        <legend style={labelStyle}>What changed?</legend>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 12 }}>
          {TILES.map(([key, label]) => <button key={key} aria-pressed={on[key]} style={{ ...(on[key] ? btnPrimary : btnSecondary), minHeight: 44 }}
            onClick={() => { setOn({ ...on, [key]: !on[key] }); setSaveError(''); }}>{label}</button>)}
        </div>
        <div style={{ display: 'grid', gap: 12 }}>
          {on.note && <label style={labelStyle}>Note<textarea aria-label="Note" rows={3} maxLength={4000} style={fieldStyle}
            value={fields.note} onChange={e => setFields({ ...fields, note: e.target.value })} /></label>}
          {on.value && <label style={labelStyle}>Value ({deal.currency})<input aria-label="Value" inputMode="decimal" style={fieldStyle}
            value={fields.value} onChange={e => setFields({ ...fields, value: e.target.value })} /></label>}
          {on.stage && <div role="group" aria-label="Stage" style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {OPEN_STAGES.map(s => <button key={s} aria-pressed={stage === s} style={stage === s ? btnPrimary : btnSmall} onClick={() => setStage(s)}>{stageLabel(s)}</button>)}
          </div>}
          {on.followup && <label style={labelStyle}>Follow-up<input aria-label="Follow-up" maxLength={500} style={fieldStyle}
            value={fields.followup} onChange={e => setFields({ ...fields, followup: e.target.value })} /></label>}
        </div>
      </fieldset>
      {Object.values(on).some(Boolean) && !preview && <button style={btnPrimary} disabled={saving || invalidValue || !Object.keys(body).length} onClick={() => void post(body)}>{saving ? 'Saving…' : 'Save'}</button>}
      <div style={{ display: 'flex', gap: 8 }}>
        <button style={btnSecondary} disabled={locked || invalidValue} onClick={() => setModal('won')}>Mark Won</button>
        <button style={btnDanger} disabled={locked || invalidValue} onClick={() => setModal('lost')}>Mark Lost</button>
      </div>
      {saveError && <p role="alert">{saveError}</p>}
      {aiReady && (preview ? <BakerPreviewCard preview={preview} noteLine={noteLine} setNoteLine={setNoteLine} saving={saving}
        onCancel={() => { askSeq.current++; setPreview(null); }} onConfirm={() => {
          const confirmed = confirmBody(preview, noteLine);
          if (confirmed && preview.deal.id === deal.id) void post(confirmed);
        }} /> : <form style={cardStyle} onSubmit={e => { e.preventDefault(); void ask(); }}>
        <label htmlFor="quick-update-baker" style={labelStyle}>Or just tell Baker</label>
        <input id="quick-update-baker" maxLength={500} placeholder="sent samples, move to Proposal, value 2500" enterKeyHint="send"
          style={fieldStyle} value={sentence} disabled={saving || asking} onChange={e => setSentence(e.target.value)} />
        <button style={{ ...btnSecondary, marginTop: 12 }} disabled={saving || asking || !sentence.trim()}>{asking ? 'Asking…' : 'Ask'}</button>
        {askError && <p role="alert">{askError}</p>}
      </form>)}
      <button style={btnSmall} disabled={saving} onClick={backToSearch}>← pick a different deal</button>
      {modal === 'won' && <ClosedOnModal dealTitle={deal.title} onCancel={() => setModal(null)}
        onConfirm={day => void post({ ...buildBody(on, fields, null), won: true, closed_on: day })} />}
      {modal === 'lost' && <LostReasonModal dealTitle={deal.title} onCancel={() => setModal(null)}
        onConfirm={reason => void post({ ...buildBody(on, fields, null), lost: true, lost_reason: reason })} />}
    </div>}
  </div>;
}
