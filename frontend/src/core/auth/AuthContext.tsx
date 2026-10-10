/**
 * CakeCRM — Account auth context with optional TOTP 2FA.
 *
 * Authenticates with an email + password and gets back a JWT (issue #60).
 * If 2FA is enabled for that account, login() returns a pending token that must be
 * verified via verify2fa() before access is granted.
 *
 * `currentUser` comes from GET /api/me, which the backend answers from the live
 * database row rather than from token claims — so a demotion or a deactivation is
 * reflected on the next request instead of at token expiry. That is also why `role`
 * is read from here and never decoded out of the JWT.
 *
 * JWT stored in sessionStorage (cleared on browser close).
 * BroadcastChannel syncs login/logout across tabs.
 */

import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  type ReactNode,
} from 'react';
import { TOKEN_KEY } from './tokenUtils';
// A dependency-free leaf (#281), so the CRM page graph stays out of the shell chunk.
import { wipeWarm } from '../../crm/warmStore';

const CHANNEL_NAME = 'cakecrm_auth';

export type LoginResult =
  | { success: true }
  | { requires2fa: true; pendingToken: string };

export interface CurrentUser {
  id: number;
  email: string;
  name: string;
  role: 'admin' | 'member';
  is_active: boolean;
}

interface AuthContextType {
  isLoggedIn: boolean;
  loading: boolean;
  /** The signed-in account, or null while logged out. */
  currentUser: CurrentUser | null;
  /** Convenience for gating admin-only UI. The server gates the routes regardless. */
  isAdmin: boolean;
  login: (email: string, password: string) => Promise<LoginResult>;
  verify2fa: (pendingToken: string, code: string, trustDevice?: boolean) => Promise<void>;
  /** Adopt a token minted outside the login flow (e.g. after a password change). */
  applyToken: (token: string) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isLoggedIn, setIsLoggedIn] = useState(false);
  const [loading, setLoading] = useState(true);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);

  // One round trip does both jobs — is this session still good, and who is it —
  // rather than a second /api/me call.
  //
  // The three outcomes are deliberately distinct. A 401 is the server saying this
  // token is dead, and only then may we throw it away. Anything else (503 while the
  // database is down, a network drop, a 502 mid-deploy) means "cannot tell right
  // now": the backend goes out of its way to answer 503 rather than 401 for exactly
  // this reason, and collapsing that back into "logged out" would sign the whole
  // team out during a blip and destroy sessions that were never invalid.
  const validateToken = useCallback(
    async (token: string): Promise<CurrentUser | 'invalid' | 'unknown'> => {
      try {
        const res = await fetch('/api/me', {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.status === 401) return 'invalid';
        if (!res.ok) return 'unknown';
        return (await res.json()) as CurrentUser;
      } catch {
        return 'unknown';
      }
    },
    [],
  );

  const _completeLogin = useCallback((token: string) => {
    sessionStorage.setItem(TOKEN_KEY, token);
    setIsLoggedIn(true);
    // Fetch the account behind the token we just stored. A failure leaves
    // currentUser null, which hides admin-only affordances — the safe direction,
    // and the server would refuse those routes anyway.
    validateToken(token).then(r => setCurrentUser(typeof r === 'string' ? null : r));
    try {
      const ch = new BroadcastChannel(CHANNEL_NAME);
      ch.postMessage({ type: 'login', token });
      ch.close();
    } catch { /* best-effort: BroadcastChannel unsupported — single-tab fallback */ }
  }, [validateToken]);

  useEffect(() => {
    async function init() {
      const token = sessionStorage.getItem(TOKEN_KEY);
      if (token) {
        const result = await validateToken(token);
        if (result === 'invalid') {
          // Definitively dead — this is the only case that discards the token.
          sessionStorage.removeItem(TOKEN_KEY);
          setIsLoggedIn(false);
        } else if (result === 'unknown') {
          // Backend unreachable or erroring. Keep the token so the session survives
          // the outage; the user lands on /login and one reload restores them once
          // the backend is healthy again.
          setIsLoggedIn(false);
        } else {
          setIsLoggedIn(true);
          setCurrentUser(result);
        }
      }
      setLoading(false);
    }
    init();

    // Cross-tab sync via BroadcastChannel
    let channel: BroadcastChannel | null = null;
    try {
      channel = new BroadcastChannel(CHANNEL_NAME);
      channel.onmessage = async (e: MessageEvent) => {
        if (e.data?.type === 'login' && e.data.token) {
          const user = await validateToken(e.data.token);
          if (typeof user === 'string') return;
          sessionStorage.setItem(TOKEN_KEY, e.data.token);
          setIsLoggedIn(true);
          setCurrentUser(user);
        } else if (e.data?.type === 'logout') {
          sessionStorage.removeItem(TOKEN_KEY);
          // The signing-out tab wiped the shared store already; this bumps THIS tab's epoch so a
          // sweep still running here cannot write its rows back afterwards.
          void wipeWarm();
          setIsLoggedIn(false);
          setCurrentUser(null);
        }
      };
    } catch {
      // BroadcastChannel not supported — single-tab fallback
    }

    return () => { channel?.close(); };
  }, [validateToken]);

  const login = useCallback(async (email: string, password: string): Promise<LoginResult> => {
    const res = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || 'Login failed');
    }
    const data = await res.json();

    if (data.requires_2fa) {
      return { requires2fa: true, pendingToken: data.pending_token };
    }

    _completeLogin(data.access_token);
    return { success: true };
  }, [_completeLogin]);

  const verify2fa = useCallback(async (pendingToken: string, code: string, trustDevice = false) => {
    const res = await fetch('/api/login/verify-2fa', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pending_token: pendingToken, code, trust_device: trustDevice }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || 'Verification failed');
    }
    const data = await res.json();
    _completeLogin(data.access_token);
  }, [_completeLogin]);

  const logout = useCallback(() => {
    sessionStorage.removeItem(TOKEN_KEY);
    // CRM rows cached in this browser must not outlive the session that fetched them (#281).
    void wipeWarm();
    setIsLoggedIn(false);
    setCurrentUser(null);

    // Notify other tabs
    try {
      const ch = new BroadcastChannel(CHANNEL_NAME);
      ch.postMessage({ type: 'logout' });
      ch.close();
    } catch { /* best-effort: BroadcastChannel unsupported — single-tab fallback */ }
  }, []);

  return (
    <AuthContext.Provider
      value={{
        isLoggedIn,
        loading,
        currentUser,
        isAdmin: currentUser?.role === 'admin',
        login,
        verify2fa,
        applyToken: _completeLogin,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextType {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
