/**
 * The Estate Explorer (spec §15.3.2, story S1.4.1).
 *
 * Three panes from one request: the site/project tree with counts and parse status, the
 * filtered workbook table, and the selected workbook with its lineage mini-graph.
 *
 * The status bar reports how long the read took against S1.4.1's two-second budget. A
 * budget nobody can see is a budget nobody keeps, and this one is the difference between
 * a screen a programme manager uses and one they ask somebody to export for them.
 */

import { useCallback, useEffect, useState } from 'react';

import type { Api, Identity, Workbook, WorkbookDetail } from '../lib/api';
import { ApiError } from '../lib/api';
import { Filters } from './Filters';
import { ReasonDialog } from './ReasonDialog';
import { Tree } from './Tree';
import { WorkbookPanel } from './WorkbookPanel';
import { WorkbookTable } from './WorkbookTable';
import { PAGE_SIZE, activeFilterCount, useEstate } from './useEstate';

//: S1.4.1: "screen loads a 1,067-workbook site in under 2 seconds".
export const LOAD_BUDGET_MS = 2000;

type Pending = 're-tier' | 'withdraw' | 'reinstate' | null;

interface Props {
  api: Api;
  identity: Identity;
}

export function EstateExplorer({ api, identity }: Props): JSX.Element {
  const estate = useEstate(api, identity);
  const [selected, setSelected] = useState<Workbook | null>(null);
  const [detail, setDetail] = useState<WorkbookDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [pending, setPending] = useState<Pending>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reharvesting, setReharvesting] = useState(false);

  useEffect(() => {
    if (!selected) {
      setDetail(null);
      return;
    }
    let live = true;
    setDetailLoading(true);
    api
      .workbook(selected.id, identity)
      .then((response) => live && setDetail(response))
      .catch(() => live && setDetail(null))
      .finally(() => live && setDetailLoading(false));
    return () => {
      live = false;
    };
  }, [api, identity, selected]);

  // Clearing the selection is tied to the *filters* changing, not to the data reloading.
  // The difference showed up the first time a workbook was withdrawn through the screen:
  // it left the default filter, the selection cleared, and the panel emptied — taking away
  // the decision the user had just made and the Reinstate button that undoes it.
  const changeFilters = useCallback(
    (patch: Parameters<typeof estate.setFilters>[0]) => {
      setSelected(null);
      estate.setFilters(patch);
    },
    [estate],
  );

  const clearFilters = useCallback(() => {
    setSelected(null);
    estate.clearFilters();
  }, [estate]);

  const runScopeAction = useCallback(
    async (reason: string, tier?: string) => {
      if (!selected || !pending) return;
      setBusy(true);
      setActionError(null);
      try {
        if (pending === 're-tier') await api.reTier(selected.id, tier ?? '', reason, identity);
        if (pending === 'withdraw') await api.withdraw(selected.id, reason, identity);
        if (pending === 'reinstate') await api.reinstate(selected.id, reason, identity);
        setPending(null);
        setNotice('Decision recorded.');
        const refreshed = await api.workbook(selected.id, identity);
        setDetail(refreshed);
        estate.reload();
      } catch (caught: unknown) {
        setActionError(
          caught instanceof ApiError ? caught.message : 'The decision could not be recorded.',
        );
      } finally {
        setBusy(false);
      }
    },
    [api, estate, identity, pending, selected],
  );

  const reharvest = useCallback(async () => {
    const site = selected?.site ?? estate.filters.site;
    if (!site) {
      setNotice('Select a site before re-harvesting.');
      return;
    }
    setReharvesting(true);
    setNotice(null);
    try {
      const run = await api.reharvest(site, `tableau/${site}`, identity);
      setNotice(`Harvest ${run.id} started for ${site}. Progress is on Platform Health.`);
    } catch (caught: unknown) {
      setNotice(
        caught instanceof ApiError
          ? `Harvest refused: ${caught.message}`
          : 'The harvest could not be started.',
      );
    } finally {
      setReharvesting(false);
    }
  }, [api, estate.filters.site, identity, selected]);

  const data = estate.data;
  const overBudget = estate.elapsedMs !== null && estate.elapsedMs > LOAD_BUDGET_MS;
  const filtersActive = activeFilterCount(estate.filters);

  return (
    <>
      {estate.error && <div className="banner">{estate.error}</div>}

      <div className="workspace">
        <section className="pane" aria-label="Sites and projects">
          <header className="pane-header">
            <h2>Estate</h2>
          </header>
          <div className="pane-body">
            <Tree
              tree={data?.tree ?? []}
              selection={{ site: estate.filters.site, project: estate.filters.project }}
              onSelect={(selection) => changeFilters(selection)}
            />
            {data && (
              <Filters
                facets={data.facets}
                tiers={data.tiers}
                filters={estate.filters}
                onChange={changeFilters}
              />
            )}
          </div>
        </section>

        <section className="pane" aria-label="Workbooks">
          <div className="toolbar">
            <input
              className="search"
              type="search"
              placeholder="Search workbooks, LUIDs, projects"
              aria-label="Search workbooks"
              value={estate.filters.search}
              onChange={(event) => changeFilters({ search: event.target.value })}
            />
            <button
              type="button"
              className="btn small"
              onClick={clearFilters}
              disabled={filtersActive === 0}
            >
              Clear filters{filtersActive > 0 ? ` (${filtersActive})` : ''}
            </button>
            <button type="button" className="btn small" onClick={estate.reload}>
              Refresh
            </button>
          </div>

          <div className="pane-body">
            {estate.loading && !data ? (
              <p className="empty">Reading the estate…</p>
            ) : (
              <WorkbookTable
                workbooks={data?.workbooks ?? []}
                selectedId={selected?.id ?? null}
                sort={estate.filters.sort}
                onSelect={setSelected}
                onSort={(sort) => changeFilters({ sort })}
              />
            )}
          </div>

          {data && data.total > PAGE_SIZE && (
            <div className="toolbar" style={{ borderTop: '1px solid var(--border)', borderBottom: 0 }}>
              <button
                type="button"
                className="btn small"
                onClick={() => estate.setPage(estate.page - 1)}
                disabled={estate.page === 0}
              >
                Previous
              </button>
              <span className="muted">
                {data.offset + 1}–{Math.min(data.offset + PAGE_SIZE, data.total)} of{' '}
                {data.total.toLocaleString('en-GB')}
              </span>
              <button
                type="button"
                className="btn small"
                onClick={() => estate.setPage(estate.page + 1)}
                disabled={data.offset + PAGE_SIZE >= data.total}
              >
                Next
              </button>
            </div>
          )}
        </section>

        <section className="pane" aria-label="Selected workbook">
          <header className="pane-header">
            <h2>Workbook</h2>
          </header>
          <div className="pane-body">
            <WorkbookPanel
              workbook={selected}
              detail={detail}
              loading={detailLoading}
              identity={identity}
              reharvesting={reharvesting}
              onReTier={() => setPending('re-tier')}
              onWithdraw={() => setPending('withdraw')}
              onReinstate={() => setPending('reinstate')}
              onReharvest={reharvest}
            />
          </div>
        </section>
      </div>

      <footer className="statusbar">
        <span>
          {data
            ? `${data.total.toLocaleString('en-GB')} of ${data.estate_total.toLocaleString('en-GB')} workbooks`
            : '—'}
        </span>
        {notice && <span>{notice}</span>}
        <span className="spacer" />
        {estate.elapsedMs !== null && (
          <span className={overBudget ? 'over-budget' : undefined}>
            {estate.elapsedMs} ms
            {data ? ` · ${Math.round(data.timing.estate_read_ms)} ms in the graph` : ''}
            {overBudget ? ` · over the ${LOAD_BUDGET_MS} ms budget` : ''}
          </span>
        )}
      </footer>

      {pending === 're-tier' && data && (
        <ReasonDialog
          title="Re-tier workbook"
          description="Recorded against the workbook with your name. The Migration Unit inherits it when the Cartographer creates one."
          confirmLabel="Record tier"
          tiers={data.tiers}
          currentTier={detail?.scope.current.tier ?? null}
          busy={busy}
          error={actionError}
          onConfirm={runScopeAction}
          onCancel={() => {
            setPending(null);
            setActionError(null);
          }}
        />
      )}

      {pending === 'withdraw' && (
        <ReasonDialog
          title="Withdraw from scope"
          description="The workbook stays in the estate and stops counting as work. It can be reinstated."
          confirmLabel="Withdraw"
          danger
          busy={busy}
          error={actionError}
          onConfirm={runScopeAction}
          onCancel={() => {
            setPending(null);
            setActionError(null);
          }}
        />
      )}

      {pending === 'reinstate' && (
        <ReasonDialog
          title="Reinstate to scope"
          description="Returns the workbook to the programme. The original withdrawal stays on the record."
          confirmLabel="Reinstate"
          busy={busy}
          error={actionError}
          onConfirm={runScopeAction}
          onCancel={() => {
            setPending(null);
            setActionError(null);
          }}
        />
      )}
    </>
  );
}
