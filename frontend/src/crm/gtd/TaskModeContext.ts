import { createContext, useContext } from 'react';

export type TaskMode = 'normal' | 'gtd';

/**
 * The active task mode, published by CrmLayout from the demo-status payload it
 * already fetches (so the mode costs no extra round trip).
 *
 * `null` means "not known yet". The router below waits rather than guessing: rendering
 * the normal Tasks page and then swapping to GTD a moment later would flash the wrong
 * task system on every load.
 *
 * That `null` is also what closes the switch-while-loading race a review raised: while
 * the demo-status GET is in flight the mode is null, TaskModeCard's buttons are disabled
 * on `mode === null`, and the layout fires that GET exactly once — so there is no window
 * in which a completed switch can be overwritten by a late response carrying the old
 * mode. Keep the disabled-until-known guard if either half is ever refactored.
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
