// The GTD pages as tabs — ONE list, read by TodoShell's top strip and by the phone
// bottom bar (#266), so the two can never name or route a list differently. Array order
// is the top strip's display order.
export type TodoTab =
  | 'today' | 'inbox' | 'next' | 'projects' | 'waiting'
  | 'someday' | 'done' | 'review' | 'search';

export interface TabDef { key: TodoTab; label: string; sub: string }

// `sub` is the path relative to the todo root ('' = Today); todoPath() resolves it
// against the active base (the CRM's /crm/todos vs. the public router basename), so
// the same tabs render in both modes.
export const TABS: TabDef[] = [
  { key: 'today', label: 'Today', sub: '' },
  { key: 'inbox', label: 'Inbox', sub: '/inbox' },
  { key: 'next', label: 'To Do', sub: '/next' },
  { key: 'projects', label: 'Projects', sub: '/projects' },
  { key: 'waiting', label: 'Waiting', sub: '/waiting' },
  { key: 'someday', label: 'Someday', sub: '/someday' },
  { key: 'done', label: 'Done', sub: '/done' },
  { key: 'review', label: 'Review', sub: '/review' },
  { key: 'search', label: 'Contexts', sub: '/search' },
];
