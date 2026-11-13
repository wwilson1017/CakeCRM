// #260 / #322: who added a todo, for every source that is not a person in the app.
import { describe, expect, it } from 'vitest';

import { todoSourceLabel } from './sourceLabel';

describe('todoSourceLabel', () => {
  it('names a connected AI tool', () => {
    expect(todoSourceLabel('mcp')).toEqual({ text: 'AI tool', title: 'Added by a connected AI tool' });
  });

  it('labels nothing for a person\'s own todo or an unknown source', () => {
    expect(todoSourceLabel('ui')).toBeNull();
    expect(todoSourceLabel('something-new')).toBeNull();
  });
});
