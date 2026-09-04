/**
 * Faceted filters (S1.4.1).
 *
 * Two things worth stating about how these behave:
 *
 * - **Every option carries the count it would yield.** The server computes each facet
 *   against the set filtered by everything *except* that facet, so the number beside an
 *   option answers "how many would I get" rather than echoing the current selection back.
 * - **A facet nothing can populate is shown as unavailable, with the reason.** §15.3.2
 *   asks for state, family and train; all three are Migration Unit properties and the
 *   Cartographer creates MUs in E3. An empty dropdown is a worse answer than an explained
 *   absence — a user will keep opening it.
 */

import type { EstateResponse, FacetOption } from '../lib/api';
import type { Filters as FilterState } from './useEstate';

interface Props {
  facets: EstateResponse['facets'];
  tiers: string[];
  filters: FilterState;
  onChange: (patch: Partial<FilterState>) => void;
}

function Group({
  title,
  options,
  selected,
  onPick,
  limit = 8,
}: {
  title: string;
  options: FacetOption[];
  selected: string | null;
  onPick: (key: string | null) => void;
  limit?: number;
}): JSX.Element | null {
  const shown = options.filter((option) => option.count > 0 || option.key === selected);
  if (shown.length === 0) return null;

  return (
    <div className="filter-group">
      <h3>{title}</h3>
      {shown.slice(0, limit).map((option) => {
        const active = selected === option.key;
        return (
          <button
            type="button"
            key={option.key}
            className="filter-option"
            aria-pressed={active}
            onClick={() => onPick(active ? null : option.key)}
          >
            <span>{option.label}</span>
            <span className="count">{option.count.toLocaleString('en-GB')}</span>
          </button>
        );
      })}
      {shown.length > limit && (
        <p className="faint" style={{ margin: '4px 6px 0', fontSize: 11.5 }}>
          {shown.length - limit} more — narrow with search
        </p>
      )}
    </div>
  );
}

export function Filters({ facets, tiers, filters, onChange }: Props): JSX.Element {
  const tierOptions: FacetOption[] = tiers.map((tier) => ({
    key: tier,
    label: tier.toLowerCase(),
    count: facets.tier.find((option) => option.key === tier)?.count ?? 0,
  }));

  return (
    <div className="filters">
      <Group
        title="Parse quality"
        options={facets.parse_quality_band}
        selected={filters.parse_quality_band}
        onPick={(key) => onChange({ parse_quality_band: key })}
      />
      <Group
        title="Usage (90 days)"
        options={facets.usage_band}
        selected={filters.usage_band}
        onPick={(key) => onChange({ usage_band: key })}
      />
      <Group
        title="Tier"
        options={tierOptions}
        selected={filters.tier}
        onPick={(key) => onChange({ tier: key })}
      />
      <Group
        title="Owner"
        options={facets.owner}
        selected={filters.owner}
        onPick={(key) => onChange({ owner: key === '__none__' ? null : key })}
      />

      <div className="filter-group">
        <h3>Scope</h3>
        <button
          type="button"
          className="filter-option"
          aria-pressed={filters.unowned_only}
          onClick={() => onChange({ unowned_only: !filters.unowned_only })}
        >
          <span>Unowned only</span>
        </button>
        <button
          type="button"
          className="filter-option"
          aria-pressed={filters.include_withdrawn}
          onClick={() => onChange({ include_withdrawn: !filters.include_withdrawn })}
        >
          <span>Show withdrawn</span>
          <span className="count">{facets.withdrawn.toLocaleString('en-GB')}</span>
        </button>
      </div>

      {facets.pending.length > 0 && (
        <div className="filter-group">
          <h3>Not available yet</h3>
          {facets.pending.map((pending) => (
            <p className="pending-note" key={pending.facet}>
              <strong>{pending.facet}</strong> — {pending.reason}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
