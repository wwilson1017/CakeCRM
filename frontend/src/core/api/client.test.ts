import { describe, expect, it } from 'vitest';

import { attachmentName } from './client';

describe('attachmentName', () => {
  it("keeps the server's dated filename", () => {
    expect(attachmentName('attachment; filename="cakecrm-export-2026-10-14.zip"', 'x.zip'))
      .toBe('cakecrm-export-2026-10-14.zip');
  });

  it('falls back when the header is missing or unquoted', () => {
    expect(attachmentName(null, 'x.zip')).toBe('x.zip');
    expect(attachmentName('attachment', 'x.zip')).toBe('x.zip');
  });
});
