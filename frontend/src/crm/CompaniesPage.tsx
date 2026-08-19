import { useState, useEffect, useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../core/api/client';
import type { CrmCompany } from '../core/types';
import { CompanyForm } from './components/CompanyForm';
import { StatusBadge } from './components/badges';
import { IconPlus, IconSearch } from '../shared/icons';
import { useIsMobile } from '../shared/useIsMobile';
import { LoadError } from '../shared/LoadError';
import { toast } from '../shared/toast';
import { INK, INK_MUTE, INK_DIM, LINE, BG_RAISED, FONT_SANS, mono, HOVER } from '../shared/styles';
import {
  pageHeading, cardStyle, filterTab,
  tableHeader, tableRow, btnPrimary, btnSmall,
} from './styles';

const STATUS_TABS = ['all', 'active', 'archived'] as const;

const COLS = '2fr 1.5fr 1.5fr 1.2fr 80px';
const PAGE_SIZE = 50;

export function CompaniesPage() {
  const [companies, setCompanies] = useState<CrmCompany[]>([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState<string>('all');
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [loadMoreFailed, setLoadMoreFailed] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  // Track current request so a stale response (filter change mid-flight) can't overwrite fresh data
  const loadIdRef = useRef(0);

  const fetchPage = useCallback(async (offset: number) => {
    const params = new URLSearchParams();
    if (search) params.set('q', search);
    if (status !== 'all') params.set('status', status);
    params.set('limit', String(PAGE_SIZE));
    params.set('offset', String(offset));
    return api<{ companies: CrmCompany[]; total: number }>(`/api/crm/companies?${params}`);
  }, [search, status]);

  const reload = useCallback(async () => {
    const id = ++loadIdRef.current;
    setLoading(true);
    setLoadingMore(false);
    setLoadFailed(false);
    setLoadMoreFailed(false);
    try {
      const data = await fetchPage(0);
      if (id !== loadIdRef.current) return;
      setCompanies(data.companies);
      setTotal(data.total);
    } catch {
      if (id !== loadIdRef.current) return;
      setCompanies([]);
      setTotal(0);
      setLoadFailed(true);
    } finally {
      if (id === loadIdRef.current) setLoading(false);
    }
  }, [fetchPage]);

  const loadMore = useCallback(async () => {
    if (loading || loadingMore) return;
    const id = loadIdRef.current;
    setLoadingMore(true);
    try {
      const data = await fetchPage(companies.length);
      if (id !== loadIdRef.current) return;
      setCompanies(prev => [...prev, ...data.companies]);
      setTotal(data.total);
    } catch {
      if (id !== loadIdRef.current) return;
      // Stop the observer from re-firing loadMore in a tight loop on a persistent
      // backend error (which would also spam the toast); the sentinel offers Retry.
      setLoadMoreFailed(true);
      toast.error('Failed to load more companies.');
    } finally {
      if (id === loadIdRef.current) setLoadingMore(false);
    }
  }, [fetchPage, companies.length, loading, loadingMore]);

  useEffect(() => {
    const t = setTimeout(reload, search ? 300 : 0);
    return () => clearTimeout(t);
  }, [reload, search]);

  useEffect(() => {
    if (loading) return;
    if (loadMoreFailed) return;  // don't auto-retry a failed page; wait for Retry
    if (companies.length >= total) return;
    const el = sentinelRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      entries => { if (entries[0].isIntersecting) loadMore(); },
      { rootMargin: '200px' },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [loading, loadingMore, loadMoreFailed, companies.length, total, loadMore]);

  return (
    <div style={{ padding: isMobile ? '20px 16px' : '32px 44px', maxWidth: 1000 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: isMobile ? 16 : 24 }}>
        <h1 style={pageHeading(isMobile)}>Companies</h1>
        <button onClick={() => setShowCreate(true)} style={{ ...btnPrimary, ...btnSmall }}>
          <IconPlus size={13} strokeWidth={2.25} /> {isMobile ? 'Add' : 'Add Company'}
        </button>
      </div>

      {/* Search + filter */}
      <div style={{ display: 'flex', flexDirection: isMobile ? 'column' : 'row', gap: 12, marginBottom: isMobile ? 16 : 24 }}>
        <div style={{
          flex: 1, display: 'flex', alignItems: 'center', gap: 8,
          background: BG_RAISED, border: `1px solid ${LINE}`,
          borderRadius: 4, padding: '0 12px',
        }}>
          <IconSearch size={14} strokeWidth={1.85} style={{ color: INK_DIM }} />
          <input type="text" placeholder="Search companies..." value={search} onChange={e => setSearch(e.target.value)}
            style={{
              flex: 1, background: 'transparent', border: 'none', color: INK,
              padding: '9px 0', fontSize: 13, outline: 'none',
              fontFamily: FONT_SANS,
            }}
          />
        </div>
        <div style={{ display: 'flex', gap: 0, flexShrink: 0 }}>
          {STATUS_TABS.map(tab => (
            <button key={tab} onClick={() => setStatus(tab)} style={filterTab(isMobile, status === tab)}>{tab}</button>
          ))}
        </div>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '48px 0' }}>
          <div className="w-6 h-6 border-2 border-ck-accent border-t-transparent rounded-full animate-spin" />
        </div>
      ) : loadFailed && companies.length === 0 ? (
        <LoadError label="Couldn't load companies" onRetry={reload} />
      ) : companies.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '64px 0' }}>
          <p style={{ color: INK_DIM, fontSize: 14 }}>
            {search ? 'No companies match your search.' : 'No companies yet. Add your first one!'}
          </p>
        </div>
      ) : (
        <>
          <p style={{ ...mono(12), marginBottom: 12 }}>{total} compan{total !== 1 ? 'ies' : 'y'}</p>
          {isMobile ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {companies.map(co => (
                <div key={co.id} onClick={() => navigate(`/crm/companies/${co.id}`)}
                  style={{ padding: '12px 14px', cursor: 'pointer', ...cardStyle }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                    <span style={{ fontSize: 16, color: INK }}>{co.name}</span>
                    <StatusBadge status={co.status} />
                  </div>
                  {co.industry && <div style={{ fontSize: 14, color: INK_MUTE, marginBottom: 2 }}>{co.industry}</div>}
                  {co.domain && <div style={{ fontSize: 14, color: INK_DIM }}>{co.domain}</div>}
                </div>
              ))}
            </div>
          ) : (
            <div style={{ borderTop: `1px solid ${LINE}` }}>
              <div style={tableHeader(COLS)}>
                <span>Name</span><span>Industry</span><span>Domain</span><span>Phone</span><span>Status</span>
              </div>
              {companies.map(co => (
                <div key={co.id} onClick={() => navigate(`/crm/companies/${co.id}`)}
                  style={tableRow(COLS)}
                  onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = HOVER; }}
                  onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'transparent'; }}
                >
                  <div>
                    <p style={{ fontSize: 16, color: INK, margin: 0 }}>{co.name}</p>
                  </div>
                  <span style={{ fontSize: 15, color: INK_MUTE, alignSelf: 'center' }}>{co.industry || '—'}</span>
                  <span style={{ fontSize: 15, color: INK_MUTE, alignSelf: 'center' }}>{co.domain || '—'}</span>
                  <span style={{ fontSize: 15, color: INK_MUTE, alignSelf: 'center' }}>{co.phone || '—'}</span>
                  <span style={{ alignSelf: 'center' }}><StatusBadge status={co.status} /></span>
                </div>
              ))}
            </div>
          )}
          {companies.length < total && (
            <div ref={sentinelRef} style={{ display: 'flex', justifyContent: 'center', padding: '20px 0', ...mono(12), color: INK_DIM }}>
              {loadMoreFailed ? (
                <button onClick={() => setLoadMoreFailed(false)} style={{
                  background: 'none', border: 'none', color: INK_MUTE, cursor: 'pointer', ...mono(12),
                }}>Couldn't load more — Retry</button>
              ) : loadingMore ? 'Loading more…' : `${total - companies.length} more`}
            </div>
          )}
        </>
      )}

      {showCreate && <CompanyForm onClose={() => setShowCreate(false)} onSaved={() => { setShowCreate(false); reload(); }} />}
    </div>
  );
}
