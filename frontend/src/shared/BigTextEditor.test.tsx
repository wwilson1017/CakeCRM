// @vitest-environment jsdom
//
// #305: the larger description window hands the edited text back on Save and on the
// Cmd/Ctrl+Enter chord, and discards it on Cancel and Escape.
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { BigTextEditor } from './BigTextEditor';
import { overflows } from './overflows';

let container: HTMLDivElement;
let root: Root;
beforeEach(() => { container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container); });
afterEach(() => { act(() => root.unmount()); container.remove(); });

function render(onSave = vi.fn(), onCancel = vi.fn()) {
  act(() => { root.render(<BigTextEditor title="Description" value="first line" onSave={onSave} onCancel={onCancel} />); });
  return { onSave, onCancel };
}
const field = () => document.body.querySelector('[role="dialog"] textarea') as HTMLTextAreaElement;
const button = (label: string) =>
  [...document.body.querySelectorAll('[role="dialog"] button')].find(b => b.textContent?.trim() === label) as HTMLButtonElement;
function type(text: string) {
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(field(), text);
    field().dispatchEvent(new Event('input', { bubbles: true }));
  });
}

describe('overflows', () => {
  it('is true only when the box holds more than it shows', () => {
    expect(overflows({ scrollHeight: 120, clientHeight: 48 })).toBe(true);
    expect(overflows({ scrollHeight: 48, clientHeight: 48 })).toBe(false);
  });
});

describe('BigTextEditor', () => {
  it('opens with the value, focused, and saves the edited text', () => {
    const { onSave } = render();
    expect(field().value).toBe('first line');
    expect(document.activeElement).toBe(field());
    type('first line\nand a second');
    act(() => { button('Save').click(); });
    expect(onSave).toHaveBeenCalledWith('first line\nand a second');
  });

  it('saves on Cmd/Ctrl+Enter and not on a plain Enter', () => {
    const { onSave } = render();
    type('longer');
    act(() => { field().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })); });
    expect(onSave).not.toHaveBeenCalled();
    act(() => { field().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true, bubbles: true, cancelable: true })); });
    expect(onSave).toHaveBeenCalledWith('longer');
  });

  it('keeps Tab inside the dialog and ignores a click that merely ends on the backdrop', () => {
    const { onCancel } = render();
    const buttons = [...document.body.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')];
    const save = buttons[buttons.length - 1];
    // Shift+Tab from the textarea (first) wraps to Save (last); Tab from Save wraps to the textarea.
    act(() => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true })); });
    expect(document.activeElement).toBe(save);
    act(() => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })); });
    expect(document.activeElement).toBe(field());
    const backdrop = document.body.querySelector('[role="dialog"]')!.parentElement as HTMLElement;
    const press = (el: Element) => el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    // A click whose target is the dialog box (a drag released inside) is not a backdrop click.
    act(() => { press(field()); (document.body.querySelector('[role="dialog"]') as HTMLElement).click(); });
    expect(onCancel).not.toHaveBeenCalled();
    // A drag that starts in the text and is released on the backdrop: the browser targets the
    // click at the backdrop (the common ancestor), and it must not discard the draft.
    act(() => { press(field()); backdrop.click(); });
    expect(onCancel).not.toHaveBeenCalled();
    // A press and release on the backdrop itself is a backdrop click.
    act(() => { press(backdrop); backdrop.click(); });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('cancels on the button and on Escape without saving', () => {
    const { onSave, onCancel } = render();
    type('thrown away');
    act(() => { button('Cancel').click(); });
    act(() => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })); });
    expect(onCancel).toHaveBeenCalledTimes(2);
    expect(onSave).not.toHaveBeenCalled();
  });
});
