import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { ToastViewport } from '../../shared/ToastViewport';
import { DonePage } from './DonePage';
import { InboxPage } from './InboxPage';
import { NextActionsPage } from './NextActionsPage';
import { ProjectDetailPage } from './ProjectDetailPage';
import { ProjectsPage } from './ProjectsPage';
import { TODO_PUBLIC_BASE } from './publicMode';
import { ReviewPage } from './ReviewPage';
import { SearchPage } from './SearchPage';
import { SomedayPage } from './SomedayPage';
import { TodayPage } from './TodayPage';
import { WaitingPage } from './WaitingPage';

/**
 * The no-login todo app.
 *
 * Mounted INSTEAD of the CRM when the backend served this page at /todo[/{token}] —
 * there is no login, no CRM nav and no route into the rest of the app. The router
 * basename is whatever the backend injected, so every in-app link (built through
 * `todoPath`) stays inside the token'd prefix and the secret rides along.
 *
 * These are the same page components the CRM renders; only the API base and the
 * routing differ, which is why there is no second copy of any page.
 */
export function PublicTodoApp() {
  return (
    <BrowserRouter basename={TODO_PUBLIC_BASE ?? '/todo'}>
      <Routes>
        <Route index element={<TodayPage />} />
        <Route path="inbox" element={<InboxPage />} />
        <Route path="next" element={<NextActionsPage />} />
        <Route path="projects" element={<ProjectsPage />} />
        <Route path="projects/:id" element={<ProjectDetailPage />} />
        <Route path="waiting" element={<WaitingPage />} />
        <Route path="someday" element={<SomedayPage />} />
        <Route path="done" element={<DonePage />} />
        <Route path="review" element={<ReviewPage />} />
        <Route path="search" element={<SearchPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <ToastViewport />
    </BrowserRouter>
  );
}
