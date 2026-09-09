import { useState, useRef, useEffect } from 'react';
import { api } from '../../core/api/client';
import { useIsMobile } from '../../shared/useIsMobile';
import {
  INK, INK_MUTE, INK_SOFT, LINE, LINE_STRONG, BG_RAISED,
  GOLD_TEXT, SAGE_TEXT, CORAL_TEXT, FONT_DISPLAY, FONT_SANS,
} from '../../shared/styles';
import { modalOverlay, modalContent, btnPrimary, btnSecondary } from '../styles';

interface ParsedContact {
  name: string;
  email: string;
  phone: string;
  company: string;
  title: string;
  source: string;
  tags: string;
  notes: string;
}

interface ParseResult {
  contacts: ParsedContact[];
  ai_used: boolean;
  warnings: string[];
}

interface ImportResult {
  imported: number;
  skipped: number;
  errors: string[];
}

interface Props {
  onClose: () => void;
  onImported: () => void;
}

type Step = 'upload' | 'parsing' | 'preview' | 'result';

export function SmartImportModal({ onClose, onImported }: Props) {
  const isMobile = useIsMobile();
  const [step, setStep] = useState<Step>('upload');
  const [file, setFile] = useState<File | null>(null);
  const [parseResult, setParseResult] = useState<ParseResult | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState('');
  const [aiReady, setAiReady] = useState(false);
  const [importing, setImporting] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    // Degrade gracefully: CSV/vCard import always works; only advertise the
    // any-format AI path when a provider is configured.
    api<{ ai_ready: boolean }>('/api/setup/status')
      .then(s => setAiReady(s.ai_ready))
      .catch(() => setAiReady(false));
  }, []);

  const accept = aiReady ? '.csv,.vcf,.json,.txt,.tsv' : '.csv,.vcf';

  async function handleParse() {
    if (!file) return;
    setStep('parsing');
    setError('');
    try {
      const formData = new FormData();
      formData.append('file', file);
      const data = await api<ParseResult>('/api/crm/smart-import/parse', {
        method: 'POST', body: formData,
      });
      setParseResult(data);
      if (data.contacts.length > 0) {
        setSelected(new Set(data.contacts.map((_, i) => i)));
        setStep('preview');
      } else {
        setStep('upload');
        setError(data.warnings.join(' ') || 'No contacts found in file');
      }
    } catch (err: unknown) {
      setStep('upload');
      setError(err instanceof Error ? err.message : 'Parse failed');
    }
  }

  async function handleImport() {
    if (!parseResult || importing) return;  // guard against double-submit
    setError('');
    const contacts = parseResult.contacts.filter((_, i) => selected.has(i));
    if (contacts.length === 0) {
      setError('No contacts selected');
      return;
    }
    setImporting(true);
    try {
      const data = await api<ImportResult>('/api/crm/smart-import/confirm', {
        method: 'POST', body: JSON.stringify({ contacts }),
      });
      setImportResult(data);
      setStep('result');
      // Only auto-close on a clean run; if anything was skipped or errored, leave
      // the result up so the user can read it (they Close manually).
      if (data.imported > 0 && data.skipped === 0 && data.errors.length === 0) {
        setTimeout(onImported, 1500);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Import failed');
    } finally {
      setImporting(false);
    }
  }

  // After any import, closing should refresh the list; before importing it's a plain cancel.
  const closeAfterResult = () => (importResult && importResult.imported > 0 ? onImported() : onClose());

  function toggleAll() {
    if (!parseResult) return;
    setSelected(selected.size === parseResult.contacts.length
      ? new Set()
      : new Set(parseResult.contacts.map((_, i) => i)));
  }

  function toggleOne(idx: number) {
    const next = new Set(selected);
    if (next.has(idx)) next.delete(idx); else next.add(idx);
    setSelected(next);
  }

  const cellStyle: React.CSSProperties = {
    padding: '8px 10px', fontSize: 12, color: INK, textAlign: 'left',
    borderTop: `1px solid ${LINE}`, fontFamily: FONT_SANS,
  };
  const headCell: React.CSSProperties = {
    padding: '8px 10px', fontSize: 11, color: INK_SOFT, textAlign: 'left',
    fontFamily: FONT_SANS, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em',
  };

  return (
    <div style={modalOverlay(isMobile)} onClick={onClose}>
      <div
        onClick={e => e.stopPropagation()}
        style={modalContent(isMobile, step === 'preview' ? 760 : 480)}
      >
        <h2 style={{
          fontFamily: FONT_DISPLAY, fontSize: 20, fontWeight: 400,
          letterSpacing: '-0.02em', color: INK, margin: '0 0 8px',
        }}>Import Contacts</h2>

        {error && <p style={{ color: CORAL_TEXT, fontSize: 12, margin: '0 0 12px', fontFamily: FONT_SANS }}>{error}</p>}

        {step === 'upload' && (
          <>
            <p style={{ color: INK_MUTE, fontSize: 13, margin: '0 0 16px', lineHeight: 1.5, fontFamily: FONT_SANS }}>
              {aiReady
                ? 'Drop any file — CSV, vCard (.vcf), JSON, or plain text. AI will extract contacts from any format.'
                : 'Import a CSV or vCard (.vcf) file. Connect an AI provider in AI Setup to import other formats.'}
            </p>
            <div
              onClick={() => inputRef.current?.click()}
              style={{
                border: `2px dashed ${LINE_STRONG}`, borderRadius: 10, padding: 28,
                textAlign: 'center', cursor: 'pointer', marginBottom: 16, background: BG_RAISED,
              }}
            >
              <input
                ref={inputRef}
                type="file"
                accept={accept}
                style={{ display: 'none' }}
                onChange={e => setFile(e.target.files?.[0] ?? null)}
              />
              {file
                ? <p style={{ color: INK, fontSize: 13, margin: 0, fontFamily: FONT_SANS }}>{file.name} ({(file.size / 1024).toFixed(1)} KB)</p>
                : <p style={{ color: INK_SOFT, fontSize: 13, margin: 0, fontFamily: FONT_SANS }}>Click to select a file</p>}
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button onClick={onClose} style={{ ...btnSecondary, flex: 1, justifyContent: 'center', display: 'flex' }}>Cancel</button>
              <button
                onClick={handleParse}
                disabled={!file}
                style={{ ...btnPrimary, flex: 1, justifyContent: 'center', opacity: file ? 1 : 0.5, cursor: file ? 'pointer' : 'not-allowed' }}
              >Parse Contacts</button>
            </div>
          </>
        )}

        {step === 'parsing' && (
          <div style={{ padding: '32px 0', textAlign: 'center' }}>
            <div className="border-2 border-ck-line border-t-ck-accent rounded-full animate-spin"
                 style={{ width: 32, height: 32, margin: '0 auto 16px' }} />
            <p style={{ color: INK_MUTE, fontSize: 13, fontFamily: FONT_SANS }}>Analyzing contacts…</p>
          </div>
        )}

        {step === 'preview' && parseResult && (
          <>
            {parseResult.ai_used && (
              <p style={{ color: GOLD_TEXT, fontSize: 12, margin: '0 0 6px', fontFamily: FONT_SANS }}>
                Parsed with AI — please verify the results before importing.
              </p>
            )}
            {parseResult.warnings.map((w, i) => (
              <p key={i} style={{ color: GOLD_TEXT, fontSize: 12, margin: '0 0 4px', fontFamily: FONT_SANS }}>{w}</p>
            ))}

            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', margin: '12px 0 8px' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: INK_MUTE, cursor: 'pointer', fontFamily: FONT_SANS }}>
                <input type="checkbox" checked={selected.size === parseResult.contacts.length} onChange={toggleAll} />
                Select all ({parseResult.contacts.length})
              </label>
              <span style={{ fontSize: 12, color: INK_SOFT, fontFamily: FONT_SANS }}>{selected.size} selected</span>
            </div>

            <div style={{ maxHeight: 320, overflowY: 'auto', border: `1px solid ${LINE}`, borderRadius: 10, marginBottom: 16 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead style={{ position: 'sticky', top: 0, background: BG_RAISED }}>
                  <tr>
                    <th style={{ ...headCell, width: 32 }} />
                    <th style={headCell}>Name</th>
                    <th style={headCell}>Email</th>
                    <th style={headCell}>Phone</th>
                    <th style={headCell}>Company</th>
                  </tr>
                </thead>
                <tbody>
                  {parseResult.contacts.map((c, i) => (
                    <tr
                      key={i}
                      onClick={() => toggleOne(i)}
                      style={{ cursor: 'pointer', opacity: selected.has(i) ? 1 : 0.45 }}
                    >
                      <td style={{ ...cellStyle, textAlign: 'center' }}>
                        <input type="checkbox" checked={selected.has(i)} onChange={() => toggleOne(i)} onClick={e => e.stopPropagation()} />
                      </td>
                      <td style={cellStyle}>
                        <span style={{ color: INK, fontWeight: 500 }}>{c.name || '—'}</span>
                        {c.title && <span style={{ color: INK_SOFT, marginLeft: 4 }}>({c.title})</span>}
                      </td>
                      <td style={cellStyle}>{c.email || '—'}</td>
                      <td style={cellStyle}>{c.phone || '—'}</td>
                      <td style={cellStyle}>{c.company || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div style={{ display: 'flex', gap: 8 }}>
              <button onClick={onClose} style={{ ...btnSecondary, flex: 1, justifyContent: 'center', display: 'flex' }}>Cancel</button>
              <button
                onClick={handleImport}
                disabled={selected.size === 0 || importing}
                style={{ ...btnPrimary, flex: 1, justifyContent: 'center', opacity: (selected.size && !importing) ? 1 : 0.5, cursor: (selected.size && !importing) ? 'pointer' : 'not-allowed' }}
              >{importing ? 'Importing…' : `Import Selected (${selected.size})`}</button>
            </div>
          </>
        )}

        {step === 'result' && importResult && (
          <>
            <div style={{ background: BG_RAISED, borderRadius: 10, padding: 16, marginBottom: 16 }}>
              <p style={{ color: SAGE_TEXT, fontSize: 14, fontWeight: 500, margin: 0, fontFamily: FONT_SANS }}>
                {importResult.imported} contacts imported
              </p>
              {importResult.skipped > 0 && (
                <p style={{ color: INK_MUTE, fontSize: 12, margin: '4px 0 0', fontFamily: FONT_SANS }}>
                  {importResult.skipped} skipped (no name)
                </p>
              )}
              {importResult.errors.length > 0 && (
                <div style={{ marginTop: 8 }}>
                  <p style={{ color: CORAL_TEXT, fontSize: 12, margin: 0, fontFamily: FONT_SANS }}>{importResult.errors.length} errors:</p>
                  {importResult.errors.slice(0, 5).map((e, i) => (
                    <p key={i} style={{ color: INK_SOFT, fontSize: 12, margin: '2px 0 0', fontFamily: FONT_SANS }}>{e}</p>
                  ))}
                </div>
              )}
            </div>
            <button onClick={closeAfterResult} style={{ ...btnSecondary, width: '100%', justifyContent: 'center', display: 'flex' }}>Close</button>
          </>
        )}
      </div>
    </div>
  );
}
