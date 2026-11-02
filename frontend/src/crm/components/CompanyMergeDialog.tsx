import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { api } from '../../core/api/client';
import type { CrmCompany, CrmCompanyMergePlan } from '../../core/types';
import { toast } from '../../shared/toast';
import { BG_ELEV, INK, INK_DIM, LINE_STRONG, SCRIM, SHADOW, inputStyle, labelStyle } from '../../shared/styles';
import { btnDanger, btnSecondary } from '../styles';
import { MAX_ARCHIVE_REASON } from '../constants';
import { PICKER_LIMIT, companyNameOf, companySublabelOf, recordId } from '../dealLinkPickers';
import { writeMayHaveLanded } from '../usePatchableAssembly';
import { RecordCombobox } from './RecordCombobox';

const searchSurvivors = (query: string) => api<{ companies: CrmCompany[] }>(
  `/api/crm/companies?limit=${PICKER_LIMIT}&status=active${query ? `&q=${encodeURIComponent(query)}` : ''}`,
).then(d => d.companies);

function detail(err: unknown, fallback: string): string {
  return typeof err === 'object' && err !== null && 'detail' in err && typeof err.detail === 'string'
    ? err.detail : fallback;
}

interface Props {
  absorbed: CrmCompany;
  onMerged: (survivorId: number) => void;
  onCancel: () => void;
  onWriteUncertain?: () => void | Promise<void>;
}

export function CompanyMergeDialog({ absorbed, onMerged, onCancel, onWriteUncertain }: Props) {
  const [survivor, setSurvivor] = useState<CrmCompany | null>(null);
  const [plan, setPlan] = useState<CrmCompanyMergePlan | null>(null);
  const [error, setError] = useState('');
  const [reason, setReason] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const submitted = useRef(false);
  const requestId = useRef(0);
  const mounted = useRef(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const onCancelRef = useRef(onCancel);
  useEffect(() => { onCancelRef.current = onCancel; });
  const search = useCallback((query: string) => searchSurvivors(query)
    .then(rows => rows.filter(row => row.id !== absorbed.id)), [absorbed.id]);

  useEffect(() => {
    mounted.current = true;
    const previous = document.activeElement;
    dialogRef.current?.querySelector<HTMLInputElement>('input')?.focus();
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        if (!submitted.current) onCancelRef.current();
        return;
      }
      if (e.key !== 'Tab') return;
      const focusable = [...(dialogRef.current?.querySelectorAll<HTMLElement>(
        'textarea, button, [href], input, select, [tabindex]:not([tabindex="-1"])',
      ) ?? [])].filter(el => !el.matches(':disabled'));
      if (!focusable.length) { e.preventDefault(); return; }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      const inside = active instanceof Node && dialogRef.current?.contains(active);
      if (!inside || (e.shiftKey && active === first) || (!e.shiftKey && active === last)) {
        e.preventDefault();
        e.stopPropagation();
        (e.shiftKey ? last : first).focus();
      }
    }
    document.addEventListener('keydown', onKeyDown, { capture: true });
    return () => {
      mounted.current = false;
      document.removeEventListener('keydown', onKeyDown, { capture: true });
      if (previous instanceof HTMLElement && document.contains(previous)) previous.focus();
    };
  }, []);

  async function select(record: CrmCompany | null) {
    if (submitted.current) return;
    const request = ++requestId.current;
    setSurvivor(record);
    setPlan(null);
    setError('');
    if (!record) return;
    try {
      const preview = await api<CrmCompanyMergePlan>(
        `/api/crm/companies/${absorbed.id}/merge-preview?survivor_id=${record.id}`,
      );
      if (mounted.current && request === requestId.current) setPlan(preview);
    } catch (err) {
      if (mounted.current && request === requestId.current) setError(detail(err, 'Could not preview the merge.'));
    }
  }

  async function merge() {
    if (submitted.current || !survivor || !plan || !reason.trim()) return;
    submitted.current = true;
    setSubmitting(true);
    try {
      await api(`/api/crm/companies/${absorbed.id}/merge`, {
        method: 'POST', body: JSON.stringify({ survivor_id: survivor.id, reason }),
      });
    } catch (err) {
      toast.error(detail(err, 'Merge failed.'));
      if (writeMayHaveLanded(err)) {
        await onWriteUncertain?.();
        onCancel();
        return;
      }
      submitted.current = false;
      setSubmitting(false);
      return;
    }
    onMerged(survivor.id);
  }

  return createPortal(
    <div style={{ position: 'fixed', inset: 0, background: SCRIM, zIndex: 150,
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
      <div ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="crm-company-merge-title"
        style={{ background: BG_ELEV, color: INK, border: `1px solid ${LINE_STRONG}`, borderRadius: 6,
          boxShadow: SHADOW, padding: 24, width: '100%', maxWidth: 480, maxHeight: '85dvh', overflowY: 'auto' }}>
        <h2 id="crm-company-merge-title">Merge company</h2>
        <p>Fold <strong>{absorbed.name}</strong> into…</p>
        <fieldset disabled={submitting} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
          <RecordCombobox<CrmCompany> label="Keep" value={survivor?.id ?? null}
            valueLabel={survivor?.name ?? ''} emptyLabel="Choose the company to keep"
            search={search} getId={recordId} getLabel={companyNameOf} getSublabel={companySublabelOf}
            onSelect={select} />
        </fieldset>
        {survivor && !plan && !error && <p>Loading preview…</p>}
        {error && <p role="alert">{error}</p>}
        {plan && <>
          <p>Current estimate; recomputed at merge.</p>
          <ul>
            <li>{plan.moves.contacts} contacts</li>
            <li>{plan.moves.deals} deals ({plan.moves.archived_deals} archived)</li>
            <li>{plan.moves.todos} open todos follow them</li>
            <li>{plan.moves.notes} notes copied</li>
            <li>{plan.moves.custom_fields} custom fields filled</li>
          </ul>
          {plan.fields.map(field => <p key={field.field}>
            {field.field === 'notes' ? 'Appends notes from the duplicate' : `Fills ${field.field} from the duplicate`}
          </p>)}
        </>}
        <label htmlFor="crm-company-merge-reason" style={labelStyle}>Why merge? *</label>
        <textarea id="crm-company-merge-reason" maxLength={MAX_ARCHIVE_REASON} rows={2}
          style={inputStyle} value={reason} disabled={submitting} onChange={e => setReason(e.target.value)} />
        <p style={{ color: INK_DIM, fontSize: 12 }}>Saved to the notes, along with who archived it.</p>
        <p>The duplicate will be archived. This merge cannot be undone.</p>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap' }}>
          <button type="button" style={btnSecondary} disabled={submitting} onClick={onCancel}>Cancel</button>
          <button type="button" style={btnDanger} disabled={submitting || !survivor || !plan || !reason.trim()}
            onClick={merge}>Merge and archive {absorbed.name}</button>
        </div>
      </div>
    </div>, document.body,
  );
}
