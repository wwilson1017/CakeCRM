import { useEffect } from 'react';
import { filePath } from '../chatterAttachments';
import { useAuthedBlobUrl } from '../useAuthedBlobUrl';
import { INK_DIM, SCRIM } from '../../shared/styles';
import type { CrmAttachment } from '../../core/types';

interface Props {
  attachment: CrmAttachment;
  onClose: () => void;
}

/**
 * Full-size view of one attached image (issue #57).
 *
 * The ORIGINAL is fetched only here, on an explicit open — list views render the ≤28 KB
 * server thumbnail and never the original, which is the "bounded image memory"
 * requirement. `useAuthedBlobUrl` owns the fetch, the object URL and its revocation, so
 * closing this releases the bytes.
 *
 * Reachable only for an attachment that HAS a thumbnail. That is not a cosmetic rule: a
 * NULL thumbnail on an image means the server's decompression-bomb ceilings refused to
 * decode it, and opening it here would hand the browser exactly the decode the server
 * declined. Such attachments download instead. `NoteAttachments` enforces it at the call
 * site and a test pins it.
 */
export function AttachmentLightbox({ attachment, onClose }: Props) {
  const { url, error } = useAuthedBlobUrl(filePath(attachment.id));

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={attachment.filename}
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 60, background: SCRIM,
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 32,
      }}
    >
      {/* Stop a click on the image itself from closing, so a drag-to-select or a
          mis-click on the picture does not dismiss what the user just opened. */}
      <div onClick={e => e.stopPropagation()} style={{ maxWidth: '100%', maxHeight: '100%' }}>
        {error ? (
          <p style={{ color: INK_DIM, fontSize: 13 }}>Couldn't load that image.</p>
        ) : url ? (
          <img
            src={url}
            alt={attachment.filename}
            style={{ maxWidth: '100%', maxHeight: '85vh', objectFit: 'contain', display: 'block' }}
          />
        ) : (
          <p style={{ color: INK_DIM, fontSize: 13 }}>Loading…</p>
        )}
      </div>
    </div>
  );
}
