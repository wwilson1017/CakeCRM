import { useState, useRef, useEffect, type ReactNode, type CSSProperties } from 'react';
import {
  type AdvancedFilters,
  type ClosePreset,
  type ActivityPreset,
  EMPTY_ADVANCED,
  advancedActiveCount,
} from '../pipelineFilters';
import { STAGE_ORDER, STAGE_COLORS } from '../constants';
import {
  INK, INK_MUTE, INK_DIM, LINE, LINE_STRONG, BG_RAISED, BG_ELEV,
  ACCENT, ACCENT_INK, FONT_SANS,
} from '../../shared/styles';
import { IconSearch, IconX, IconChevron } from '../../shared/icons';

const CLOSE_OPTIONS: { value: ClosePreset; label: string }[] = [
  { value: 'overdue', label: 'Overdue' },
  { value: 'next7', label: 'Next 7 days' },
  { value: 'thisMonth', label: 'This month' },
  { value: 'noDate', label: 'No close date' },
];

// Activity is deal-scoped (deal-level activity + un-archived deal notes), so the
// "no activity" bucket is honestly labelled "logged", not "never contacted".
const ACTIVITY_OPTIONS: { value: ActivityPreset; label: string }[] = [
  { value: 'le7', label: 'Active (≤ 7 days)' },
  { value: 'le30', label: 'Active (≤ 30 days)' },
  { value: 'stale30', label: 'No activity in 30+ days' },
  { value: 'none', label: 'No activity logged' },
];

interface Props {
  search: string;
  advanced: AdvancedFilters;
  onSearchChange: (s: string) => void;
  onAdvancedChange: (f: AdvancedFilters) => void;
  isMobile: boolean;
}

/** Lightweight inline advanced-filter bar for the pipeline board: a free-text search
 *  input plus a row of click-to-open facet popovers, removable active-filter pills, and
 *  a clear-all. Not a modal or drawer; popovers close on outside-click / Escape. Filters
 *  stack (AND). Controlled — the page owns the state and persists it to sessionStorage. */
export default function PipelineFilterBar({ search, advanced, onSearchChange, onAdvancedChange, isMobile }: Props) {
  const closeLabel = CLOSE_OPTIONS.find(o => o.value === advanced.closeDate)?.label;
  const activityLabel = ACTIVITY_OPTIONS.find(o => o.value === advanced.lastActivity)?.label;
  const hasValue = advanced.valueMin !== null || advanced.valueMax !== null;

  const toggleStage = (stage: string) => {
    const next = advanced.stages.includes(stage)
      ? advanced.stages.filter(s => s !== stage)
      : [...advanced.stages, stage];
    onAdvancedChange({ ...advanced, stages: next });
  };

  const valueSummary = () => {
    const fmt = (n: number) => `$${n.toLocaleString()}`;
    if (advanced.valueMin !== null && advanced.valueMax !== null) return `${fmt(advanced.valueMin)}–${fmt(advanced.valueMax)}`;
    if (advanced.valueMin !== null) return `≥ ${fmt(advanced.valueMin)}`;
    if (advanced.valueMax !== null) return `≤ ${fmt(advanced.valueMax)}`;
    return 'Value';
  };

  const activeCount = advancedActiveCount(advanced);
  const clearAll = () => { onSearchChange(''); onAdvancedChange(EMPTY_ADVANCED); };

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 8 }}>
      {/* Search */}
      <div style={{
        flex: isMobile ? '1 1 100%' : '1 1 220px', minWidth: isMobile ? undefined : 200,
        display: 'flex', alignItems: 'center', gap: 8,
        background: BG_RAISED, border: `1px solid ${LINE}`, borderRadius: 4, padding: '0 12px',
      }}>
        <IconSearch size={14} strokeWidth={1.85} style={{ color: INK_DIM, flexShrink: 0 }} />
        <input
          type="text" placeholder="Search deals, contacts, companies..."
          value={search} onChange={e => onSearchChange(e.target.value)}
          style={{
            flex: 1, background: 'transparent', border: 'none', color: INK,
            padding: '9px 0', fontSize: 13, outline: 'none', fontFamily: FONT_SANS,
          }}
        />
      </div>

      {/* Stage facet */}
      <FacetButton label={advanced.stages.length > 0 ? `Stage · ${advanced.stages.length}` : 'Stage'} active={advanced.stages.length > 0}>
        <div style={{ width: 180, maxHeight: 260, overflowY: 'auto' }}>
          {STAGE_ORDER.map(stage => (
            <label key={stage} style={{
              display: 'flex', alignItems: 'center', gap: 8, padding: '5px 6px',
              borderRadius: 4, cursor: 'pointer', fontSize: 13, color: INK, textTransform: 'capitalize',
            }}>
              <input type="checkbox" checked={advanced.stages.includes(stage)} onChange={() => toggleStage(stage)} style={{ width: 14, height: 14, accentColor: ACCENT }} />
              <span style={{ width: 9, height: 9, borderRadius: '50%', flexShrink: 0, background: STAGE_COLORS[stage]?.color || INK_DIM }} />
              <span>{stage}</span>
            </label>
          ))}
          {advanced.stages.length > 0 && (
            <button onClick={() => onAdvancedChange({ ...advanced, stages: [] })} style={clearLinkStyle}>Clear stages</button>
          )}
        </div>
      </FacetButton>

      {/* Value facet */}
      <FacetButton label={valueSummary()} active={hasValue}>
        <div style={{ width: 200, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ fontSize: 11, color: INK_MUTE }}>Deal value ($)</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <input
              type="number" inputMode="numeric" placeholder="Min" value={advanced.valueMin ?? ''}
              onChange={e => onAdvancedChange({ ...advanced, valueMin: e.target.value === '' ? null : Number(e.target.value) })}
              style={valueInputStyle}
            />
            <span style={{ color: INK_MUTE }}>–</span>
            <input
              type="number" inputMode="numeric" placeholder="Max" value={advanced.valueMax ?? ''}
              onChange={e => onAdvancedChange({ ...advanced, valueMax: e.target.value === '' ? null : Number(e.target.value) })}
              style={valueInputStyle}
            />
          </div>
          {hasValue && (
            <button onClick={() => onAdvancedChange({ ...advanced, valueMin: null, valueMax: null })} style={clearLinkStyle}>Clear value</button>
          )}
        </div>
      </FacetButton>

      {/* Close-date facet */}
      <FacetButton label={closeLabel ? `Close · ${closeLabel}` : 'Close date'} active={!!advanced.closeDate}>
        <RadioList options={CLOSE_OPTIONS} value={advanced.closeDate} onSelect={v => onAdvancedChange({ ...advanced, closeDate: v })} />
      </FacetButton>

      {/* Last-activity facet */}
      <FacetButton label={activityLabel ? `Activity · ${activityLabel}` : 'Deal activity'} active={!!advanced.lastActivity}>
        <RadioList options={ACTIVITY_OPTIONS} value={advanced.lastActivity} onSelect={v => onAdvancedChange({ ...advanced, lastActivity: v })} />
      </FacetButton>

      {/* Active facet pills */}
      {advanced.stages.map(stage => (
        <Pill key={`st-${stage}`} label={stage} capitalize onRemove={() => toggleStage(stage)} />
      ))}
      {hasValue && <Pill label={valueSummary()} onRemove={() => onAdvancedChange({ ...advanced, valueMin: null, valueMax: null })} />}
      {closeLabel && <Pill label={`Close: ${closeLabel}`} onRemove={() => onAdvancedChange({ ...advanced, closeDate: null })} />}
      {activityLabel && <Pill label={activityLabel} onRemove={() => onAdvancedChange({ ...advanced, lastActivity: null })} />}

      {(activeCount > 0 || search.trim() !== '') && (
        <button onClick={clearAll} style={{ ...clearLinkStyle, textDecoration: 'underline', padding: '4px 6px' }}>Clear filters</button>
      )}
    </div>
  );
}

