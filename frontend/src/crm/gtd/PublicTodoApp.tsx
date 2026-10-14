import { useEffect } from 'react';
import { BrowserRouter, Navigate, Outlet, Route, Routes } from 'react-router-dom';
import { ApiError } from '../../core/api/client';
import { ToastViewport } from '../../shared/ToastViewport';
import { getSession } from './api';
import {
  DonePage, InboxPage, NextActionsPage, ProjectDetailPage, ProjectsPage,
  ReviewPage, SearchPage, SomedayPage, TodayPage, WaitingPage,
} from './pages';
import { resetSession, setSession, useSession } from './session';
import { SignedOutPage } from './SignedOutPage';
import { SignInPage } from './SignInPage';
import { LoadFailed, LoadingRows } from './TodoShell';

/**
 * The no-login todo app.
 *
 * Mounted INSTEAD of the CRM when the backend served this page at /todo — there is no
 * login, no CRM nav and no route into the rest of the app. Since #293 it lives at ONE
 * fixed address for everyone; which session it is comes from the browser's HttpOnly
 * cookie. `SessionGate` asks the server once per page load before any page mounts, and
 * shows the signed-out page (never the CRM login) when there is no live session.
 * `/signin/:token` sits OUTSIDE the gate: it is how a signed-out device gets in.
 *
 * These are the same page components the CRM renders; only the API base and the
 * routing differ, which is why there is no second copy of any page. They come through
 * `./pages` — the same module `App.tsx` lazy-loads — so both mounts share ONE GTD chunk.
 *
 * Since #149 the dispatch that mounts this is `Root.tsx`, above App, with both branches
 * lazy: a /todo visitor downloads this graph and never a CRM chunk. Root.tsx is the file
 * to read before changing anything about how this mounts; `bootSplit.test.ts` pins what
 * this graph may reach.
 */
export function PublicTodoApp() {
  return (
    <BrowserRouter basename="/todo">
      <Routes>
        <Route path="signin/:token" element={<SignInPage />} />
        <Route element={<SessionGate />}>
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
        </Route>
      </Routes>
      <ToastViewport />
    </BrowserRouter>
  );
}

function bootstrap() {
  getSession().then(
    () => setSession('ok'),
    e => setSession(e instanceof ApiError && e.status === 401 ? 'signedOut' : 'error'),
  );
}

function SessionGate() {
  const session = useSession();
  useEffect(() => {
    if (session === 'loading') bootstrap();
  }, [session]);
  if (session === 'ok') return <Outlet />;
  if (session === 'signedOut') return <SignedOutPage />;
  if (session === 'error') {
    return (
      <div className="mx-auto max-w-3xl px-4 pt-6">
        <LoadFailed retry={resetSession} />
      </div>
    );
  }
  return (
    <div className="mx-auto max-w-3xl px-4 pt-6">
      <LoadingRows />
    </div>
  );
}
