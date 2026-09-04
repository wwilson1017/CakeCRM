// The ten GTD pages as ONE module (#149).
//
// Both mounts import the pages from here — `PublicTodoApp` statically, `App.tsx`'s lazy()
// route table dynamically — so the bundler emits a single shared GTD chunk. Ten separate
// dynamic imports would give the no-login phone PWA ten chunk requests on every cold load.
//
// Nothing but a GTD page may be re-exported from here: this module IS the public todo
// surface's download, so a CRM page added to it would ship the CRM to /todo visitors.
// `src/bootSplit.test.ts` pins the export list to exactly these ten.
export { DonePage } from './DonePage';
export { InboxPage } from './InboxPage';
export { NextActionsPage } from './NextActionsPage';
export { ProjectDetailPage } from './ProjectDetailPage';
export { ProjectsPage } from './ProjectsPage';
export { ReviewPage } from './ReviewPage';
export { SearchPage } from './SearchPage';
export { SomedayPage } from './SomedayPage';
export { TodayPage } from './TodayPage';
export { WaitingPage } from './WaitingPage';
