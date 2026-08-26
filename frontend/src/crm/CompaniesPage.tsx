/**
 * Companies, on the shared collection layer (issue #77).
 *
 * Same shape as ContactsPage — the route is the selection, the assembly lives above the
 * detail branch so open → back does not re-sweep, and the rollup detail page (#13) stays a
 * routed page rather than moving into the layer's modal shell. See ContactsPage's header
 * for why.
 */
import { useCallback, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api } from '../core/api/client';
import type { CrmCompany } from '../core/types';
import { CompanyDetailPage } from './CompanyDetailPage';
import { CompanyForm } from './components/CompanyForm';
import { useOwnerOptions } from './useOwnerOptions';
import { CollectionView, useCollectionState } from '../shared/collection';
import type { FacetOption } from '../shared/search';
import { IconPlus } from '../shared/icons';
import { useIsMobile } from '../shared/useIsMobile';
import { INK_DIM, mono } from '../shared/styles';
import { pageHeading, btnPrimary, btnSmall } from './styles';
import { makeCompaniesCollectionConfig } from './collectionConfig';
import { buildCompanyColumns } from './listColumns';
import { useCrmCorpus, type CrmCorpus } from './usePatchableAssembly';
import { RefreshButton } from './components/RefreshButton';

const COMPANY_COLUMNS = buildCompanyColumns();
const NO_ROWS: CrmCompany[] = [];

export function CompaniesPage() {
  const { id } = useParams<{ id: string }>();
  const isMobile = useIsMobile();
  const [showCreate, setShowCreate] = useState(false);
  const { options: owners, loading: usersLoading } = useOwnerOptions();

  const corpus = useCrmCorpus<CrmCompany>(
    useCallback(async (params, signal) => {
      const res = await api<{ companies: CrmCompany[] }>(`/api/crm/companies?${params}`, { signal });
      return res.companies;
    }, []),
    id === undefined,
  );
  const { upsert, remove, retry } = corpus;

  if (id !== undefined) {
    return <CompanyDetailPage onChanged={upsert} onDeleted={remove} />;
  }

  return (
    <div style={{ padding: isMobile ? '20px 16px' : '32px 44px', maxWidth: 1000 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: isMobile ? 16 : 24 }}>
        <h1 style={pageHeading(isMobile)}>Companies</h1>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <RefreshButton onClick={retry} label="Reload companies" />
          <button onClick={() => setShowCreate(true)} style={{ ...btnPrimary, ...btnSmall }}>
            <IconPlus size={13} strokeWidth={2.25} /> {isMobile ? 'Add' : 'Add Company'}
          </button>
        </div>
      </div>

      {usersLoading
        ? <p style={{ ...mono(12), color: INK_DIM }}>Loading…</p>
        : <CompaniesCollection corpus={corpus} owners={owners} />}

      {showCreate && (
        <CompanyForm
          onClose={() => setShowCreate(false)}
          onSaved={saved => { setShowCreate(false); upsert(saved); }}
          // A save whose outcome is unknown may have committed — re-sweep rather
          // than keep rendering a corpus we can no longer vouch for.
          onWriteUncertain={retry}
        />
      )}
    </div>
  );
}

function CompaniesCollection(
  { corpus, owners }: { corpus: CrmCorpus<CrmCompany>; owners: FacetOption[] | null },
) {
  const navigate = useNavigate();
  const config = useMemo(
    () => makeCompaniesCollectionConfig({ columns: COMPANY_COLUMNS, owners }),
    [owners],
  );
  const rows = corpus.items ?? NO_ROWS;
  const state = useCollectionState(config, rows);
  return (
    <CollectionView<CrmCompany>
      config={config}
      state={state}
      items={rows}
      onSelect={selected => { if (selected !== null) navigate(`/crm/companies/${selected}`); }}
      searchPlaceholder="Search companies..."
      loading={{
        loading: corpus.loading,
        error: corpus.error,
        itemsLoaded: corpus.itemsLoaded,
        retry: corpus.retry,
      }}
    />
  );
}
