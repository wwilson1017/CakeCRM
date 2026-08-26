/**
 * StageChipBar — the mobile jump bar for the pipeline board (issue #74).
 *
 * On a phone the board is a horizontally snapped scroller showing roughly one column at a time,
 * which makes "get me to Negotiation" a lot of swiping. This is a row of stage pills that scrolls
 * the matching column into view, plus a readout of which column you are currently on.
 *
 * `stages` is the EXACT set the board rendered (post visibility-toggle, post stage-facet), so a
 * chip can never point at a column that is not there and the counts always match the columns.
 */

import { useEffect, useRef } from 'react';
import { STAGE_COLORS } from '../constants';
import { stageLabel } from '../pipelineBoard';
import { ACCENT, ACCENT_INK, BG_CARD, FONT_DISPLAY, INK, INK_DIM, LINE } from '../../shared/styles';

export interface StageChip {
  stage: string;
  count: number;
}

export default function StageChipBar({
  stages,
  activeStage,
  onSelect,
}: {
  stages: StageChip[];
  activeStage: string | null;
  onSelect: (stage: string) => void;
}) {
  const chipRefs = useRef<Map<string, HTMLButtonElement>>(new Map());

  // Keep the active chip visible as the board scrolls under the finger — without this the
  // highlight walks off the end of the bar and stops being a position readout.
  useEffect(() => {
    if (!activeStage) return;
    chipRefs.current.get(activeStage)?.scrollIntoView({
      behavior: 'smooth',
      inline: 'center',
      block: 'nearest',
    });
  }, [activeStage]);

  if (stages.length === 0) return null;

  return (
    <div
      style={{
        display: 'flex',
        gap: 6,
        overflowX: 'auto',
        paddingBottom: 8,
        marginBottom: 4,
        scrollbarWidth: 'none',
      }}
    >
      {stages.map(({ stage, count }) => {
        const active = stage === activeStage;
        return (
          <button
            key={stage}
            ref={el => {
              if (el) chipRefs.current.set(stage, el);
              else chipRefs.current.delete(stage);
            }}
            type="button"
            onClick={() => onSelect(stage)}
            aria-current={active ? 'true' : undefined}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              flexShrink: 0,
              padding: '6px 12px',
              borderRadius: 999,
              fontFamily: FONT_DISPLAY,
              fontSize: 13,
              cursor: 'pointer',
              // Accent here is a FILL with ACCENT_INK on top, not accent-as-text — the WCAG
              // rule that sends accent text through ACCENT_TEXT does not apply to a filled pill.
              background: active ? ACCENT : BG_CARD,
              color: active ? ACCENT_INK : INK,
              border: `1px solid ${active ? ACCENT : LINE}`,
            }}
          >
            <span
              style={{
                width: 7,
                height: 7,
                borderRadius: '50%',
                flexShrink: 0,
                background: active ? ACCENT_INK : STAGE_COLORS[stage]?.color || INK_DIM,
              }}
            />
            {stageLabel(stage)}
            <span style={{ opacity: 0.7 }}>{count}</span>
          </button>
        );
      })}
    </div>
  );
}
