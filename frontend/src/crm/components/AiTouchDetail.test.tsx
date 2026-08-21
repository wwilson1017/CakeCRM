// @vitest-environment jsdom
//
// What this pins is the drill-down's contract, not its markup: it stays invisible with no
// AI count (the zero-keys rule), it costs nothing until someone actually asks for it, it
// prefers the freshly-fetched count over the frozen list-row prop, and a failed load says
// so instead of spinning forever.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { AiTouchEvidenceResponse } from '../../core/types';

const api = vi.hoisted(() => vi.fn());
vi.mock('../../core/api/client', () => ({ api }));

const { AiTouchDetail } = await import('./AiTouchDetail');

function response(over: Partial<AiTouchEvidenceResponse> = {}): AiTouchEvidenceResponse {
  return {
    deal_id: 7,
    open: true,
    stage: 'qualified',
    ai_touch_count: 2,
    computed_at: '2026-08-19T10:00:00+00:00',
    verdict_state: 'current',
    counted: 2,
    evaluated: 3,
    truncated: false,
    events: [
      { source: 'note', source_id: 11, event_at: '2026-08-18T00:00:00+00:00',
        line: '2026-08-18 [note] Called the buyer', state: 'touch', reason: '' },
      { source: 'activity', source_id: 41, event_at: '2026-08-19T00:00:00+00:00',
        line: '2026-08-19 [activity:email] Bulk newsletter', state: 'not_touch',
        reason: 'Mass mail, never answered' },
      { source: 'stage_move', source_id: 2, event_at: '2026-08-19T12:00:00+00:00',
        line: '2026-08-19 [stage] lead → qualified', state: 'stage_move',
        reason: 'Stage moves are internal bookkeeping — never counted as touches.' },
    ],
    ...over,
  };
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  api.mockReset();
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render(node: React.ReactElement) {
  act(() => root.render(node));
}

async function clickToggle() {
  const button = container.querySelector('button[aria-expanded]') as HTMLButtonElement;
  await act(async () => { button.click(); });
}

describe('AiTouchDetail', () => {
  it('renders nothing at all when no count exists (the zero-keys rule)', () => {
    render(<AiTouchDetail dealId={7} count={null} />);
    expect(container.textContent).toBe('');
    render(<AiTouchDetail dealId={7} count={undefined} />);
    expect(container.textContent).toBe('');
    expect(api).not.toHaveBeenCalled();
  });

  it('shows the count collapsed and fetches nothing until asked', () => {
    render(<AiTouchDetail dealId={7} count={4} />);
    expect(container.textContent).toContain('4 touches');
    expect(container.querySelector('button[aria-expanded]')!.getAttribute('aria-expanded'))
      .toBe('false');
    expect(api).not.toHaveBeenCalled();
  });

  it('fetches once on expand and lists each event with its verdict and reason', async () => {
    api.mockResolvedValue(response());
    render(<AiTouchDetail dealId={7} count={2} />);
    await clickToggle();

    expect(api).toHaveBeenCalledTimes(1);
    expect(api).toHaveBeenCalledWith('/api/crm/deals/7/touch-count/evidence');
    const text = container.textContent!;
    expect(text).toContain('Called the buyer');
    expect(text).toContain('Touch');
    expect(text).toContain('Not a touch');
    expect(text).toContain('Mass mail, never answered');
    expect(text).toContain('lead → qualified');
    expect(text).toContain('2 of 3 events counted as touches');
  });

  it('does not re-request when collapsed and expanded again', async () => {
    api.mockResolvedValue(response());
    render(<AiTouchDetail dealId={7} count={2} />);
    await clickToggle();
    await clickToggle();
    await clickToggle();
    expect(api).toHaveBeenCalledTimes(1);
  });

  it('prefers the response count over the frozen list-row prop', async () => {
    // The prop came from a list snapshot that nothing refreshes after a recompute, so a
    // note logged since would otherwise show the old number here.
    api.mockResolvedValue(response({ ai_touch_count: 9, counted: 9 }));
    render(<AiTouchDetail dealId={7} count={2} />);
    await clickToggle();
    expect(container.textContent).toContain('9 touches');
    expect(container.textContent).not.toContain('2 touches');
  });

  it('re-requests on refresh', async () => {
    api.mockResolvedValue(response());
    render(<AiTouchDetail dealId={7} count={2} />);
    await clickToggle();
    const refresh = [...container.querySelectorAll('button')]
      .find(b => b.textContent === 'REFRESH') as HTMLButtonElement;
    await act(async () => { refresh.click(); });
    expect(api).toHaveBeenCalledTimes(2);
  });

  it('surfaces the honesty banner when the explanation no longer adds up', async () => {
    api.mockResolvedValue(response({ verdict_state: 'superseded' }));
    render(<AiTouchDetail dealId={7} count={2} />);
    await clickToggle();
    expect(container.textContent).toContain("don't fully explain it");
  });

  it('reports a failed load instead of spinning forever', async () => {
    api.mockRejectedValue(new Error('boom'));
    render(<AiTouchDetail dealId={7} count={2} />);
    await clickToggle();
    expect(container.querySelector('[role="alert"]')!.textContent)
      .toContain('Could not load the evidence');
    expect(container.textContent).not.toContain('Loading the evidence');
  });

  it('retries after a failure rather than latching the error', async () => {
    api.mockRejectedValueOnce(new Error('boom')).mockResolvedValue(response());
    render(<AiTouchDetail dealId={7} count={2} />);
    await clickToggle();          // fails
    await clickToggle();          // collapse
    await clickToggle();          // expand again → retried
    expect(api).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain('Called the buyer');
  });

  it('ignores a superseded response so the panel cannot hang', async () => {
    // The failure this guards: switch deal mid-flight, the OLD deal's slow reply lands
    // last and overwrites state. Because the render filters on id, `data` goes null while
    // the fetch latch still says "already fetched this deal" — a spinner forever.
    let resolveFirst: (v: AiTouchEvidenceResponse) => void = () => {};
    api.mockImplementationOnce(() => new Promise(res => { resolveFirst = res; }));
    api.mockResolvedValue(response({ deal_id: 8, ai_touch_count: 5, counted: 5 }));

    render(<AiTouchDetail dealId={7} count={2} />);
    await clickToggle();                                   // starts deal 7's request
    render(<AiTouchDetail dealId={8} count={5} />);         // switch before it resolves
    await act(async () => {});                             // let deal 8's request settle
    await act(async () => { resolveFirst(response({ deal_id: 7 })); });  // 7 lands late

    expect(container.textContent).toContain('5 touches');
    expect(container.textContent).not.toContain('Loading the evidence');
  });

  it('renders an unjudged event without dressing it as a ruling', async () => {
    api.mockResolvedValue(response({
      verdict_state: 'none',
      counted: null,
      evaluated: 0,
      computed_at: null,
      events: [{ source: 'note', source_id: 11, event_at: '2026-08-18T00:00:00+00:00',
                 line: '2026-08-18 [note] Called the buyer', state: 'not_evaluated',
                 reason: '' }],
    }));
    render(<AiTouchDetail dealId={7} count={2} />);
    await clickToggle();
    const text = container.textContent!;
    expect(text).toContain('Awaiting next AI pass');
    expect(text).toContain('No per-event explanations are stored for this count.');
    expect(text).toContain('No per-event explanations stored yet.');
  });
});
