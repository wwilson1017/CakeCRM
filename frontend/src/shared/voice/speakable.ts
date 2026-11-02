/** Markdown to spoken prose, port of cake_os speech_text.py (#4019/#4020). */
export const CODE_LINE = "I've put the code on screen.";
export const TABLE_LINE = "I've put a table on screen.";
const FENCE = /^ {0,3}(`{3,}|~{3,})/;
const DELIMITER = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;
const RULE = /^ {0,3}([-*_=])(\s*\1){2,}\s*$/;
const HEADING = /^ {0,3}#{1,6}(?:\s+|$)/;
const QUOTE = /^\s*(?:>\s?)+/;
const BULLET = /^\s*(?:[-*+•]|\d{1,9}[.)])\s+(?:\[[ xX]\]\s+)?/;
const TAG = /<\/?[A-Za-z][^<>]*>/g;
const EMPHASIS = [
  /(?<![\w*])\*\*(?=\S)(.{1,200}?)(?<=\S)\*\*(?![\w*])/g,
  /(?<![\w_])__(?=\S)(.{1,200}?)(?<=\S)__(?![\w_])/g,
  /(?<![\w*])\*(?=\S)(.{1,200}?)(?<=\S)\*(?![\w*])/g,
  /(?<![\w_])_(?=\S)(.{1,200}?)(?<=\S)_(?![\w_])/g,
  /(?<![\w~])~~(?=\S)(.{1,200}?)(?<=\S)~~(?![\w~])/g,
];
const cells = (line: string) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|');
const delimiter = (line: string) => line.includes('|') && DELIMITER.test(line);
function blocks(md: string): string[] {
  const lines = md.replace(/\r\n?/g, '\n').split('\n');
  const out: string[] = [];
  const placeholder = (line: string) => {
    if ([...out].reverse().find(s => s.trim()) !== line) out.push(line);
  };
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const fence = FENCE.exec(line);
    if (fence) {
      const marker = fence[1];
      const close = new RegExp(`^ {0,3}${marker[0]}{${marker.length},}\\s*$`);
      i++;
      while (i < lines.length && !close.test(lines[i])) i++;
      placeholder(CODE_LINE); i++; continue;
    }
    const header = i + 1 < lines.length && line.includes('|') && delimiter(lines[i + 1]);
    if (header || (line.trim().startsWith('|') && (line.match(/\|/g)?.length ?? 0) >= 2) || delimiter(line)) {
      if (header) {
        const parts = cells(line), columns = cells(lines[i + 1]).length;
        if (!line.trim().startsWith('|') && parts.length > columns) out.push(parts.slice(0, parts.length - columns).join('|'));
        i += 2;
      } else i++;
      while (i < lines.length && lines[i].trim() && lines[i].includes('|')) i++;
      placeholder(TABLE_LINE); continue;
    }
    out.push(line); i++;
  }
  return out;
}
function isEmoji(ch: string): boolean {
  const cp = ch.codePointAt(0)!;
  return [0x200D, 0xFE0E, 0xFE0F, 0x20E3].includes(cp)
    || (cp >= 0x1F3FB && cp <= 0x1F3FF) || (cp >= 0xE0020 && cp <= 0xE007F)
    || (cp >= 0x2190 && cp <= 0x21FF) || (cp >= 0x2190 && /\p{So}/u.test(ch));
}
function unescape(text: string): string {
  const entities: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'", nbsp: ' ' };
  return text.replace(/&(#x[\da-f]+|#\d+|amp|lt|gt|quot|nbsp);/gi, (whole, entity: string) => {
    if (entities[entity.toLowerCase()]) return entities[entity.toLowerCase()];
    const cp = entity[1].toLowerCase() === 'x' ? parseInt(entity.slice(2), 16) : parseInt(entity.slice(1), 10);
    return cp > 0 && cp <= 0x10ffff && !(cp >= 0xd800 && cp <= 0xdfff) ? String.fromCodePoint(cp) : whole;
  });
}
function inline(text: string): string {
  text = text.replace(/<(?:https?:\/\/|mailto:)[^<>\s]+>/g, 'a link');
  text = unescape(text.replace(TAG, ' ')).replace(TAG, ' ');
  text = text.replace(/!\[[^\]]{0,300}\](?:\((?:[^()]|\([^()]{0,300}\)){0,500}\)|\[[^\]]{0,300}\])/g, '');
  text = text.replace(/\[([^\]]{0,300})\](?:\((?:[^()]|\([^()]{0,300}\)){0,500}\)|\[[^\]]{0,300}\])/g, '$1');
  const spans: string[] = [];
  text = text.replace(/(?<!`)(`{1,10})(?!`)(.{1,500}?)(?<!`)\1(?!`)/g, (_, _ticks: string, content: string) => {
    spans.push(content.trim()); return `\x00${spans.length - 1}\x00`;
  });
  text = text.replace(/\b(?:https?:\/\/|www\.)[^\s<>]+/g, url => {
    let end = url.length;
    while (end && '.,;:!?)]\'"'.includes(url[end - 1])) {
      const prefix = url.slice(0, end);
      if (url[end - 1] === ')' && (prefix.match(/\(/g)?.length ?? 0) >= (prefix.match(/\)/g)?.length ?? 0)) break;
      end--;
    }
    return 'a link' + url.slice(end);
  });
  for (const pattern of EMPHASIS) text = text.replace(pattern, '$1');
  text = text.replace(/[*`|]|~~/g, ' ').replace(/\\(?=[^\w\s])/g, '');
  // The input NULs were removed before inserting these alternating text/index parts.
  text = text.split('\x00').map((part, i) => i % 2 ? spans[Number(part)] : part).join('');
  text = [...text].filter(ch => !isEmoji(ch)).join('').replace(/\s+/g, ' ');
  return text.replace(/\(\s*\)/g, '').replace(/\s+([.,!?;:])/g, '$1').trim();
}
export function speakable(md: string): string {
  const items: string[] = [];
  let current: string[] = [];
  let inQuote = false;
  const flush = () => { if (current.length) items.push(current.join(' ')); current = []; };
  for (let line of blocks((md || '').replaceAll(String.fromCharCode(0), ''))) {
    if (line === CODE_LINE || line === TABLE_LINE) { flush(); items.push(line); inQuote = false; continue; }
    const quoted = QUOTE.test(line);
    if (quoted) { if (!inQuote) flush(); line = line.replace(QUOTE, ''); }
    inQuote = quoted;
    if (!line.trim() || RULE.test(line)) { flush(); continue; }
    if (HEADING.test(line)) { flush(); items.push(line.replace(HEADING, '').replace(/\s+#+\s*$/, '')); continue; }
    if (BULLET.test(line)) { flush(); current.push(line.replace(BULLET, '')); continue; }
    current.push(line);
  }
  flush();
  return items.map(item => item === CODE_LINE || item === TABLE_LINE ? item : inline(item))
    .filter(text => /[\p{L}\p{N}_]/u.test(text))
    .map(text => /[.!?…:;]["')\]”’»]*$/.test(text) ? text : text + '.').join(' ');
}
