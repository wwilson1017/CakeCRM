/**
 * Note composer geometry and key handling (issue #57) — pure, so it tests in Node.
 *
 * Ported from `cake_os/frontend/src/shared/chatter/composer.ts`, which is the readable-
 * composer half of the blueprint's #1526. The heights are re-derived for CakeCRM's own
 * type scale rather than copied: the blueprint's field is 14px on a 20px line-height,
 * while `shared/styles.inputStyle` here renders the composer at 13px.
 */

/**
 * Default height, ≈6 lines.
 *
 * 13px text at a ~1.5 line-height is ~19.5px per line, and the field carries 8px of
 * padding top and bottom plus a 1px border each side: 6 × 19.5 + 16 + 2 ≈ 135. The extra
 * few pixels keep a 7th line from peeking, which reads as a broken box.
 */
export const COMPOSER_MIN_HEIGHT_PX = 140;

/**
 * Grow to ≈14 lines, then scroll. The cap is what stops one long note from pushing the
 * rest of the thread off screen.
 */
export const COMPOSER_MAX_HEIGHT_PX = 300;

/** Height the textarea should take for a given `scrollHeight`, clamped to the band above. */
export function nextComposerHeight(scrollHeight: number): number {
  if (!Number.isFinite(scrollHeight)) return COMPOSER_MIN_HEIGHT_PX;
  return Math.min(Math.max(scrollHeight, COMPOSER_MIN_HEIGHT_PX), COMPOSER_MAX_HEIGHT_PX);
}

/**
 * What a keypress in the composer means.
 *
 * A note box is not chat, so Enter is a NEWLINE and the Post button submits. Enter-to-post
 * is a convention for short one-line chat messages; here people write paragraph-length
 * call summaries, so it would post the half-written note the moment someone started a
 * second paragraph. Cmd/Ctrl+Enter survives as the power-user chord — the standard
 * "submit a form from inside a textarea", which cannot collide with typing. `shiftKey` is
 * deliberately absent: with plain Enter already inserting a newline, Shift+Enter is the
 * same keystroke and nothing needs to read the modifier.
 *
 * `isComposing` is load-bearing: an IME (Japanese, Chinese, Korean, and macOS accent
 * entry) uses Enter to COMMIT the candidate text, so acting on that keypress truncates the
 * word the user was still typing. Callers must read it off the NATIVE event — React's
 * synthetic KeyboardEvent does not carry `isComposing`, so passing the synthetic event
 * makes this guard silently dead.
 */
export function composerKeyAction(event: {
  key: string;
  metaKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
  isComposing?: boolean;
}): 'submit' | 'none' {
  if (event.key !== 'Enter') return 'none';
  if (event.isComposing) return 'none';
  // Alt disqualifies the chord, because Windows synthesizes AltGr as Ctrl+Alt: someone on
  // a European layout still holding AltGr from typing a special character would post their
  // half-written note on the next Enter — precisely the bug this mapping exists to fix.
  // (X11 reports AltGr as its own AltGraph modifier with ctrlKey unset, so it never
  // reaches the chord check to begin with.)
  if (event.altKey) return 'none';
  if (event.metaKey || event.ctrlKey) return 'submit';
  return 'none';
}
