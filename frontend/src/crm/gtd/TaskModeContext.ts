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
