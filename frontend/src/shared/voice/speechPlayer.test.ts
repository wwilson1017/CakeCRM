import { expect, it, vi } from 'vitest';
import { createSpeechPlayer } from './speechPlayer';
function harness() {
  const spoken: SpeechSynthesisUtterance[] = [];
  const cancel = vi.fn();
  const player = createSpeechPlayer({ cancel, speak: u => spoken.push(u), createUtterance: text => ({ text } as SpeechSynthesisUtterance) });
  const end = (i: number, error = false) => {
    const u = spoken[i];
    if (error) u.onerror?.call(u, {} as SpeechSynthesisErrorEvent);
    else u.onend?.call(u, {} as SpeechSynthesisEvent);
  };
  return { player, spoken, cancel, end };
}
it('plays in order and advances on errors', () => {
  const { player, spoken, end } = harness();
  player.play('a|1', ['first', 'second', 'third']);
  expect(spoken.map(u => u.text)).toEqual(['first']);
  expect(player.getState()).toEqual({ owner: 'a|1', active: true });
  end(0); end(1, true);
  expect(spoken.map(u => u.text)).toEqual(['first', 'second', 'third']);
  end(2);
  expect(player.getState().active).toBe(false);
});
it('changes generation before cancel can synchronously fire a stale callback', () => {
  const { player, spoken, cancel, end } = harness();
  player.play('a|1', ['old', 'dropped']);
  cancel.mockImplementation(() => end(0, true));
  player.play('b|2', ['new', 'next']);
  end(0);
  expect(spoken.map(u => u.text)).toEqual(['old', 'new']);
  player.cancel('other|');
  expect(player.getState().active).toBe(true);
  player.cancel('b|'); end(1);
  expect(player.getState()).toEqual({ owner: null, active: false });
  expect(spoken.map(u => u.text)).toEqual(['old', 'new']);
});
it('notifies subscribers, primes and drops stale events', () => {
  const { player, spoken, end } = harness();
  const listener = vi.fn(); const unsubscribe = player.subscribe(listener);
  player.prime(); expect(spoken[0].text).toBe('');
  player.play('a|1', ['first', 'dropped']); player.cancel(); end(1);
  expect(spoken.map(u => u.text)).toEqual(['', 'first']);
  expect(listener).toHaveBeenCalled(); unsubscribe();
});
