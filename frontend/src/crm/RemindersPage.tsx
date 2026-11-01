/**
 * Reminders (issue #283), on the shared collection layer — shaped like TodosPage.
 *
 * The corpus is every reminder (the list endpoint's keyset sweep); Mine / status / date
 * filtering is the collection layer's, client-side. An upcoming or paused reminder can be
 * edited or voided; Pause / Resume keep its detail open. Any reminder can be copied with
 * Remind again. Actions with forms close the detail first, as TodosPage does, so no modal
 * is nested in another.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../core/api/client';
import { useAuth } from '../core/auth/AuthContext';
import type { CrmReminder } from '../core/types';
import { CollectionView, useCollectionState } from '../shared/collection';
import type { ListColumn } from '../shared/listview';
import { IconPlus } from '../shared/icons';
import { useIsMobile } from '../shared/useIsMobile';
import { INK, INK_DIM, INK_MUTE, LINE_STRONG, CORAL_TEXT, mono } from '../shared/styles';
import { pageHeading, btnPrimary, btnSecondary, btnSmall } from './styles';
import { RefreshButton } from './components/RefreshButton';
import { ReminderForm, VoidReminderForm } from './components/ReminderForm';
import { REPEAT_LABELS, makeRemindersCollectionConfig, reminderWhen } from './remindersCollection';
import { useCrmCorpus, writeMayHaveLanded, type CrmCorpus } from './usePatchableAssembly';
import { useLocalDay } from './useLocalDay';
import { useUsers } from './useUsers';
import { getFilters } from './gtd/api';
import { formatDay, todayStr } from './gtd/util';

const NO_ROWS: CrmReminder[] = [];

const STATUS_LABEL: Record<CrmReminder['status'], string> = {
  upcoming: 'Upcoming', paused: 'Paused', fired: 'Sent', voided: 'Voided',
};

type Modal =
  | { kind: 'create' }
  | { kind: 'edit'; reminder: CrmReminder }
  | { kind: 'again'; reminder: CrmReminder }
  | { kind: 'void'; reminder: CrmReminder };

export function RemindersPage() {
  const isMobile = useIsMobile();
  const { currentUser } = useAuth();
  // The INSTALL's day, not the browser's: the server validates and fires reminders in the
  // install timezone (the GTD screens read it the same way, #259). On a UTC install a US
  // browser's evening is already tomorrow there, so the browser day would default the
  // form to a date the server refuses as past. useLocalDay re-renders at midnight; until
  // the zone lands, the browser's day stands in.
  useLocalDay();
  const [tz, setTz] = useState<string | undefined>();
  useEffect(() => { getFilters().then(f => setTz(f.tz)).catch(() => {}); }, []);
  const today = todayStr(new Date(), tz);
  const { nameFor, loading: usersLoading } = useUsers();
  const [modal, setModal] = useState<Modal | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const corpus = useCrmCorpus<CrmReminder>(
    useCallback(async (params, signal) => {
      const res = await api<{ reminders: CrmReminder[] }>(`/api/crm/reminders?${params}`, { signal });
      return res.reminders;
    }, []),
  );
  const { upsert, retry } = corpus;

  const columns = useMemo<ListColumn<CrmReminder>[]>(() => [
    { key: 'due', header: 'Date', render: r => reminderWhen(r, today) },
    {
      key: 'note', header: 'Note',
      render: r => (
        <span style={{ textDecoration: r.status === 'voided' ? 'line-through' : undefined }}>{r.note}</span>
      ),
    },
    {
      key: 'people', header: 'People', className: 'hidden md:table-cell',
      render: r => r.people.map(nameFor).join(', '),
    },
    {
      key: 'status', header: 'Status',
      render: r => (
        <span style={{ ...mono(10), color: r.status === 'voided' ? CORAL_TEXT : INK_MUTE }}>
          {STATUS_LABEL[r.status]}
        </span>
      ),
    },
  ], [today, nameFor]);

  const close = () => setModal(null);
  const saved = (r: CrmReminder) => { setModal(null); upsert(r); };

  return (
    <div style={{ padding: isMobile ? '20px 16px' : '32px 44px', maxWidth: 900 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: isMobile ? 16 : 24 }}>
        <h1 style={pageHeading(isMobile)}>Reminders</h1>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <RefreshButton onClick={retry} label="Reload reminders" />
          <button onClick={() => setModal({ kind: 'create' })} style={{ ...btnPrimary, ...btnSmall }}>
            <IconPlus size={13} strokeWidth={2.25} /> {isMobile ? 'Add' : 'Add Reminder'}
          </button>
        </div>
      </div>

      {usersLoading
        ? <p style={{ ...mono(12), color: INK_DIM }}>Loading…</p>
        : (
          <RemindersCollection
            corpus={corpus}
            columns={columns}
            meId={currentUser?.id ?? null}
            today={today}
            nameFor={nameFor}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onAction={m => { setSelectedId(null); setModal(m); }}
          />
        )}

      {modal?.kind === 'create' && <ReminderForm today={today} onClose={close} onSaved={saved} onWriteUncertain={retry} />}
      {modal?.kind === 'edit' && (
        <ReminderForm today={today} reminder={modal.reminder} onClose={close} onSaved={saved} onWriteUncertain={retry} />
      )}
      {modal?.kind === 'again' && (
        <ReminderForm today={today} againFrom={modal.reminder} onClose={close} onSaved={saved} onWriteUncertain={retry} />
      )}
      {modal?.kind === 'void' && (
        <VoidReminderForm reminder={modal.reminder} onClose={close} onSaved={saved} onWriteUncertain={retry} />
      )}
    </div>
  );
}

interface CollectionProps {
  corpus: CrmCorpus<CrmReminder>;
  columns: ListColumn<CrmReminder>[];
  meId: number | null;
  today: string;
  nameFor: (id: number | null) => string;
  selectedId: number | null;
  onSelect: (id: number | null) => void;
  onAction: (m: Modal) => void;
}

function RemindersCollection(
  { corpus, columns, meId, today, nameFor, selectedId, onSelect, onAction }: CollectionProps,
) {
  // The collection can remount its detail during a retry; keep write feedback here.
  const [write, setWrite] = useState<{ id: number; saving: boolean; error: string } | null>(null);
  async function changeStatus(r: CrmReminder) {
    setWrite({ id: r.id, saving: true, error: '' });
    try {
      const action = r.status === 'paused' ? 'resume' : 'pause';
      corpus.upsert(await api<CrmReminder>(`/api/crm/reminders/${r.id}/${action}`, { method: 'POST' }));
      setWrite(null);
    } catch (err: unknown) {
      setWrite({ id: r.id, saving: false, error: err instanceof Error ? err.message : 'Failed to save' });
      if (writeMayHaveLanded(err)) corpus.retry();
    }
  }
  const config = useMemo(() => makeRemindersCollectionConfig({ columns, meId }), [columns, meId]);
  const rows = corpus.items ?? NO_ROWS;
  const state = useCollectionState(config, rows);
  return (
    <CollectionView<CrmReminder>
      config={config}
      state={state}
      items={rows}
      selectedId={selectedId}
      onSelect={id => { setWrite(null); onSelect(id === null ? null : Number(id)); }}
      searchPlaceholder="Search reminders..."
      loading={{
        loading: corpus.loading,
        error: corpus.error,
        itemsLoaded: corpus.itemsLoaded,
        retry: corpus.retry,
      }}
      detail={{
        render: r => (
          <ReminderDetailBody key={r.id} reminder={r} today={today} nameFor={nameFor}
            onAction={onAction} onChangeStatus={() => changeStatus(r)}
            saving={write?.id === r.id && write.saving} error={write?.id === r.id ? write.error : ''} />
        ),
        // Actions with forms close this detail first, so none can be orphaned.
        onRequestClose: () => true,
      }}
    />
  );
}

function ReminderDetailBody(
  { reminder: r, today, nameFor, onAction, onChangeStatus, saving, error }:
  { reminder: CrmReminder; today: string; nameFor: (id: number | null) => string; onAction: (m: Modal) => void;
    onChangeStatus: () => void; saving: boolean; error: string },
) {
  const btn = { ...btnSecondary, padding: '10px 16px', borderRadius: 6, fontSize: 13, border: `1px solid ${LINE_STRONG}`, color: INK };
  return (
    <div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginBottom: 20 }}>
        <DetailRow label="When" value={reminderWhen(r, today)} />
        <DetailRow label="Repeat" value={r.repeat ? REPEAT_LABELS[r.repeat] : 'Does not repeat'} />
        <DetailRow label="Notify" value={r.people.map(nameFor).join(', ')} />
        <DetailRow label="Set by" value={nameFor(r.owner_id)} />
        <DetailRow label="Status" value={STATUS_LABEL[r.status]} />
        {r.fired_at && <DetailRow label="Sent" value={formatDay(r.fired_at)} />}
        {r.status === 'voided' && <DetailRow label="Voided because" value={r.void_reason} />}
      </div>
      {error && <p role="alert" style={{ color: CORAL_TEXT, fontSize: 12, marginBottom: 12 }}>{error}</p>}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {(r.status === 'upcoming' || r.status === 'paused') && (
          <>
            <button disabled={saving} onClick={() => onAction({ kind: 'edit', reminder: r })} style={btn}>Edit</button>
            <button disabled={saving} onClick={onChangeStatus} style={btn}>{r.status === 'paused' ? 'Resume' : 'Pause'}</button>
            <button disabled={saving} onClick={() => onAction({ kind: 'void', reminder: r })} style={btn}>Void</button>
          </>
        )}
        <button disabled={saving} onClick={() => onAction({ kind: 'again', reminder: r })} style={btn}>Remind again</button>
      </div>
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
      <span style={{ ...mono(10), color: INK_DIM }}>{label}</span>
      <span style={{ fontSize: 13, color: INK, textAlign: 'right', whiteSpace: 'pre-wrap' }}>{value}</span>
    </div>
  );
}
