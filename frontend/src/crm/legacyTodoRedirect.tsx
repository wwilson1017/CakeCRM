import { Navigate, useLocation } from 'react-router-dom';

/**
 * #169 renamed /crm/tasks… to /crm/todos…. Bookmarks, Telegram deep links and browser
 * history still carry the old path, and App's `path="*"` catch-all would silently drop
 * them on the dashboard rather than on the page they asked for. Mounted once as
 * `<Route path="tasks/*">` under /crm, so every old sub-path — including the param route
 * projects/:id and any query string or hash — lands on its renamed twin. `replace`, so
 * Back does not bounce through the old URL.
 *
 * This is the ONE place the old word survives in the frontend, on purpose: a redirect has
 * to spell the path it is redirecting FROM. There is deliberately no REST or tool-name
 * compatibility shim beside it — the frontend is the only consumer of those.
 *
 * It reads the pathname off `useLocation` and rewrites the anchored prefix, rather than
 * rebuilding the path from `useParams()['*']`: the splat comes back percent-DECODED, so
 * reassembling from it corrupts any encoded segment (a literal %2F most obviously).
 */
export function LegacyTodoRedirect() {
  const { pathname, search, hash } = useLocation();
  return (
    <Navigate
      to={{ pathname: pathname.replace(/^\/crm\/tasks/, '/crm/todos'), search, hash }}
      replace
    />
  );
}
