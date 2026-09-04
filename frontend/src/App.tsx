import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './core/auth/AuthContext';
import { ProtectedRoute } from './core/auth/ProtectedRoute';
import { BrandingProvider } from './core/branding/BrandingContext';
import BootFallback from './core/components/BootFallback';
import { LoginPage } from './login/LoginPage';
import { TasksModeRouter } from './crm/gtd/TasksModeRouter';
import { ToastViewport } from './shared/ToastViewport';
import { ConfirmHost } from './shared/ConfirmHost';

// Every page below is code-split (#149). App.tsx used to import every page statically, so
// one 1 MB chunk had to download and execute before anything rendered — on every surface,
// including the no-login /todo PWA. The identifiers are unchanged, which is what keeps the
// route table below byte-identical to the eager version.
//
// ADDING A ROUTE: add a lazy() const here, never a static page import. The bundler follows
// a static import into THIS module's chunk — the shell every CRM visitor downloads, the
// login page included — so one of them silently re-bundles that page and its transitive
// deps for all of them. `src/bootSplit.test.ts` enforces this.
//
// What stays eager above is the shell every CRM surface needs anyway: the router, auth, the
// route guard, branding, the toast/confirm hosts, `TasksModeRouter` (tiny glue — its own
// heavy import is lazy inside it), and `LoginPage`: it is the first screen of every
// signed-out visit and three small modules, so a Suspense hop there would cost every first
// visit a round trip to save the logged-in visitor ~5 kB.
//
// The ten GTD pages come through ONE module, `crm/gtd/pages`, deliberately: the public todo
// app imports the same module, so the bundler emits a single shared GTD chunk rather than
// ten page chunks the phone PWA would have to request one by one.
const SetupPage = lazy(() => import('./setup/SetupPage').then((m) => ({ default: m.SetupPage })));
const CrmLayout = lazy(() => import('./crm/CrmLayout').then((m) => ({ default: m.CrmLayout })));
const CrmDashboardPage = lazy(() => import('./crm/CrmDashboardPage').then((m) => ({ default: m.CrmDashboardPage })));
const ContactsPage = lazy(() => import('./crm/ContactsPage').then((m) => ({ default: m.ContactsPage })));
const CompaniesPage = lazy(() => import('./crm/CompaniesPage').then((m) => ({ default: m.CompaniesPage })));
const PipelinePage = lazy(() => import('./crm/PipelinePage').then((m) => ({ default: m.PipelinePage })));
const RemindersPage = lazy(() => import('./crm/RemindersPage').then((m) => ({ default: m.RemindersPage })));
const SettingsPage = lazy(() => import('./crm/SettingsPage').then((m) => ({ default: m.SettingsPage })));
const MemoryPage = lazy(() => import('./crm/MemoryPage').then((m) => ({ default: m.MemoryPage })));
const DonePage = lazy(() => import('./crm/gtd/pages').then((m) => ({ default: m.DonePage })));
const InboxPage = lazy(() => import('./crm/gtd/pages').then((m) => ({ default: m.InboxPage })));
const NextActionsPage = lazy(() => import('./crm/gtd/pages').then((m) => ({ default: m.NextActionsPage })));
const ProjectDetailPage = lazy(() => import('./crm/gtd/pages').then((m) => ({ default: m.ProjectDetailPage })));
const ProjectsPage = lazy(() => import('./crm/gtd/pages').then((m) => ({ default: m.ProjectsPage })));
const ReviewPage = lazy(() => import('./crm/gtd/pages').then((m) => ({ default: m.ReviewPage })));
const SearchPage = lazy(() => import('./crm/gtd/pages').then((m) => ({ default: m.SearchPage })));
const SomedayPage = lazy(() => import('./crm/gtd/pages').then((m) => ({ default: m.SomedayPage })));
const TodayPage = lazy(() => import('./crm/gtd/pages').then((m) => ({ default: m.TodayPage })));
const WaitingPage = lazy(() => import('./crm/gtd/pages').then((m) => ({ default: m.WaitingPage })));

