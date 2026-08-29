// Composer key handling and geometry (#57). Pure — no DOM needed.
//
// The key mapping is the readable-composer contract: a note box is not chat, so Enter must
// insert a newline. Two of the four branches exist for bugs that are invisible on a US
// English keyboard — the IME commit and the Windows AltGr chord — so they are pinned
// individually rather than folded into one "modifier" case.
import { describe, expect, it } from 'vitest';
import {
  COMPOSER_MAX_HEIGHT_PX,
  COMPOSER_MIN_HEIGHT_PX,
  composerKeyAction,
  nextComposerHeight,
} from './chatterComposer';

describe('composerKeyAction', () => {
  it('does nothing for a plain Enter — that is a newline', () => {
    expect(composerKeyAction({ key: 'Enter' })).toBe('none');
  });

  it('does nothing for Shift+Enter either', () => {
    // With plain Enter already inserting a newline, Shift+Enter is the same keystroke and
    // nothing needs to read the modifier.
    expect(composerKeyAction({ key: 'Enter', metaKey: false, ctrlKey: false })).toBe('none');
  });

  it('submits on Cmd+Enter and Ctrl+Enter', () => {
    expect(composerKeyAction({ key: 'Enter', metaKey: true })).toBe('submit');
    expect(composerKeyAction({ key: 'Enter', ctrlKey: true })).toBe('submit');
  });

  it('ignores every other key', () => {
    expect(composerKeyAction({ key: 'a', metaKey: true })).toBe('none');
    expect(composerKeyAction({ key: 'Escape' })).toBe('none');
  });

  it('never submits mid-IME-composition', () => {
    // Japanese, Chinese, Korean and macOS accent entry all use Enter to COMMIT the
    // candidate text. Acting on that keypress truncates the word being typed.
    expect(composerKeyAction({ key: 'Enter', metaKey: true, isComposing: true })).toBe('none');
    expect(composerKeyAction({ key: 'Enter', ctrlKey: true, isComposing: true })).toBe('none');
  });

  it('does not treat Windows AltGr as the submit chord', () => {
    // Windows synthesizes AltGr as Ctrl+Alt, so a European-layout user still holding it
    // after typing a special character would otherwise post a half-written note.
    expect(composerKeyAction({ key: 'Enter', ctrlKey: true, altKey: true })).toBe('none');
    expect(composerKeyAction({ key: 'Enter', metaKey: true, altKey: true })).toBe('none');
  });
});

describe('nextComposerHeight', () => {
  it('never shrinks below the ~6-line default', () => {
    expect(nextComposerHeight(10)).toBe(COMPOSER_MIN_HEIGHT_PX);
    expect(nextComposerHeight(0)).toBe(COMPOSER_MIN_HEIGHT_PX);
  });

  it('grows with the content between the bounds', () => {
    const mid = (COMPOSER_MIN_HEIGHT_PX + COMPOSER_MAX_HEIGHT_PX) / 2;
    expect(nextComposerHeight(mid)).toBe(mid);
  });

  it('caps at the max so one long note cannot push the thread off screen', () => {
    expect(nextComposerHeight(99999)).toBe(COMPOSER_MAX_HEIGHT_PX);
  });

  it('falls back to the default for any non-finite measurement', () => {
    // Infinity is non-finite too, so it takes the same branch as NaN rather than clamping
    // to the max — a detached or hidden textarea should render at its default height, not
    // at its tallest.
    expect(nextComposerHeight(NaN)).toBe(COMPOSER_MIN_HEIGHT_PX);
    expect(nextComposerHeight(Infinity)).toBe(COMPOSER_MIN_HEIGHT_PX);
  });

  it('has a sane band', () => {
    expect(COMPOSER_MIN_HEIGHT_PX).toBeLessThan(COMPOSER_MAX_HEIGHT_PX);
  });
});
