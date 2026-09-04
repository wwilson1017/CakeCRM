/**
 * The collection layer's card-grid view — the one net-new view (no kit
 * component renders a card grid). Chrome comes from config (`getTitle`/`getSubtitle`,
 * sections per `getSection` in first-appearance order — the same order `visibleOrder`
 * flattens for ‹ › nav) and two app slots (`renderThumb`, `renderBadge` — the sibling surface's
 * RequestCard + StageBadge patterns). Sections render at most `sectionCap` cards
 * until expanded (`expandSection`); voided rows follow the tri-state and render struck
 * through, never hidden.
 */
import { useMemo } from 'react';
import { voidedRowClass } from '../voidedRowClass';
import { CARDS_SECTION_CAP } from '../useCollectionState';
import EmptyState from '../EmptyState';
import { groupCardSections } from './grouping';
import type { CollectionCardsProps, CollectionConfig, CollectionState } from '../types';

export default function CardsView<T>({
  config,
  state,
  cards,
  selectedId,
  onSelect,
}: {
  config: CollectionConfig<T>;
  state: CollectionState<T>;
  cards?: CollectionCardsProps<T>;
  selectedId?: string | number | null;
  onSelect?: (id: string | number | null) => void;
}) {
  const cardsConfig = config.cards;
  const sections = useMemo(
    () => groupCardSections(state.visibleItems, cardsConfig?.getSection),
    [state.visibleItems, cardsConfig],
  );

  if (!cardsConfig) return null;
  const cap = cardsConfig.sectionCap ?? CARDS_SECTION_CAP;
  const getVoided = config.getVoided;

  if (state.visibleItems.length === 0) {
    return <EmptyState message={config.emptyState?.message ?? 'Nothing to show.'} />;
  }

  return (
    <div className="flex flex-col gap-5">
      {sections.map(({ section, items }) => {
        const sectionKey = section ?? '__all';
        const expanded = state.expandedSections.has(sectionKey);
        const shown = expanded ? items : items.slice(0, cap);
        const hidden = items.length - shown.length;
        return (
          <section key={sectionKey}>
            {section !== null && (
              <h3 className="mb-2 font-heading text-sm font-semibold text-charcoal">
                {section} <span className="font-normal text-muted">({items.length})</span>
              </h3>
            )}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {shown.map(item => {
                const id = config.getItemId(item);
                // Full-cell override: the app owns the entire cell, including selection
                // styling and click-to-open — its cell may have interactive children a nested
                // <button> cannot legally contain.
                if (cards?.renderCard) {
                  return <div key={id}>{cards.renderCard(item)}</div>;
                }
                const subtitle = cardsConfig.getSubtitle?.(item) ?? null;
                const cardClass = `flex flex-col overflow-hidden rounded-xl border bg-cream text-left ${
                  selectedId === id ? 'border-brand ring-1 ring-brand' : 'border-line'
                }`;
                const body = (
                  <>
                    {cards?.renderThumb && cards.renderThumb(item)}
                    <div className={`flex flex-1 flex-col gap-1 p-3 ${getVoided ? voidedRowClass(getVoided(item)) : ''}`}>
                      <div className="flex items-start justify-between gap-2">
                        {/* `min-w-0 break-words`: a flex item defaults to `min-width: auto`, so a
                            title containing one long unbreakable token (`record_attachments`,
                            a part number) refuses to shrink, pushes the `shrink-0` badge past the
                            card's content box, and the card's `overflow-hidden` then CLIPS the
                            badge. Measured on a sibling surface's card grid: 18 of 208 cards at the 4-column
                            width. The subtitle below takes `break-words` for the same reason —
                            it is the other text node that can carry an id, a path or a part
                            number with no space to wrap at. */}
                        <span className="min-w-0 break-words font-heading text-sm font-semibold text-charcoal">
                          {cardsConfig.getTitle(item)}
                        </span>
                        {cards?.renderBadge && <span className="shrink-0">{cards.renderBadge(item)}</span>}
                      </div>
                      {subtitle && <span className="break-words text-xs text-muted">{subtitle}</span>}
                    </div>
                  </>
                );
                // Interactivity follows `onSelect`, the rule the list view adopted in #148.
                // Rendering the `<button>` unconditionally gave a page with nothing to open a
                // focusable card and a hover shadow that both do nothing — the same "affordance
                // that lies" the list-view seam was changed to remove, one file over. No shipped
                // surface hits it (all three CRM pages wire `onSelect`), so this keeps the two
                // views telling ONE story rather than fixing a live bug. `aria-current` goes with
                // the button: without a select handler there is no selection to be current in.
                if (!onSelect) {
                  return <div key={id} className={cardClass}>{body}</div>;
                }
                return (
                  <button
                    key={id}
                    type="button"
                    onClick={() => onSelect(id)}
                    aria-current={selectedId === id || undefined}
                    className={`${cardClass} transition-shadow hover:shadow-md`}
                  >
                    {body}
                  </button>
                );
              })}
            </div>
            {hidden > 0 && (
              <button
                type="button"
                onClick={() => state.expandSection(sectionKey)}
                className="mt-2 rounded-lg border border-dashed border-line px-3 py-1.5 text-xs text-muted hover:bg-sand hover:text-charcoal"
              >
                Show {hidden} more
              </button>
            )}
          </section>
        );
      })}
    </div>
  );
}