export default function App() {
  // The no-login todo surface used to be dispatched from here, by an early return above
  // this router. It now lives in `Root.tsx`, which decides BEFORE this module is fetched —
  // read the comment there before touching either side. That is what lets a /todo visitor
  // skip the CRM's chunks, and it keeps the nested-<Router> failure unreachable.
  return (
    <AuthProvider>
      <BrowserRouter>
        {/* One boundary for the whole table (#149): every route element below is lazy, so each
            first visit to a page fetches its chunk.

            WHEN THIS FALLBACK IS ACTUALLY SEEN, stated precisely because the obvious reading is
            wrong: React Router 7 wraps every navigation in `startTransition` (BrowserRouter's
            `useTransitions` defaults to true), and React does not re-show an already-revealed
            Suspense fallback during a transition. So an in-app click to an unvisited route keeps
            the OLD screen up and shows NOTHING while the chunk downloads — no spinner, and the
            NavLink active state does not move either, because it reads the deferred location.
            This fallback is therefore seen on a cold load, not on navigation.
            That silent wait is the accepted cost of not flashing a spinner on every first visit
            to a page; the real answer is a navigation progress indicator, which is a design
            change rather than a rider on a bundling one. `useTransitions={false}` would make the
            fallbacks render on navigation instead — it is a real prop and it does work — but it
            buys feedback on slow connections at the price of a flicker on fast ones.

            The toast and confirm hosts sit OUTSIDE this boundary deliberately — a toast in
            flight or an open confirm dialog must not be replaced by a loading state while a
            chunk downloads. */}
        <Suspense fallback={<BootFallback />}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route
              path="/setup"
              element={
                <ProtectedRoute>
                  <SetupPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/crm"
              element={
                <ProtectedRoute>
                  <BrandingProvider>
                    <CrmLayout />
                  </BrandingProvider>
                </ProtectedRoute>
              }
            >
              <Route index element={<CrmDashboardPage />} />
              {/* One route per entity, list and detail both (#77). The list page renders the
                  detail page when :id is present, so the route element never changes and its
                  swept corpus survives open → back without re-fetching. Every existing
                  /crm/contacts/42 link still resolves. */}
              <Route path="contacts/:id?" element={<ContactsPage />} />
              <Route path="companies/:id?" element={<CompaniesPage />} />
              <Route path="pipeline" element={<PipelinePage />} />
              {/* One task route, two task systems — TasksModeRouter picks by mode.
                  The GTD-only sub-routes redirect to /crm/tasks in normal mode, so a
                  bookmarked GTD URL degrades to the Tasks page instead of 404ing. */}
              <Route path="tasks" element={<TasksModeRouter gtd={<TodayPage />} />} />
              <Route path="tasks/inbox" element={<TasksModeRouter gtd={<InboxPage />} normal="redirect" />} />
              <Route path="tasks/next" element={<TasksModeRouter gtd={<NextActionsPage />} normal="redirect" />} />
              <Route path="tasks/projects" element={<TasksModeRouter gtd={<ProjectsPage />} normal="redirect" />} />
              <Route path="tasks/projects/:id" element={<TasksModeRouter gtd={<ProjectDetailPage />} normal="redirect" />} />
              <Route path="tasks/waiting" element={<TasksModeRouter gtd={<WaitingPage />} normal="redirect" />} />
              <Route path="tasks/someday" element={<TasksModeRouter gtd={<SomedayPage />} normal="redirect" />} />
              <Route path="tasks/done" element={<TasksModeRouter gtd={<DonePage />} normal="redirect" />} />
              <Route path="tasks/review" element={<TasksModeRouter gtd={<ReviewPage />} normal="redirect" />} />
              <Route path="tasks/search" element={<TasksModeRouter gtd={<SearchPage />} normal="redirect" />} />
              <Route path="reminders" element={<RemindersPage />} />
              <Route path="memory" element={<MemoryPage />} />
              <Route path="settings" element={<SettingsPage />} />
            </Route>
            <Route path="*" element={<Navigate to="/crm" replace />} />
          </Routes>
        </Suspense>
        <ConfirmHost />
        <ToastViewport />
      </BrowserRouter>
    </AuthProvider>
  );
}
