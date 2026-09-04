/**
 * CakeCRM — Protected route guard.
 * Redirects unauthenticated users to /login.
 */

import { Navigate } from 'react-router-dom';
import { useAuth } from './AuthContext';
import BootFallback from '../components/BootFallback';

export function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isLoggedIn, loading } = useAuth();

  // The same spinner a chunk load shows (#149), so an auth check and a code-split gap look
  // like one app rather than two loading states.
  if (loading) return <BootFallback />;

  if (!isLoggedIn) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}
