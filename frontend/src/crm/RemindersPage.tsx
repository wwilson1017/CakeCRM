/**
 * RemindersPage (issue #6) — reminders CRUD from the UI (the C2 acceptance).
 *
 * Pending / History tabs, a create/edit modal (message, due time, context,
 * recurrence presets), cancel (pending) and delete (history) actions. due_at is a
 * real instant: the datetime-local input is LOCAL and converted to UTC ISO on
 * submit (and back for editing). Two demo affordances (C4): a "fires in 1 minute"
 * quick-fill and a "Process due now" button that runs a heartbeat tick. Keyless.
 */

import { useCallback, useEffect, useState } from 'react';
import { api } from '../core/api/client';
import { useIsMobile } from '../shared/useIsMobile';
import { toast } from '../shared/toast';
import { confirmDialog } from '../shared/confirm';
import { INK, INK_MUTE, INK_SOFT, LINE, GOLD_TEXT, FONT_SANS, labelStyle, inputStyle } from '../shared/styles';
import {
  pagePadding, pageHeading, cardStyle, modalOverlay, modalContent, formTitle,
  btnPrimary, btnSecondary, btnDanger, btnSmall, filterBar, filterTab,
} from './styles';

interface RecurrenceRule {
  type: string; days?: number[]; day?: number; hours?: number; minutes?: number; expression?: string;
}
interface Reminder {
  id: string; message: string; context: string; due_at: string; status: string;
  recurrence_rule: RecurrenceRule | null; series_id: string | null;
  created_at: string; fired_at: string | null; result: string | null;
  is_recurring: boolean; recurrence_description: string | null;
}

const DOW = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];

function pad(n: number): string { return String(n).padStart(2, '0'); }

