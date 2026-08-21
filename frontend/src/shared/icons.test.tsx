// @vitest-environment jsdom
//
// WHY THIS FILE EXISTS. `Ico` destructures the props it renders and does NOT spread the rest onto
// the `<svg>`, so an attribute it does not name explicitly is dropped. TypeScript cannot catch
// that for `aria-hidden`: hyphenated JSX attributes are exempt from prop type-checking, so
// `<IconX aria-hidden="true" />` compiles cleanly whether or not the icon forwards it. The
// decorative icons across `shared/search`, `shared/collection` and `shared/overlay` all pass it,
// and the failure mode is invisible — nothing throws, nothing fails to build, a screen reader just
// starts announcing graphics. Pinned here so the forwarding cannot be refactored away silently.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it } from 'vitest';

import { IconX } from './icons';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement | null = null;
let root: Root | null = null;

function render(ui: React.ReactNode) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => { root!.render(ui); });
  return container.querySelector('svg')!;
}

afterEach(() => {
  act(() => { root?.unmount(); });
  container?.remove();
  container = null;
  root = null;
});

describe('icon aria-hidden forwarding', () => {
  it('reaches the rendered svg when supplied', () => {
    expect(render(<IconX aria-hidden="true" />).getAttribute('aria-hidden')).toBe('true');
  });

  it('is absent when not supplied, so a meaningful icon stays exposed', () => {
    expect(render(<IconX />).hasAttribute('aria-hidden')).toBe(false);
  });
});
