import { createContext, useContext } from 'react';

export type TodoMode = 'normal' | 'gtd';

/**
 * The active todo mode, published by CrmLayout from the demo-status payload it
 * already fetches (so the mode costs no extra round trip).
 *
 * `null` means "not known yet". The router below waits rather than guessing: rendering
 * the normal Todos page and then swapping to GTD a moment later would flash the wrong
 * todo system on every load.
 *
 * That `null` is also what closes the switch-while-loading race a review raised: while
 * the demo-status GET is in flight the mode is null, TodoModeCard's buttons are disabled
 * on `mode === null`, and the layout fires that GET exactly once — so there is no window
 * in which a completed switch can be overwritten by a late response carrying the old
 * mode. Keep the disabled-until-known guard if either half is ever refactored.
 */
export const TodoModeContext = createContext<TodoMode | null>(null);

export function useTodoMode(): TodoMode | null {
  return useContext(TodoModeContext);
}

/**
 * Write side of the same state (#102). CrmLayout owns the mode for the whole CRM, and
 * Settings and Todos live under that one persistent layout — so the Settings card has
 * to push its change UP rather than keep a second copy. When it kept its own state, a
 * user who switched mode in Settings and navigated to Todos still got the OLD todo
 * system until a full page reload.
 *
 * That matters more since #102 made GTD the default: "switch back in Settings" is the
 * opt-out for every install the migration flipped, so it has to visibly work.
 *
 * It THROWS without a provider, matching `useActiveRecord` in RecordContext.tsx rather
 * than defaulting to a no-op. This is the read and write halves being separate
 * providers: the easy future mistake is keeping the value provider and dropping the
 * setter one (a refactor, a second mount point, a new route tree). Under a no-op
 * default that failure is silent and looks like success — the POST returns 200, the
 * toast says the mode switched, and /crm/todos keeps rendering the old todo system.
 * That is precisely the bug #102 exists to fix, restored with no error to notice.
 */
export const TodoModeSetterContext = createContext<((mode: TodoMode) => void) | null>(null);

export function useSetTodoMode(): (mode: TodoMode) => void {
  const setMode = useContext(TodoModeSetterContext);
  if (!setMode) {
    throw new Error('useSetTodoMode must be used within a TodoModeSetterContext.Provider');
  }
  return setMode;
}