function isoToLocalInput(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function localInputToISO(local: string): string { return new Date(local).toISOString(); }
function formatDue(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}
function nowPlusMinutes(mins: number): string {
  const d = new Date(Date.now() + mins * 60000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const RECUR_PRESETS = [
  { value: '', label: 'Does not repeat' },
  { value: 'daily', label: 'Daily' },
  { value: 'weekdays', label: 'Weekdays (Mon–Fri)' },
  { value: 'weekly', label: 'Weekly (on this weekday)' },
  { value: 'monthly', label: 'Monthly (on this date)' },
  { value: 'hourly', label: 'Every N hours' },
];

function ruleToPreset(rule: RecurrenceRule | null): string {
  if (!rule) return '';
  if (rule.type === 'daily') return 'daily';
  if (rule.type === 'weekly') {
    const days = rule.days || [];
    if (days.length === 5 && [1, 2, 3, 4, 5].every(d => days.includes(d))) return 'weekdays';
    return 'weekly';
  }
  if (rule.type === 'monthly') return 'monthly';
  if (rule.type === 'interval') return 'hourly';
  return 'cron';  // not representable as a preset
}

function presetToNL(preset: string, dueLocal: string, hoursN: number): string {
  // Derive weekday/day from the UTC representation of the chosen instant — the
  // backend evaluates recurrence rules in UTC, so a rule built from LOCAL calendar
  // fields would drift a day for any time that crosses the UTC date boundary
  // (e.g. US evenings). getUTC* keeps the rule consistent with the due_at we submit.
  const d = new Date(dueLocal);
  const valid = !Number.isNaN(d.getTime());
  switch (preset) {
    case 'daily': return 'daily';
    case 'weekdays': return 'weekly:mon,tue,wed,thu,fri';
    case 'weekly': return `weekly:${DOW[valid ? d.getUTCDay() : 1]}`;
    case 'monthly': return `monthly:${valid ? d.getUTCDate() : 1}`;
    case 'hourly': return `every ${Math.max(1, hoursN)} hours`;
    default: return '';
  }
}

interface FormState { message: string; dueLocal: string; context: string; preset: string; hoursN: number; touchedRecur: boolean; }

const EMPTY_FORM: FormState = {
  message: '', dueLocal: nowPlusMinutes(60), context: '', preset: '', hoursN: 4, touchedRecur: false,
};

export function RemindersPage() {
  const isMobile = useIsMobile();
  const [tab, setTab] = useState<'pending' | 'all'>('pending');
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<Reminder | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [processing, setProcessing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api<{ reminders: Reminder[] }>(`/api/reminders?status=${tab}&limit=100`);
      setReminders(res.reminders);
    } catch {
      toast.error('Failed to load reminders.');
    } finally {
      setLoading(false);
    }
  }, [tab]);

  useEffect(() => { queueMicrotask(load); }, [load]);

  function openCreate() {
    setEditing(null);
    setForm({ ...EMPTY_FORM, dueLocal: nowPlusMinutes(60) });
    setShowForm(true);
  }
  function openEdit(r: Reminder) {
    setEditing(r);
    setForm({
      message: r.message, dueLocal: isoToLocalInput(r.due_at), context: r.context || '',
      preset: ruleToPreset(r.recurrence_rule),
      hoursN: r.recurrence_rule?.type === 'interval' ? (r.recurrence_rule.hours || 4) : 4,
      touchedRecur: false,
    });
    setShowForm(true);
  }

  async function submit() {
    if (!form.message.trim()) { toast.error('A message is required.'); return; }
    if (!form.dueLocal) { toast.error('A due time is required.'); return; }
    setSaving(true);
    try {
      const due_at = localInputToISO(form.dueLocal);
      if (editing) {
        const body: Record<string, unknown> = {
          message: form.message.trim(), due_at, context: form.context,
        };
        // Only send recurrence if the user changed it (preserves cron rules).
        if (form.touchedRecur) body.recurrence = presetToNL(form.preset, form.dueLocal, form.hoursN);
        await api(`/api/reminders/${editing.id}`, { method: 'PATCH', body: JSON.stringify(body) });
        toast.success('Reminder updated.');
      } else {
        await api('/api/reminders', {
          method: 'POST',
          body: JSON.stringify({
            message: form.message.trim(), due_at, context: form.context,
            recurrence: presetToNL(form.preset, form.dueLocal, form.hoursN),
          }),
        });
        toast.success('Reminder set.');
      }
      setShowForm(false);
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message.replace(/^API error \d+: /, '') : 'Failed to save reminder.');
    } finally {
      setSaving(false);
    }
  }

  async function cancel(r: Reminder) {
    const ok = await confirmDialog({
      title: 'Cancel reminder?',
      message: r.is_recurring ? 'This stops the whole recurring series.' : 'This reminder will not fire.',
      confirmLabel: 'Cancel reminder', danger: true,
    });
    if (!ok) return;
    try {
      await api(`/api/reminders/${r.id}/cancel`, { method: 'POST' });
      toast.success('Reminder cancelled.');
      load();
    } catch { toast.error('Failed to cancel reminder.'); }
  }

  async function remove(r: Reminder) {
    const ok = await confirmDialog({
      title: 'Delete reminder?', message: 'This permanently removes it from history.',
      confirmLabel: 'Delete', danger: true,
    });
    if (!ok) return;
    try {
      await api(`/api/reminders/${r.id}`, { method: 'DELETE' });
      load();
    } catch { toast.error('Failed to delete reminder.'); }
  }

  async function processDueNow() {
    setProcessing(true);
    try {
      const res = await api<{ reminders_processed: number }>('/api/heartbeat/run-now', {
        method: 'POST', body: JSON.stringify({ run_ai_turn: false }),
      });
      toast.success(res.reminders_processed > 0
        ? `Fired ${res.reminders_processed} due reminder${res.reminders_processed === 1 ? '' : 's'}.`
        : 'No reminders were due.');
      load();
    } catch { toast.error('Failed to process reminders.'); }
    finally { setProcessing(false); }
  }

  const statusColor = (s: string) => s === 'pending' ? GOLD_TEXT : s === 'fired' ? INK_SOFT : INK_MUTE;

  return (
    <div style={pagePadding(isMobile)}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
        <h1 style={pageHeading(isMobile)}>Reminders</h1>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <button onClick={processDueNow} disabled={processing} style={{ ...btnSecondary, ...btnSmall, opacity: processing ? 0.6 : 1 }}>
            {processing ? 'Processing…' : 'Process due now'}
          </button>
          <button onClick={openCreate} style={{ ...btnPrimary, ...btnSmall }}>New reminder</button>
        </div>
      </div>

      <div style={{ marginTop: 24 }}>
        <div style={filterBar(isMobile)}>
          <button style={filterTab(isMobile, tab === 'pending')} onClick={() => setTab('pending')}>Pending</button>
          <button style={filterTab(isMobile, tab === 'all')} onClick={() => setTab('all')}>History</button>
        </div>

        {loading ? (
          <p style={{ fontFamily: FONT_SANS, fontSize: 14, color: INK_MUTE }}>Loading…</p>
        ) : reminders.length === 0 ? (
          <div style={{ ...cardStyle, padding: 28, textAlign: 'center' }}>
            <p style={{ fontFamily: FONT_SANS, fontSize: 14, color: INK_MUTE, margin: 0 }}>
              {tab === 'pending' ? 'No pending reminders. Create one to get a nudge at the right time.' : 'No reminder history yet.'}
            </p>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {reminders.map(r => (
              <div key={r.id} style={{ ...cardStyle, padding: 16, display: 'flex', gap: 12, alignItems: 'flex-start' }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontFamily: FONT_SANS, fontSize: 15, color: INK, fontWeight: 500 }}>{r.message}</div>
                  <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', margin: '5px 0 0', alignItems: 'center' }}>
                    <span style={{ fontFamily: FONT_SANS, fontSize: 13, color: INK_MUTE }}>{formatDue(r.due_at)}</span>
                    <span style={{ fontFamily: FONT_SANS, fontSize: 12, color: statusColor(r.status), textTransform: 'capitalize' }}>{r.status}</span>
                    {r.recurrence_description && (
                      <span style={{ fontFamily: FONT_SANS, fontSize: 12, color: INK_SOFT, border: `1px solid ${LINE}`, borderRadius: 4, padding: '1px 7px' }}>
                        ↻ {r.recurrence_description}
                      </span>
                    )}
                  </div>
                  {r.context && <div style={{ fontFamily: FONT_SANS, fontSize: 13, color: INK_MUTE, marginTop: 6, whiteSpace: 'pre-wrap' }}>{r.context}</div>}
                  {r.status !== 'pending' && r.result && (
                    <div style={{ fontFamily: FONT_SANS, fontSize: 12, color: INK_SOFT, marginTop: 6 }}>{r.result}</div>
                  )}
                </div>
                <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
                  {r.status === 'pending' ? (
                    <>
                      <button onClick={() => openEdit(r)} style={{ ...btnSecondary, ...btnSmall }}>Edit</button>
                      <button onClick={() => cancel(r)} style={{ ...btnDanger, ...btnSmall }}>Cancel</button>
                    </>
                  ) : (
                    <button onClick={() => remove(r)} style={{ ...btnSecondary, ...btnSmall }}>Delete</button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {showForm && (
        <div style={modalOverlay(isMobile)} onClick={() => setShowForm(false)}>
          <div style={modalContent(isMobile, 460)} onClick={e => e.stopPropagation()}>
            <div style={formTitle}>{editing ? 'Edit reminder' : 'New reminder'}</div>

            <div style={{ marginBottom: 16 }}>
              <label style={labelStyle}>Message</label>
              <input style={inputStyle} value={form.message} autoFocus
                onChange={e => setForm(f => ({ ...f, message: e.target.value }))}
                placeholder="Follow up with Dana about the renewal" />
            </div>

            <div style={{ marginBottom: 16 }}>
              <label style={labelStyle}>When</label>
              <input style={inputStyle} type="datetime-local" value={form.dueLocal}
                onChange={e => setForm(f => ({ ...f, dueLocal: e.target.value }))} />
              <button
                onClick={() => setForm(f => ({ ...f, dueLocal: nowPlusMinutes(1) }))}
                style={{ ...btnSecondary, padding: '4px 10px', fontSize: 12, marginTop: 8 }}>
                Fires in 1 minute
              </button>
            </div>

            <div style={{ marginBottom: 16 }}>
              <label style={labelStyle}>Repeat</label>
              <select style={{ ...inputStyle, cursor: 'pointer' }} value={form.preset}
                onChange={e => setForm(f => ({ ...f, preset: e.target.value, touchedRecur: true }))}>
                {RECUR_PRESETS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                {form.preset === 'cron' && <option value="cron">Custom (cron)</option>}
              </select>
              {form.preset === 'hourly' && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}>
                  <span style={{ fontFamily: FONT_SANS, fontSize: 13, color: INK_MUTE }}>Every</span>
                  <input type="number" min={1} max={168} value={form.hoursN} style={{ ...inputStyle, width: 80 }}
                    onChange={e => setForm(f => ({ ...f, hoursN: Number(e.target.value) || 1, touchedRecur: true }))} />
                  <span style={{ fontFamily: FONT_SANS, fontSize: 13, color: INK_MUTE }}>hours</span>
                </div>
              )}
            </div>

            <div style={{ marginBottom: 20 }}>
              <label style={labelStyle}>Context (optional)</label>
              <textarea style={{ ...inputStyle, minHeight: 60, resize: 'vertical' }} value={form.context}
                onChange={e => setForm(f => ({ ...f, context: e.target.value }))}
                placeholder="Extra detail the assistant should have when this fires" />
            </div>

            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <button onClick={() => setShowForm(false)} style={btnSecondary}>Cancel</button>
              <button onClick={submit} disabled={saving} style={{ ...btnPrimary, opacity: saving ? 0.6 : 1 }}>
                {saving ? 'Saving…' : editing ? 'Save' : 'Set reminder'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
