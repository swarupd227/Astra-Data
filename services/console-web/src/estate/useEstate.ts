/**
 * The Explorer's state: filters, selection, and the one request that feeds three panes.
 *
 * **Filters live in the URL.** A programme manager who has narrowed a thousand workbooks
 * to the eleven that are held and unowned will want to send that view to somebody. A
 * screen whose state is only in memory cannot be shared, bookmarked, or reloaded after a
 * mistake, and the browser's back button silently means something else.
 *
 * **One request, not three.** The tree, the page and the facet counts all come from one
 * read of the estate. Three endpoints would triple the work to draw one screen, and the
 * facet counts could disagree with the rows they sit beside.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { Api, EstateResponse, Identity } from '../lib/api';
import { ApiError } from '../lib/api';

export interface Filters {
  site: string | null;
  project: string | null;
  owner: string | null;
  tier: string | null;
  parse_quality_band: string | null;
  usage_band: string | null;
  unowned_only: boolean;
  include_withdrawn: boolean;
  search: string;
  sort: string;
}

export const EMPTY_FILTERS: Filters = {
  site: null,
  project: null,
  owner: null,
  tier: null,
  parse_quality_band: null,
  usage_band: null,
  unowned_only: false,
  include_withdrawn: false,
  search: '',
  sort: 'name',
};

const BOOLEAN_KEYS = ['unowned_only', 'include_withdrawn'] as const;

export function filtersFromSearch(search: string): Filters {
  const params = new URLSearchParams(search);
  const filters: Filters = { ...EMPTY_FILTERS };
  for (const key of Object.keys(EMPTY_FILTERS) as (keyof Filters)[]) {
    const raw = params.get(key);
    if (raw === null) continue;
    if ((BOOLEAN_KEYS as readonly string[]).includes(key)) {
      (filters[key] as boolean) = raw === 'true';
    } else {
      (filters[key] as string) = raw;
    }
  }
  return filters;
}

export function searchFromFilters(filters: Filters): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value === null || value === '' || value === false) continue;
    if (key === 'sort' && value === 'name') continue;
    params.set(key, String(value));
  }
  const rendered = params.toString();
  return rendered ? `?${rendered}` : '';
}

export function activeFilterCount(filters: Filters): number {
  return Object.entries(filters).filter(([key, value]) => {
    if (key === 'sort') return false;
    return value !== null && value !== '' && value !== false;
  }).length;
}

interface UseEstate {
  data: EstateResponse | null;
  loading: boolean;
  error: string | null;
  filters: Filters;
  setFilters: (patch: Partial<Filters>) => void;
  clearFilters: () => void;
  reload: () => void;
  page: number;
  setPage: (page: number) => void;
  elapsedMs: number | null;
}

export const PAGE_SIZE = 100;

export function useEstate(api: Api, identity: Identity): UseEstate {
  const [filters, setFiltersState] = useState<Filters>(() =>
    filtersFromSearch(typeof window === 'undefined' ? '' : window.location.search),
  );
  const [page, setPage] = useState(0);
  const [data, setData] = useState<EstateResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [elapsedMs, setElapsedMs] = useState<number | null>(null);
  const [nonce, setNonce] = useState(0);

  // A slow response for a filter the user has already changed must not overwrite the
  // fast one for the filter they are looking at.
  const request = useRef(0);

  const query = useMemo(
    () => ({
      site: filters.site,
      project: filters.project,
      owner: filters.owner,
      tier: filters.tier,
      parse_quality_band: filters.parse_quality_band,
      usage_band: filters.usage_band,
      unowned_only: filters.unowned_only,
      include_withdrawn: filters.include_withdrawn,
      search: filters.search || null,
      sort: filters.sort,
      offset: page * PAGE_SIZE,
      limit: PAGE_SIZE,
    }),
    [filters, page],
  );

  useEffect(() => {
    const id = ++request.current;
    setLoading(true);
    const started = performance.now();

    api
      .estate(query, identity)
      .then((response) => {
        if (id !== request.current) return;
        setData(response);
        setElapsedMs(Math.round(performance.now() - started));
        setError(null);
      })
      .catch((caught: unknown) => {
        if (id !== request.current) return;
        setError(
          caught instanceof ApiError
            ? `${caught.message}`
            : 'The estate could not be read. The service may be starting up.',
        );
      })
      .finally(() => {
        if (id === request.current) setLoading(false);
      });
  }, [api, identity, query, nonce]);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const next = `${window.location.pathname}${searchFromFilters(filters)}`;
    window.history.replaceState(null, '', next);
  }, [filters]);

  const setFilters = useCallback((patch: Partial<Filters>) => {
    setFiltersState((current) => ({ ...current, ...patch }));
    setPage(0);
  }, []);

  const clearFilters = useCallback(() => {
    setFiltersState(EMPTY_FILTERS);
    setPage(0);
  }, []);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  return {
    data,
    loading,
    error,
    filters,
    setFilters,
    clearFilters,
    reload,
    page,
    setPage,
    elapsedMs,
  };
}
