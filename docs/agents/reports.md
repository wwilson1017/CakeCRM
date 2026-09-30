# Reports: the company rollup

> Topic doc split out of `AGENTS.md` (former Product Rules). `AGENTS.md` keeps the enforceable
> invariants; this file is the full implementation record, moved verbatim. Add new
> implementation notes ("landed #N as …", design reasoning, divergences) HERE, not in
> `AGENTS.md`. Phrases like "the CRM bullet above" or "see the X bullet" refer to the
> rule bullets this file opens with, or to the topic doc that holds that rule; find
> other areas' docs through the Topic docs table in `AGENTS.md`.

- **Reports is a top-level surface with one report: the company rollup** (#144,
  `backend/crm/report_service.py` + `frontend/src/crm/{ReportsPage.tsx,companyRollup.ts,
  components/{CompanyRollupReport,CompanyTimeline}.tsx}`). Pick one company and get
  everything the CRM knows about it on one scroll — header, exact chips, every deal and
  every contact expandable in place, then a merged notes-and-activity feed. Keyless and
  read-only; nothing on the page writes.
  **The timeline is this repo's first two-source merge, and its ORDER BY has three terms for
  a reason worth stating once.** Notes live in `crm_chatter` and events in `activity_log` —
  two tables with INDEPENDENT `SERIAL` sequences — so note #7 and activity #7 both exist and
  `ORDER BY created_at DESC, id DESC` over the `UNION ALL` is **not** a total order: under
  LIMIT/OFFSET that tie puts one row on two pages and drops another. #58's scanner cannot
  catch it, because it judges the FINAL term's NAME and `id` is in its allowlist — the same
  blind spot its own docstring records for a join that leaves a parent `id` non-unique. So
  the reader projects a `source` discriminator and orders
  `created_at DESC, source DESC, id DESC`: `(source, id)` is unique by construction, and
  `id` stays last so the static sweep still passes on merit rather than by exemption.
  `test_timeline_order_is_total_across_both_sources` pins the term list exactly, and fails
  deterministically if `source` is dropped. **Any future UNION reader owes the same
  treatment** — the scanner will wave it through.
  Paging is LIMIT/OFFSET and its contract is narrow **on purpose**: deterministic while the
  matching set is unchanged, not immune to concurrent writes (a row archived behind the
  cursor shifts the rest up and is skipped; the client's `(source, id)` dedupe hides
  duplicates but cannot recover a skip). Keyset paging on the same triple is the stated
  upgrade path.
  **Archived is opt-in, and that makes `report_service` the THIRD sanctioned hole in the
  `LIVE_PREDICATE` sweep**, in the same shape as `search_deals(include_archived=)` and #83's
  `get_pipeline(include_archived=)`. `include_archived` widens exactly two things — archived
  deals and archived NOTES — and deliberately does not govern contacts: `contacts.status` is
  not a sweep, so contacts are always returned and rendered marked, matching
  `get_company_detail`'s documented asymmetry. `summary.contact_count` is narrower still —
  `status = 'active'` only, so BOTH `inactive` and `archived` are out — because the chip it
  feeds says "Active contacts" and that word has to be true; the section below lists every
  contact and states its own total. Two questions, two honest numbers.
  **Activity attribution is mutually exclusive, and the near-miss is worth recording**: the
  contact bucket tests an absolute `deal_id IS NULL`, NOT membership of the deals being
  displayed. Those look equivalent and are not — an archived deal (or one past the child
  cap) is absent from the displayed set, so its activities silently reappeared under the
  contact with archived history switched OFF. The first draft shipped that; the Codex plan
  review caught it. Because the rule is absolute, a deal wins GLOBALLY rather than only among
  the deals on screen: an activity naming this company's contact and ANOTHER company's deal
  belongs to that deal and appears once, on the OTHER company's rollup and timeline. That is
  better than the blueprint, whose per-company predicates dropped such a row from both — here
  every activity has exactly one home. (The code documented the blueprint's behaviour while
  implementing this one; the Codex verify turn caught the contradiction.)
  **The open-value chip declines to lie about currency.** `deals.currency` is in
  `_DEAL_USER_WRITABLE`, so USD-only is a convention here and NOT an enforced invariant, and a
  bare `SUM(value)` across currencies is simply a false number. `summary.open_deal_currency`
  is the single currency every open deal agrees on, or NULL when they disagree, and the chip
  renders "Mixed currencies" instead of a total in that case; per-deal values render in their
  own currency through `Intl`, falling back to the raw code because the column is free text.
  The rest of the app still sums and prefixes `$` (`get_company_detail` documents that as a
  single-currency sum), which is consistency rather than correctness — this report is the
  first surface to decline it.
  **The headline chips are their own aggregate over the full tables**, never a reduction of
  the capped child lists — reducing the lists lets a cap change a headline number, and with
  archived deals competing for the same window, enabling MORE history could make the
  open-deal count go DOWN. **Custom fields and open todos ride the payload**, read once per
  entity type (`field_service.list_field_definitions` + `get_field_values_batch`) rather than
  once per row: that is what satisfies the issue's load-bearing "every field, including the
  unset ones" — a values-only read cannot express an unset field — and what keeps one
  "Expand all" from becoming ~150 requests. Caps are 200 children / 25 activities and todos
  per record, each with a `+1` probe and an explicit truncation flag, because a child now
  carries its own activities, fields and todos, so the cap bounds a payload rather than a row
  count.
  Three components derive their state from ONE object tagged with the request it belongs to,
  rather than resetting several `useState`s at the top of an effect. That is not only what
  the stricter `react-hooks` ruleset demands (`set-state-in-effect`, fixed rather than
  suppressed): reset-then-fetch is two steps, so a response from the previous archive filter
  could land between them. Row expansion is local to the report and the report is mounted
  `key={companyId}`, so switching account collapses the previous one's rows while toggling
  the filter does not. **`RecordCombobox`'s `create` became optional** here — a report must
  never create a company — gated on the single `canCreate` derivation every other create path
  already reads.
  A **Reports nav entry lives in TWO places**: `CrmLayout.NAV_ITEMS` (rendered twice from one
  list) and `shared/MobileMenuDrawer`'s own separate `items` list. Adding a nav destination
  means editing both.
