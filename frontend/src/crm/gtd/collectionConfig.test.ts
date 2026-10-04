// @vitest-environment jsdom
// jsdom only because the module graph reaches publicMode.ts, which reads `window` at load.
import { describe, expect, it } from 'vitest';
import {
  makeDoneConfig,
  makeSomedayConfig,
  projectsCollectionConfig,
  todoSearchText,
} from './collectionConfig';
import { contextOptions, matchesContexts } from './contextFacet';
import type { Todo, TodoProject } from './types';
import { matchesFilter } from './util';

const NO_ACTIONS = { onToggleDone: () => {}, onToggleStar: () => {}, onEdit: () => {} };
const SOMEDAY_ACTIONS = { ...NO_ACTIONS, onPromote: () => {} };

function todo(over: Partial<Todo> = {}): Todo {
  return {
    id: 1, title: 'Call the supplier', notes: 'about the spring order', project_id: null,
    project_name: 'Q4 sourcing', context: '@Calls', tags: ['urgent'], status: 'someday_maybe',
    star: false, due_date: '', repeat: '', auto_star_on_due: false, source: 'web',
    created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z', completed_at: null,
    contact_id: null, deal_id: null,
    ...over,
  };
}

/** The context facet a todo config declares, narrowed to its multi shape. */
function contextFacetOf(config: ReturnType<typeof makeDoneConfig>) {
  const facet = config.facets?.find(f => f.key === 'context');
  if (!facet || (facet.kind !== undefined && facet.kind !== 'multi')) throw new Error('no multi context facet');
  return facet;
}

describe('todoSearchText', () => {
  it('covers exactly the fields util.matchesFilter searches', () => {
    // The layer tokenizes and ANDs where matchesFilter did one substring test, but the FIELDS
    // must stay identical or a todo someone found by its project name or a tag goes missing.
    const t = todo();
    const searched = todoSearchText(t).filter(Boolean) as string[];
    expect(searched).toEqual([t.title, t.notes, t.context, t.project_name, ...t.tags]);
    for (const field of searched) expect(matchesFilter(t, field)).toBe(true);
  });

  it('tolerates a null project name and keeps every tag', () => {
    const searched = todoSearchText(todo({ project_name: null, tags: ['a', 'b', 'c'] }));
    expect(searched).toContain('Call the supplier');
    expect(searched).toEqual(expect.arrayContaining(['a', 'b', 'c']));
  });
});

describe('the context facet', () => {
  it('is ONE mapping — the collection configs build options from the bespoke pages\' function', () => {
    expect(contextFacetOf(makeDoneConfig(NO_ACTIONS, ['@Calls'])).options).toEqual(contextOptions(['@Calls']));
    expect(contextFacetOf(makeSomedayConfig(SOMEDAY_ACTIONS, ['@Calls'])).options).toEqual(contextOptions(['@Calls']));
  });

  it('lower-cases the getter to meet the lower-cased option values, as matchesContexts does', () => {
    // The layer matches facet values EXACTLY; contexts are free text typed in any case.
    const facet = contextFacetOf(makeDoneConfig(NO_ACTIONS, ['@Calls']));
    expect(facet.getValue(todo({ context: '@Calls' }))).toBe('@calls');
    expect(matchesContexts('@Calls', ['@calls'])).toBe(true);
  });

  it('offers ONE option per context regardless of case — two would share a React key', () => {
    // Contexts are free text, so the server can list `@home` and `@Home` side by side.
    expect(contextOptions(['@home', '@Home', '@Calls'])).toEqual([
      { value: '@home', label: '@home' },
      { value: '@calls', label: '@Calls' },
    ]);
  });

  it('maps a blank context to null, never to a nameless empty-string chip', () => {
    expect(contextFacetOf(makeSomedayConfig(SOMEDAY_ACTIONS, [])).getValue(todo({ context: '' }))).toBeNull();
  });
});

describe('surface configs', () => {
  const someday = makeSomedayConfig(SOMEDAY_ACTIONS, []);
  const done = makeDoneConfig(NO_ACTIONS, []);

  it('declares no sort, so the server order passes through untouched', () => {
    // Done is newest-finished-first from the server under a LIMIT; a sort would reorder it.
    expect(someday.sort).toBeUndefined();
    expect(done.sort).toBeUndefined();
    expect(projectsCollectionConfig.sort).toBeUndefined();
  });

  it('declares no detail block, so a click keeps its existing behavior', () => {
    // Rows open TodoEditSheet from inside TodoRow; a project card is a real <Link>.
    expect(someday.detail).toBeUndefined();
    expect(done.detail).toBeUndefined();
    expect(projectsCollectionConfig.detail).toBeUndefined();
  });

  it('gives each surface its own storage key, none shared with the normal-mode Todos page', () => {
    const keys = [someday.storage.key, done.storage.key, projectsCollectionConfig.storage.key, 'crm_todos'];
    expect(new Set(keys).size).toBe(keys.length);
  });

  it('keeps one Done key across the done|dropped tabs — the tab is a fetch key, not a facet', () => {
    expect(done.storage.key).toBe(makeDoneConfig(NO_ACTIONS, ['@x']).storage.key);
  });

  it('Projects searches name and notes', () => {
    const p: TodoProject = {
      id: 7, name: 'Rebuild the shed', notes: 'quote pending', purpose: '', outcome: '', status: 'active', open_count: 2,
      created_at: '', updated_at: '',
    };
    expect(projectsCollectionConfig.searchText(p)).toEqual(['Rebuild the shed', 'quote pending']);
  });
});
