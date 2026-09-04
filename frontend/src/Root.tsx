import { lazy, Suspense } from 'react';
import BootFallback from './core/components/BootFallback';
import ChunkErrorBoundary from './core/components/ChunkErrorBoundary';
import { isTodoPublicMode } from './crm/gtd/publicMode';

// The no-login todo web app (#70) REPLACES the CRM tree rather than sitting in App's route
// table: it needs a router whose basename is /todo[/{token}] (that basename is what keeps
// todoPath() and <Navigate to="/"> inside the surface), and React Router throws
// "You cannot render a <Router> inside another <Router>" the moment a second one mounts.
// Only a full page load can reach /todo/... (no <Link> in the app points there), so this
// module-level check catches every real entry — decided from the basename the backend
// injected, so the branch can only be taken on a page the backend served at /todo.
//
// The dispatch lives HERE, above App, and both branches are lazy(): that is what splits the
// bundle (#149). A /todo visitor downloads the todo app and never a CRM chunk; a CRM visitor
// never downloads the todo shell. This module is reached eagerly from main.tsx, so anything
// it imports statically is ENTRY-chunk weight paid by every visitor on every surface — making
// either branch a static import would put that whole graph back in front of everyone and
// undo the split. `src/bootSplit.test.ts` fails if that happens.
const App = lazy(() => import('./App'));
const PublicTodoApp = lazy(() =>
  import('./crm/gtd/PublicTodoApp').then((m) => ({ default: m.PublicTodoApp })),
);

/**
 * The composition root — what `main.tsx` renders, and the whole of the boot decision.
 *
 * Kept as a component (rather than inlining the ternary into `main.tsx`) so it stays
 * testable: `main.tsx` calls `createRoot` at module scope and renders on import, which
 * leaves nothing for a test to drive. `crm/gtd/PublicTodoApp.test.tsx` boots this exact
 * component, so the test exercises the real production boot path.
 */
export default function Root() {
  return (
    <ChunkErrorBoundary>
      <Suspense fallback={<BootFallback />}>
        {isTodoPublicMode ? <PublicTodoApp /> : <App />}
      </Suspense>
    </ChunkErrorBoundary>
  );
}
