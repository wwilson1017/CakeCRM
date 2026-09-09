import { useState } from 'react';
import { api, apiBlob } from '../../core/api/client';
import { filePath, formatBytes, isImage, thumbPath } from '../chatterAttachments';
import { useAuthedBlobUrl } from '../useAuthedBlobUrl';
import { AttachmentLightbox } from './AttachmentLightbox';
import { toast } from '../../shared/toast';
import { INK_DIM, INK_MUTE, LINE_STRONG, mono } from '../../shared/styles';
import type { CrmAttachment } from '../../core/types';

interface Props {
  items: CrmAttachment[];
  onChanged: () => void;
}

/**
 * How long a download's object URL is kept alive before revoking (ms).
 *
 * Revoking immediately after `a.click()` can cancel or truncate the download in some
 * browsers — the navigation to the blob has been requested but not necessarily started.
 * A few seconds is far past any real start and far short of a leak that matters.
 */
const DOWNLOAD_REVOKE_DELAY_MS = 5000;

/** One image tile, backed by the server thumbnail — never the original. */
function Thumb({ item, onOpen }: { item: CrmAttachment; onOpen: () => void }) {
  const { url, error } = useAuthedBlobUrl(thumbPath(item.id));
  return (
    <button
      type="button"
      onClick={onOpen}
      title={item.filename}
      style={{
        width: 72, height: 72, padding: 0, borderRadius: 4, overflow: 'hidden',
        border: `1px solid ${LINE_STRONG}`, background: 'transparent', cursor: 'pointer',
        flexShrink: 0,
      }}
    >
      {url && !error ? (
        <img src={url} alt={item.filename}
             style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
      ) : (
        <span style={{ ...mono(9), color: INK_DIM }}>{error ? '—' : '…'}</span>
      )}
    </button>
  );
}

/**
 * The attachments hanging off one posted note (issue #57).
 *
 * Images with a server thumbnail render as a tile that opens the lightbox. Everything else
 * — non-images, and images the server's bomb ceilings refused to thumbnail — renders as a
 * chip that DOWNLOADS. That split is deliberate: a missing thumbnail on an image means the
 * server declined to decode those bytes, so offering to open it in the browser would just
 * move the decode to the user's machine.
 *
 * Nothing here is a bare `<img src>`: every byte comes through `apiBlob` with the Bearer
 * token, and every object URL is revoked (by `useAuthedBlobUrl` for the tiles, on a timer
 * for downloads).
 */
export function NoteAttachments({ items, onChanged }: Props) {
  const [open, setOpen] = useState<CrmAttachment | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  async function download(item: CrmAttachment) {
    try {
      const blob = await apiBlob(filePath(item.id));
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = item.filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), DOWNLOAD_REVOKE_DELAY_MS);
    } catch {
      toast.error('Failed to download that attachment.');
    }
  }

  async function remove(item: CrmAttachment) {
    if (!window.confirm(`Remove "${item.filename}" permanently?`)) return;
    setBusyId(item.id);
    try {
      await api(`/api/crm/chatter/attachments/${item.id}`, { method: 'DELETE' });
      onChanged();
    } catch {
      toast.error('Failed to remove that attachment.');
    } finally {
      setBusyId(null);
    }
  }

  return (
    <>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 6 }}>
        {items.map(item => {
          // An image WITHOUT a thumbnail deliberately falls through to the chip.
          const openable = isImage(item.mime_type) && item.has_thumb;
          return (
            <span key={item.id} style={{ display: 'inline-flex', flexDirection: 'column', gap: 2 }}>
              {openable ? (
                <Thumb item={item} onOpen={() => setOpen(item)} />
              ) : (
                <button
                  type="button"
                  onClick={() => void download(item)}
                  style={{
                    display: 'inline-flex', alignItems: 'center', gap: 6,
                    border: `1px solid ${LINE_STRONG}`, borderRadius: 4, padding: '5px 8px',
                    background: 'transparent', cursor: 'pointer',
                    fontSize: 11, color: INK_MUTE, maxWidth: 220,
                  }}
                >
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {item.filename}
                  </span>
                  <span style={{ ...mono(10), flexShrink: 0 }}>{formatBytes(item.byte_size)}</span>
                </button>
              )}
              <button
                type="button"
                onClick={() => void remove(item)}
                disabled={busyId === item.id}
                style={{
                  background: 'none', border: 'none', color: INK_DIM,
                  fontSize: 10, cursor: 'pointer', padding: 0, textAlign: 'left',
                }}
              >{busyId === item.id ? 'Removing…' : 'Remove'}</button>
            </span>
          );
        })}
      </div>
      {open && <AttachmentLightbox attachment={open} onClose={() => setOpen(null)} />}
    </>
  );
}
