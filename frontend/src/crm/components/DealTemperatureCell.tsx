/**
 * DealTemperatureCell — the temperature control as the List view can reach it (issue #125).
 *
 * WHY A CONTEXT FOR ONE CALLBACK, which is otherwise exactly the abstraction this repo's
 * conventions tell you not to add.
 *
 * The board card takes its writer as a plain prop, because `renderCard` is ordinary JSX. The
 * List cannot: its columns are built inside `PipelinePage`'s `listColumns` `useMemo`, and that
 * memo has to stay stable — `config` identity keys every memo in the collection layer, so
 * rebuilding it per render re-runs them on every keystroke (`dealDetailConfig.ts` states the
 * same rule for the same reason). Threading the writer through `buildPipelineListColumns`
 * therefore means REFERENCING it from inside a memo body, and this repo's `react-hooks` v7
 * ruleset rejects that outright — "Cannot access refs during render" — because `writeDeal`
 * reads five refs. Measured, not assumed: passing the callback, wrapping it in a latest-ref
 * indirection, and `useEffectEvent` were each tried and each rejected (the last with "cannot
 * be assigned to a variable or passed down").
 *
 * A context is the primitive for the shape that leaves: a cell rendered deep inside a memoized
 * tree needs one page-level action. The read happens in the CELL's render, where a ref-reading
 * function is legal to hold, and the memo never sees it.
 *
 * Absent provider ⇒ `onCycle` is undefined ⇒ `DealTemperatureIcon` renders read-only, with no
 * button and no tab stop. That is the honest default for any future host that lists deals
 * without a writer, and it means this file cannot make a surface lie about being editable.
 */

import { createContext, use } from 'react';
import type { CrmDeal } from '../../core/types';
import type { DealTemperature } from '../dealTemperature';
import DealTemperatureIcon from './DealTemperatureIcon';

export type CycleDealTemperature = (
  deal: CrmDeal,
  next: DealTemperature | null,
) => void | Promise<unknown>;

const CycleContext = createContext<CycleDealTemperature | null>(null);

/** Wrap the surface that renders deal rows. The value is an ordinary callback prop here — it is
 *  only a `useMemo` body that may not hold one. */
export const DealTemperatureWriter = CycleContext.Provider;

export function DealTemperatureCell({ deal, disabled }: { deal: CrmDeal; disabled?: boolean }) {
  const cycle = use(CycleContext);
  return (
    <DealTemperatureIcon
      value={deal.deal_temperature}
      disabled={disabled}
      onCycle={cycle ? next => cycle(deal, next) : undefined}
    />
  );
}
