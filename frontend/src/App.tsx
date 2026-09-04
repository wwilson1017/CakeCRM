import { Suspense, lazy } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './core/auth/AuthContext';
import { ProtectedRoute } from './core/auth/ProtectedRoute';
import { LoginPage } from './login/LoginPage';
import { SetupPage } from './setup/SetupPage';
import { BrandingProvider } from './core/branding/BrandingContext';
import { CrmLayout } from './crm/CrmLayout';
import { CrmDashboardPage } from './crm/CrmDashboardPage';
import { ContactsPage } from './crm/ContactsPage';
import { CompaniesPage } from './crm/CompaniesPage';
import { PipelinePage } from './crm/PipelinePage';
import { RemindersPage } from './crm/RemindersPage';
import { SettingsPage } from './crm/SettingsPage';
import { MemoryPage } from './crm/MemoryPage';
import { DonePage } from './crm/gtd/DonePage';
import { InboxPage } from './crm/gtd/InboxPage';
import { NextActionsPage } from './crm/gtd/NextActionsPage';
import { ProjectDetailPage } from './crm/gtd/ProjectDetailPage';
import { ProjectsPage } from './crm/gtd/ProjectsPage';
import { PublicTodoApp } from './crm/gtd/PublicTodoApp';
import { ReviewPage } from './crm/gtd/ReviewPage';
import { SearchPage } from './crm/gtd/SearchPage';
import { SomedayPage } from './crm/gtd/SomedayPage';
import { TodayPage } from './crm/gtd/TodayPage';
import { WaitingPage } from './crm/gtd/WaitingPage';
import { isTodoPublicMode } from './crm/gtd/publicMode';
import { TasksModeRouter } from './crm/gtd/TasksModeRouter';
import { ToastViewport } from './shared/ToastViewport';
import { ConfirmHost } from './shared/ConfirmHost';

// Weekly Touches per-rep drill-down (#146), registered LAZILY on purpose.
//
// Every other page above is a static import today, but PR #149 converts this whole
// table to route-level `lazy()` chunks and adds `src/bootSplit.test.ts`, which fails
// on any static page import here. Registering this one the way #149 does — plus the
// local <Suspense> it needs, since this version of the file has no outer boundary
// yet — makes that merge a keep-both with no edit.
//
// It costs today's bundle nothing either: this build emits a single chunk regardless
// (rolldown's code splitting is not enabled here — that is exactly what #149 turns
// on), so the `lazy()` is merge compatibility now and a real chunk after that lands.
const WeeklyTouchesDetailPage = lazy(() =>
  import('./crm/WeeklyTouchesDetailPage').then((m) => ({ default: m.WeeklyTouchesDetailPage })),
);

export default function App() {
  // The no-login todo surface replaces the whole app: no auth provider, no CRM
  // router, no way into anything else. Decided from the basename the backend
  // injected, so this branch can only be taken on a page the backend actually served
  // at /todo — see crm/gtd/publicMode.ts.
  if (isTodoPublicMode) return <PublicTodoApp />;

  return (
    <AuthProvider>
      <BrowserRouter>
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
            {/* Reached from the dashboard's Weekly Touches card, so it is deliberately
                absent from CrmLayout's NAV_ITEMS — the same way /crm/memory is. */}
            <Route
              path="touches/:owner"
              element={
                <Suspense fallback={null}>
                  <WeeklyTouchesDetailPage />
                </Suspense>
              }
            />
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
        <ConfirmHost />
        <ToastViewport />
      </BrowserRouter>
    </AuthProvider>
  );
}
