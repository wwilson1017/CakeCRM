import { createContext, useContext } from 'react';

export type TaskMode = 'normal' | 'gtd';

/**
 * The active task mode, published by CrmLayout from the demo-status payload it
 * already fetches (so the mode costs no extra round trip).
 *
 * `null` means "not known yet". The router below waits rather than guessing: rendering
 * the normal Tasks page and then swapping to GTD a moment later would flash the wrong
 * task system on every load.
 */
export const TaskModeContext = createContext<TaskMode | null>(null);

export function useTaskMode(): TaskMode | null {
  return useContext(TaskModeContext);
}

/**
 * Write side of the same state (#102). CrmLayout owns the mode for the whole CRM, and
 * Settings and Tasks live under that one persistent layout — so the Settings card has
 * to push its change UP rather than keep a second copy. When it kept its own state, a
 * user who switched mode in Settings and navigated to Tasks still got the OLD task
 * system until a full page reload.
 *
 * That matters more since #102 made GTD the default: "switch back in Settings" is the
 * opt-out for every install the migration flipped, so it has to visibly work.
 *
 * A no-op default keeps the card renderable outside the layout (tests, storybook-style
 * isolation) instead of throwing.
 */
export const TaskModeSetterContext = createContext<(mode: TaskMode) => void>(() => {});

export function useSetTaskMode(): (mode: TaskMode) => void {
  return useContext(TaskModeSetterContext);
}
