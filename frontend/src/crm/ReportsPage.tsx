/**
 * Reports (issue #144) — the CRM's reports surface. One report today: the company rollup.
 *
 * The report is reached by picking ONE company, because its value is depth on a single
 * account rather than a table of many. The selection lives in the URL (`?company=7`) so a
 * report is linkable and survives a reload, and the report is mounted with
 * `key={companyId}` so switching account collapses the previous one's expanded rows
 * without this page having to own or reset that state.
 *
 * Adding a second report means adding a block here. Nothing else is generalised for that
 * yet — one report does not justify a report registry.
 */
import { useCallback, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { api } from '../core/api/client';
import type { CrmCompany } from '../core/types';
import { CompanyRollupReport } from './components/CompanyRollupReport';
import { RecordCombobox } from './components/RecordCombobox';
import { useIsMobile } from '../shared/useIsMobile';
import { INK_DIM, INK_MUTE } from '../shared/styles';
import { pageHeading, pagePadding } from './styles';

const PICKER_LIMIT = 20;

// Module-level so they are not re-created per render. Deliberately duplicated from DealForm
// rather than imported from it: that is a component module and importing it here would drag
// the whole deal form into this route's chunk.
const searchCompanies = (query: string) =>
  api<{ companies: CrmCompany[] }>(
    `/api/crm/companies?limit=${PICKER_LIMIT}${query ? `&q=${encodeURIComponent(query)}` : ''}`,
  ).then(d => d.companies);

// Display carries the archived marker; MATCHING must not (the #123 rule).
const companyLabelOf = (co: CrmCompany) => (co.status === 'archived' ? `${co.name} (archived)` : co.name);
const companyNameOf = (co: CrmCompany) => co.name;
const companySublabelOf = (co: CrmCompany) => co.domain || '';
const recordId = (r: { id: number }) => r.id;

export function ReportsPage() {
  const isMobile = useIsMobile();
  const [searchParams, setSearchParams] = useSearchParams();
  const raw = searchParams.get('company');
  // Anything that is not a plain id reads as no selection rather than as a broken request.
  const companyId = raw && /^\d+$/.test(raw) ? Number(raw) : null;

  // The label is stored WITH the id it describes and derived during render, rather than
  // reset by an effect when the id changes. Back/Forward changes `?company=` without going
  // through `select`, and a label kept in its own state would then caption the new report
  // with the previous company's name until its own fetch landed.
  const [picked, setPicked] = useState<{ id: number; label: string } | null>(null);
  const companyLabel = picked && picked.id === companyId ? picked.label : '';

  const select = (company: CrmCompany | null) => {
    const next = new URLSearchParams(searchParams);
    if (company) {
      next.set('company', String(company.id));
      setPicked({ id: company.id, label: companyLabelOf(company) });
    } else {
      next.delete('company');
    }
    setSearchParams(next);
  };

  // Lets a deep-linked `?company=7` label the closed picker once the report has loaded,
  // instead of leaving it blank.
  const onCompanyName = useCallback(
    (name: string) => { if (companyId != null) setPicked({ id: companyId, label: name }); },
    [companyId],
  );

  return (
    <div style={pagePadding(isMobile)}>
      <h1 style={pageHeading(isMobile)}>Reports</h1>
      <p style={{ fontSize: 14, color: INK_MUTE, marginTop: 10, maxWidth: 560 }}>
        Everything this CRM knows about one company on a single page — its deals, its
        contacts, and every note and activity across the account.
      </p>

      <div style={{ maxWidth: 420, marginTop: 20 }}>
        {/* No `create` prop: a report must never create a company. */}
        <RecordCombobox<CrmCompany>
          label="Company"
          id="report-company"
          value={companyId}
          valueLabel={companyLabel}
          emptyLabel="Search for a company"
          search={searchCompanies}
          getId={recordId}
          getLabel={companyLabelOf}
          getMatchText={companyNameOf}
          getSublabel={companySublabelOf}
          onSelect={select}
        />
      </div>

      {companyId == null ? (
        <p style={{ fontSize: 13, color: INK_DIM, marginTop: 24 }}>
          Pick a company to build its report.
        </p>
      ) : (
        // key={companyId} remounts on a company change, which is what collapses the
        // previous account's expanded rows (that state lives inside the report).
        <CompanyRollupReport
          key={companyId}
          companyId={companyId}
          onCompanyName={onCompanyName}
        />
      )}
    </div>
  );
}
