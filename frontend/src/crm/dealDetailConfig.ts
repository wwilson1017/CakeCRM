/**
 * The `CollectionDetail` host config both deal-opening pages share.
 *
 * `PipelinePage` and `CrmDashboardPage` open deals into the SAME shell, so they answer the
 * layer's four questions identically — and a second copy of that answer is how the two drift
 * apart (a subtitle fixed on one page, a `loadById` route renamed on the other). It lives here
 * for the same reason `dealDeepLink.ts` and `boardNavOrder.ts` do: one small piece, two hosts.
 *
 * It is a `DetailHostConfig`, not a full `CollectionConfig`: neither page runs
 * `useCollectionState` — the board keeps issue #21's own filter bar and its own kanban, and the
 * dashboard renders three unrelated queries — so each supplies its own `navOrder` instead.
 *
 * `loadById` is the part that earns its keep. It is what makes a shared `?deal=` link resolve a
 * deal the host's array does not hold: `GET /api/crm/deals/:id` deliberately carries no live-deal
 * predicate, so an ARCHIVED deal resolves too and stays readable/restorable. The dashboard leans
 * on it constantly — only `top_deals` is ever in `items`, so its stale-deals and weekly-touches
 * rows all arrive through this fetch.
 *
 * Module scope, and it must stay there: every memo in the layer keys on config identity, so an
 * object literal rebuilt per render would re-run them on every keystroke.
 */
import { api } from '../core/api/client';
import type { CrmDeal } from '../core/types';
import type { DetailHostConfig } from '../shared/collection';

export const DEAL_DETAIL_CONFIG: DetailHostConfig<CrmDeal> = {
  getItemId: d => d.id,
  detail: {
    getTitle: d => d.title,
    // Tolerates a row missing EITHER name: the pipeline board joins both, but the dashboard's
    // `top_deals` query selects no `company_name` at all, so `filter(Boolean)` is what keeps a
    // subtitle from reading " · " — and `|| null` is what keeps an empty one from reserving a line.
    getSubtitle: d => [d.contact_name, d.company_name].filter(Boolean).join(' · ') || null,
    loadById: id => api<CrmDeal>(`/api/crm/deals/${id}`),
  },
};
