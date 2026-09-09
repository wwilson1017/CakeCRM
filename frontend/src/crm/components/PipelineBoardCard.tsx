/**
 * PipelineBoardCard — the personal "show the closed stages" preference (#124).
 *
 * The pipeline board hides `won` and `lost` by default so the board is about live work. This is
 * the way back, and it is a PERSONAL setting rather than an install one: it configures the
 * person, so it sits in the member-visible Personal section and drives no server route at all —
 * there is nothing here that could 403, which is why it needs no `isAdmin` gate.
 *
 * Storage is `localStorage` via `pipelineBoard.{load,save}ShowClosedStages`, per device, on the
 * `cakecrm_theme` precedent — this repo has no per-user server preferences store and #124 is not
 * the issue that builds one. `saveShowClosedStages` also reconciles the current tab's hidden set,
 * which is what stops the toggle looking inert when someone walks board → Settings → board; see
 * its docstring for why that is load-bearing rather than tidy.
 *
 * State is seeded once from storage and thereafter owned here. No effect re-reads it: nothing
 * else in the app writes this key, and this card unmounts with the section.
 *
 * The second paragraph on screen is not decoration. Two things can leave a person turning this
 * on and seeing nothing change, and neither is discoverable from here: a per-tab hide made from
 * the board (which outranks this default by design) and a persisted stage FILTER on the board
 * (`visibleStageKeys` narrows by both). Clearing someone's filters from a Settings page would be
 * the worse answer, so the card says so instead.
 */

import { useState } from 'react';

import { FONT_SANS, INK_MUTE } from '../../shared/styles';
import { loadShowClosedStages, saveShowClosedStages } from '../pipelineBoard';
import { SettingsCard } from './SettingsCard';

export function PipelineBoardCard({ isMobile }: { isMobile: boolean }) {
  const [showClosed, setShowClosed] = useState(loadShowClosedStages);

  return (
    <SettingsCard
      id="pipeline_board"
      title="Pipeline board"
      description="The pipeline hides Won and Lost by default, so the board is about work that is still live. Turning them on brings back both the board columns and those deals in the list view. Saved in this browser."
      isMobile={isMobile}
    >
      <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: FONT_SANS, fontSize: 14 }}>
        <input
          type="checkbox"
          checked={showClosed}
          onChange={e => {
            setShowClosed(e.target.checked);
            saveShowClosedStages(e.target.checked);
          }}
        />
        Show Won and Lost stages
      </label>
      <p style={{ fontFamily: FONT_SANS, fontSize: 12, color: INK_MUTE, marginTop: 10, marginBottom: 0 }}>
        This is the default every new tab starts from. Hiding or showing a stage on the board
        itself still wins for that tab, and the board's own filters apply on top of both.
      </p>
    </SettingsCard>
  );
}
