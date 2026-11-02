import { expect, it } from 'vitest';
import { CODE_LINE, TABLE_LINE, speakable } from './speakable';
it.each([
  ['**Hello** from [Example](https://x).', 'Hello from Example.'],
  ['an *italic*, a __bold__ and ~~gone~~ word', 'an italic, a bold and gone word.'],
  ['Call create_github_issue then snake_case_name', 'Call create_github_issue then snake_case_name.'],
  ['Run `create_github_issue` with `a*b*c`', 'Run create_github_issue with a*b*c.'],
  ['See https://example.com/foo.', 'See a link.'],
  ['(see https://x.com/Foo_(bar)).', '(see a link).'],
  ['Mail <https://a.b/c> now', 'Mail a link now.'],
  ['[https://a.com](https://a.com)', 'a link.'],
  ['Done 🎉 ✅ — great! ![chart](http://x/y.png)', 'Done — great!'],
  ['👍🏽', ''],
  ['Serve at 350°F © Acme™', 'Serve at 350°F © Acme™.'],
  ["# Plan\nHere's what I found:\n- one thing\n2. another\n> a quote\n> continues", "Plan. Here's what I found: one thing. another. a quote continues."],
  ["Here:\n```python\nprint('**not read**')\n```\n\n~~~\nmore\n~~~\nAfter.", `Here: ${CODE_LINE} After.`],
  ['Start\n```\ncode never closed\n| a | b |', `Start. ${CODE_LINE}`],
  ['| a | b |\n|---|---|\n| 1 | 2 |\n\nAfter', `${TABLE_LINE} After.`],
  ['Name | Qty\n---|:---:\nx | 1\ny | 2', TABLE_LINE],
  ['| a |\n|---|\n| 1 |\n\n| b |\n|---|\n| 2 |', TABLE_LINE],
  ['**Hello** from [Example](https://x). | a | b |\n|---|---|\n| 1 | 2 |', `Hello from Example. ${TABLE_LINE}`],
  ['<b>Bold</b> &amp; <br> done &lt;i&gt;x&lt;/i&gt;', 'Bold & done x.'],
  ['First line\n\n---\nSecond\n***\nThird', 'First line. Second. Third.'],
  ['one\ntwo   three\r\n\tfour', 'one two three four.'],
  ['hello \x000\x00 there `code`', 'hello 0 there code.'],
  ['&#65; &#x42; &quot;C&quot; &#39;D&#39; &nbsp;', 'A B "C" \'D\'.'],
  ...['', '   \n\n', '---', '🎉 ✅', '![x](y)'].map(s => [s, '']),
])('flattens %j', (md, expected) => { expect(speakable(md)).toBe(expected); });
it('collapses a huge table', () => {
  const md = '| Item | Qty |\n|---|---|\n' + Array.from({ length: 800 }, (_, i) => `| item ${i} | ${i * 3} |`).join('\n');
  expect(md.length).toBeGreaterThan(15000);
  expect(speakable(md)).toBe(TABLE_LINE);
});
it.each([
  ...['*a ', '**a ', '_a ', '__a ', '~~a ', '`a ', '![a ', '[a ', '[a]('].map(s => s.repeat(7000).slice(0, 20000)),
  'a' + '`'.repeat(20000), '<a'.repeat(10000), '<http://x'.repeat(2200),
])('bounds work on malformed markdown %#', text => {
  const start = performance.now(); speakable(text);
  expect(performance.now() - start).toBeLessThan(1000);
});