const clearLinkStyle: CSSProperties = {
  background: 'none', border: 'none', color: INK_MUTE, fontSize: 12,
  cursor: 'pointer', fontFamily: FONT_SANS, padding: '4px 2px', marginTop: 4,
};

const valueInputStyle: CSSProperties = {
  width: '100%', minWidth: 0, boxSizing: 'border-box',
  background: BG_RAISED, border: `1px solid ${LINE_STRONG}`, color: INK,
  borderRadius: 4, padding: '7px 8px', fontSize: 13, outline: 'none', fontFamily: FONT_SANS,
};

function FacetButton({ label, active, children }: { label: string; active: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open]);

  return (
    <div ref={ref} style={{ position: 'relative', flexShrink: 0 }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 5, padding: '7px 12px', borderRadius: 4,
          fontSize: 13, fontWeight: 500, cursor: 'pointer', fontFamily: FONT_SANS,
          whiteSpace: 'nowrap', transition: 'background 0.15s, color 0.15s',
          background: active ? ACCENT : 'rgba(31,35,40,0.045)',
          color: active ? ACCENT_INK : INK_MUTE,
          border: `1px solid ${active ? ACCENT : LINE}`,
        }}
      >
        {label}
        <IconChevron size={12} strokeWidth={2.5} style={{ opacity: 0.7 }} />
      </button>
      {open && (
        <div style={{
          position: 'absolute', left: 0, top: '100%', marginTop: 4, zIndex: 40,
          background: BG_ELEV, border: `1px solid ${LINE_STRONG}`, borderRadius: 6, padding: 12,
          boxShadow: '0 8px 40px rgba(31,35,40,0.18)',
        }}>
          {children}
        </div>
      )}
    </div>
  );
}

function RadioList<T extends string>({ options, value, onSelect }: {
  options: { value: T; label: string }[];
  value: T | null;
  onSelect: (v: T | null) => void;
}) {
  return (
    <div style={{ width: 190, display: 'flex', flexDirection: 'column', gap: 2 }}>
      {options.map(o => {
        const selected = value === o.value;
        return (
          <button
            key={o.value}
            onClick={() => onSelect(selected ? null : o.value)}
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              width: '100%', textAlign: 'left', padding: '7px 8px', borderRadius: 4,
              fontSize: 13, cursor: 'pointer', border: 'none', background: 'transparent',
              fontFamily: FONT_SANS, fontWeight: selected ? 600 : 400,
              color: selected ? ACCENT : INK,
            }}
          >
            {o.label}
            {selected && <span style={{ color: ACCENT }}>✓</span>}
          </button>
        );
      })}
    </div>
  );
}

function Pill({ label, capitalize, onRemove }: { label: string; capitalize?: boolean; onRemove: () => void }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 4, padding: '4px 4px 4px 10px',
      background: BG_RAISED, color: INK_MUTE, borderRadius: 4, fontSize: 12,
      fontFamily: FONT_SANS, textTransform: capitalize ? 'capitalize' : 'none',
    }}>
      {label}
      <button
        onClick={onRemove} aria-label={`Remove ${label} filter`}
        style={{
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          width: 16, height: 16, borderRadius: '50%', border: 'none', background: 'none',
          color: INK_DIM, cursor: 'pointer', padding: 0,
        }}
      >
        <IconX size={11} strokeWidth={2.25} />
      </button>
    </span>
  );
}
