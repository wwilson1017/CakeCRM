// @vitest-environment jsdom
//
// #233: the backend-rendered `/capture` page (`backend/crm/todo_capture.py`) retries focus on
// open and re-attempts on resume, but only for a standalone (home-screen) launch. This runs the
// page's REAL inline script in jsdom — the source arrives through the `__CAPTURE_PAGE_PY__`
// define in vitest.config.ts — so the rules are pinned by behaviour, not by source text.
//
// Every retry is asserted from a BLURRED start: jsdom has no autofocus-on-parse for markup set
// via innerHTML, and the script's own first attempt focuses immediately, so asserting "focused
// after load" alone would pass with the whole ladder deleted.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

declare const __CAPTURE_PAGE_PY__: string;

function captureScript(): string {
  const src = typeof __CAPTURE_PAGE_PY__ === 'string' ? __CAPTURE_PAGE_PY__ : '';
  const html = src.slice(src.indexOf('_CAPTURE_HTML = """'));
  const start = html.indexOf('<script>');
  const end = html.indexOf('</script>');
  if (start < 0 || end < start) throw new Error('capture page script not found');
  return html.slice(start + '<script>'.length, end).replace('__POST_PATH__', '/api/capture');
}

const listeners: Array<[EventTarget, string, EventListenerOrEventListenerObject]> = [];

function loadPage(): { t: HTMLTextAreaElement; b: HTMLButtonElement } {
  document.body.innerHTML =
    '<main><textarea id="t" autofocus></textarea><button id="b">Send</button><div id="msg"></div></main>';
  // Record what the script subscribes so afterEach can undo it — window/document outlive a test.
  for (const target of [window, document] as EventTarget[]) {
    const add = target.addEventListener.bind(target);
    vi.spyOn(target, 'addEventListener').mockImplementation((type, fn, opts) => {
      if (fn) listeners.push([target, type, fn]);
      add(type, fn, opts);
    });
  }
  new Function(captureScript())();
  return {
    t: document.getElementById('t') as HTMLTextAreaElement,
    b: document.getElementById('b') as HTMLButtonElement,
  };
}

/** Drain the load ladder: the rAF attempt plus both settle timeouts. */
function runLadder(): void {
  vi.advanceTimersByTime(500);
}

function setIosStandalone(value: boolean): void {
  Object.defineProperty(window.navigator, 'standalone', { value, configurable: true });
}

function stubMatchMedia(matching: string[]): void {
  window.matchMedia = ((query: string) =>
    ({ matches: matching.includes(query) })) as unknown as typeof window.matchMedia;
}

function stubVirtualKeyboard(): ReturnType<typeof vi.fn> {
  const show = vi.fn();
  Object.defineProperty(window.navigator, 'virtualKeyboard', { value: { show }, configurable: true });
  return show;
}

function resume(): void {
  window.dispatchEvent(new Event('pageshow'));
  vi.advanceTimersByTime(50);
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  listeners.splice(0).forEach(([target, type, fn]) => target.removeEventListener(type, fn));
  vi.restoreAllMocks();
  vi.useRealTimers();
  Reflect.deleteProperty(window.navigator, 'standalone');
  Reflect.deleteProperty(window.navigator, 'virtualKeyboard');
  Reflect.deleteProperty(window, 'matchMedia');
  document.body.innerHTML = '';
});

describe('capture page focus (#233)', () => {
  it('focuses the textarea on load', () => {
    const { t } = loadPage();
    expect(document.activeElement).toBe(t);
  });

  it('re-focuses after a blur, via the deferred ladder', () => {
    const { t } = loadPage();
    t.blur();
    expect(document.activeElement).not.toBe(t);
    runLadder();
    expect(document.activeElement).toBe(t);
  });

  it('re-focuses on a standalone resume, which is not a fresh load', () => {
    setIosStandalone(true);
    const { t } = loadPage();
    runLadder();
    t.blur();
    resume();
    expect(document.activeElement).toBe(t);
  });

  it('honours the standard display-mode signal too', () => {
    stubMatchMedia(['(display-mode: standalone)']);
    const { t } = loadPage();
    runLadder();
    t.blur();
    resume();
    expect(document.activeElement).toBe(t);
  });

  it('leaves a browser tab alone on resume', () => {
    const { t } = loadPage();
    runLadder();
    t.blur();
    resume();
    document.dispatchEvent(new Event('visibilitychange'));
    vi.advanceTimersByTime(50);
    expect(document.activeElement).not.toBe(t);
  });

  it('never takes focus from an element the user is already in', () => {
    setIosStandalone(true);
    const { b } = loadPage();
    b.focus();
    runLadder();
    expect(document.activeElement).toBe(b);
    resume();
    expect(document.activeElement).toBe(b);
  });

  it('raises the virtual keyboard on a touch device even when the textarea already has focus', () => {
    stubMatchMedia(['(pointer: coarse)']);
    const show = stubVirtualKeyboard();
    const { t } = loadPage();
    expect(document.activeElement).toBe(t);
    show.mockClear();
    runLadder();
    expect(show).toHaveBeenCalled();
  });

  it('does not touch the virtual keyboard without a coarse pointer', () => {
    const show = stubVirtualKeyboard();
    loadPage();
    runLadder();
    expect(show).not.toHaveBeenCalled();
  });

  it('swallows a refused virtualKeyboard.show()', () => {
    stubMatchMedia(['(pointer: coarse)']);
    const show = stubVirtualKeyboard();
    show.mockImplementation(() => {
      throw new Error('NotAllowedError');
    });
    expect(() => {
      loadPage();
      runLadder();
    }).not.toThrow();
  });
});
