/**
 * Contacts, on the shared collection layer (issue #77).
 *
 * The route is the selection. `/crm/contacts` and `/crm/contacts/:id` are ONE route
 * rendering this component, which shows the detail page when the segment is present and
 * the collection otherwise. That is why `usePageAssembly` lives HERE, above the branch:
 * the route element never changes, so opening a contact and coming back does not re-sweep
 * the corpus, and a cold deep link never sweeps it at all.
 *
 * The detail deliberately does NOT move into the layer's `CollectionDetail` shell, which
 * the issue suggested. `shared/overlay/DetailModal` renders at `z-50` and the assistant
 * launcher button sits at `z-40` — `DealDetailSheet` drops its own overlay to 39 precisely
 * to stay under it — so a modal contact detail would cover the launcher for exactly the
 * records that publish assistant context (#14). It is also `max-w-2xl`, where this detail
 * is a full-width working surface, and four other surfaces deep-link to `/crm/contacts/:id`.
 * What that costs is the shell's ‹ › record navigation; that is the trade.
 */
import { useCallback, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api } from '../core/api/client';
import type { CrmContact } from '../core/types';
import { ContactDetailPage } from './ContactDetailPage';
import { ContactForm } from './components/ContactForm';
import { SmartImportModal } from './components/SmartImportModal';
import { useOwnerOptions } from './useOwnerOptions';
import { CollectionView, useCollectionState } from '../shared/collection';
import type { FacetOption } from '../shared/search';
import { IconPlus } from '../shared/icons';
import { useIsMobile } from '../shared/useIsMobile';
import { INK_DIM, mono } from '../shared/styles';
import { pageHeading, btnPrimary, btnSecondary, btnSmall } from './styles';
import { makeContactsCollectionConfig } from './collectionConfig';
import { buildContactColumns } from './listColumns';
import { useCrmCorpus, type CrmCorpus } from './usePatchableAssembly';
import { useLocalDay } from './useLocalDay';
import { RefreshButton } from './components/RefreshButton';

// Module scope: the columns take no runtime deps, and the config they feed must be
// referentially stable or the layer re-derives every search doc on each keystroke.
const CONTACT_COLUMNS = buildContactColumns();
const NO_ROWS: CrmContact[] = [];

export function ContactsPage() {
  const { id } = useParams<{ id: string }>();
  const isMobile = useIsMobile();
  const [showCreate, setShowCreate] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const { options: owners, loading: usersLoading } = useOwnerOptions();

  const corpus = useCrmCorpus<CrmContact>(
    useCallback(async (params, signal) => {
      const res = await api<{ contacts: CrmContact[] }>(`/api/crm/contacts?${params}`, { signal });
      return res.contacts;
    }, []),
    // Gate: a cold deep link to /crm/contacts/42 must not pull the whole corpus. The gate
    // latches, so open → back never re-sweeps.
    id === undefined,
  );
  const { upsert, remove, retry } = corpus;

  if (id !== undefined) {
    return <ContactDetailPage onChanged={upsert} onDeleted={remove} onWriteUncertain={retry} />;
  }

  return (
    <div style={{ padding: isMobile ? '20px 16px' : '32px 44px', maxWidth: 1000 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: isMobile ? 16 : 24 }}>
        <h1 style={pageHeading(isMobile)}>Contacts</h1>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <RefreshButton onClick={retry} label="Reload contacts" />
          <button onClick={() => setShowImport(true)} style={{ ...btnSecondary, ...btnSmall }}>
            {isMobile ? 'Import' : 'Import Contacts'}
          </button>
          <button onClick={() => setShowCreate(true)} style={{ ...btnPrimary, ...btnSmall }}>
            <IconPlus size={13} strokeWidth={2.25} /> {isMobile ? 'Add' : 'Add Contact'}
          </button>
        </div>
      </div>

      {/* The roster decides whether an Owner facet exists at all, and the layer persists
          facet selections by config identity — so mount the collection only once it is
          known, rather than letting the facet appear a beat later. */}
      {usersLoading
        ? <p style={{ ...mono(12), color: INK_DIM }}>Loading…</p>
        : <ContactsCollection corpus={corpus} owners={owners} />}

      {showCreate && (
        <ContactForm
          onClose={() => setShowCreate(false)}
          onSaved={saved => { setShowCreate(false); upsert(saved); }}
          // A save whose outcome is unknown may have committed — re-sweep rather
          // than keep rendering a corpus we can no longer vouch for.
          onWriteUncertain={retry}
        />
      )}
      {showImport && (
        <SmartImportModal
          onClose={() => setShowImport(false)}
          // An import writes an unknown number of rows server-side, so re-sweep rather
          // than guess.
          onImported={() => { setShowImport(false); retry(); }}
        />
      )}
    </div>
  );
}

function ContactsCollection(
  { corpus, owners }: { corpus: CrmCorpus<CrmContact>; owners: FacetOption[] | null },
) {
  const navigate = useNavigate();
  // `now` changes once a day, which is what re-runs the Last-contact facet against the
  // new boundary — a predicate alone never would, since nothing re-renders at midnight.
  const { now } = useLocalDay();
  const config = useMemo(
    () => makeContactsCollectionConfig({ columns: CONTACT_COLUMNS, owners, now }),
    [owners, now],
  );
  const rows = corpus.items ?? NO_ROWS;
  const state = useCollectionState(config, rows);
  return (
    <CollectionView<CrmContact>
      config={config}
      state={state}
      items={rows}
      onSelect={selected => { if (selected !== null) navigate(`/crm/contacts/${selected}`); }}
      searchPlaceholder="Search contacts..."
      loading={{
        loading: corpus.loading,
        error: corpus.error,
        itemsLoaded: corpus.itemsLoaded,
        retry: corpus.retry,
      }}
    />
  );
}
