/**
 * The Parse Quality Queue (spec §15.3.2, story S1.4.3).
 *
 * A platform engineer's screen, and the ordering is the point: the queue is worked
 * **construct-first**, not workbook-first. One grammar gap typically blocks many workbooks,
 * so "fixing this releases 38 workbooks" is the number that decides what to do next — and
 * it is the column the table sorts on by default.
 *
 * The held workbooks are still listed, because §15.3.2 asks for both and because "which of
 * my workbooks is stuck" is a fair question. They are the left pane; the work is in the
 * centre.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import type {
  Api,
  ConstructGroup,
  ConstructsResponse,
  HeldWorkbook,
  Identity,
  QueueResponse,
} from '../lib/api';
import { ApiError } from '../lib/api';
import { ConstructPanel } from './ConstructPanel';
import { HeldWorkbooks } from './HeldWorkbooks';

type Pending = 'ignorable' | 'issue' | null;

interface Props {
  api: Api;
  identity: Identity;
}

export function ParseQualityQueue({ api, identity }: Props): JSX.Element {
  const [queue, setQueue] = useState<QueueResponse | null>(null);
  const [constructs, setConstructs] = useState<ConstructsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reharvesting, setReharvesting] = useState(false);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let live = true;
    setLoading(true);
    Promise.all([api.parseQualityQueue(identity), api.constructs(identity)])
      .then(([held, grouped]) => {
        if (!live) return;
        setQueue(held);
        setConstructs(grouped);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!live) return;
        setError(
          caught instanceof ApiError ? caught.message : 'The queue could not be read.',
        );
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [api, identity, nonce]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  // Ordered by what fixing each would release, then by how much of the estate it touches.
  // That is the working order: a construct blocking thirty workbooks is worth an afternoon
  // and one blocking a single workbook usually is not.
  const ordered = useMemo(() => {
    const groups = [...(constructs?.constructs ?? [])];
    groups.sort(
      (a, b) =>
        b.workbooks_released_if_resolved - a.workbooks_released_if_resolved ||
        b.workbooks - a.workbooks ||
        b.occurrences - a.occurrences ||
        a.construct.localeCompare(b.construct),
    );
    return groups;
  }, [constructs]);

  const current = useMemo(
    () => ordered.find((group) => group.construct === selected) ?? null,
    [ordered, selected],
  );

  const act = useCallback(
    async (reason: string, summary?: string) => {
      if (!current || !pending) return;
      setBusy(true);
      setActionError(null);
      try {
        if (pending === 'ignorable') {
          const result = await api.markIgnorable(current.construct, reason, identity);
          setNotice(
            `Accepted. ${result.workbooks_released} workbook${
              result.workbooks_released === 1 ? '' : 's'
            } released, ${result.workbooks_rescored} re-scored.`,
          );
        } else {
          const issue = await api.openGrammarIssue(
            current.construct,
            summary ?? '',
            reason,
            identity,
          );
          setNotice(
            issue.external.ref
              ? `Grammar issue ${issue.external.ref} raised.`
              : 'Grammar issue raised. No work tracker is configured, so it is held here.',
          );
        }
        setPending(null);
        reload();
      } catch (caught: unknown) {
        setActionError(
          caught instanceof ApiError ? caught.message : 'The action could not be recorded.',
        );
      } finally {
        setBusy(false);
      }
    },
    [api, current, identity, pending, reload],
  );

  const reharvest = useCallback(
    async (workbook: HeldWorkbook | null) => {
      const site = workbook?.site ?? current?.sites[0];
      if (!site) {
        setNotice('Select a construct or a workbook first, so there is a site to re-harvest.');
        return;
      }
      setReharvesting(true);
      try {
        const run = await api.reharvest(site, `tableau/${site}`, identity);
        setNotice(
          `Harvest ${run.id} started for ${site}. Workbooks are re-parsed under the current grammar.`,
        );
      } catch (caught: unknown) {
        setNotice(
          caught instanceof ApiError
            ? `Harvest refused: ${caught.message}`
            : 'The harvest could not be started.',
        );
      } finally {
        setReharvesting(false);
      }
    },
    [api, current, identity],
  );

  const releasable = ordered.reduce(
    (total, group) => total + group.workbooks_released_if_resolved,
    0,
  );

  return (
    <>
      {error && <div className="banner">{error}</div>}

      <div className="workspace">
        <section className="pane" aria-label="Held workbooks">
          <header className="pane-header">
            <h2>Held workbooks</h2>
          </header>
          <div className="pane-body">
            <HeldWorkbooks
              queue={queue}
              loading={loading}
              onReharvest={reharvest}
              reharvesting={reharvesting}
            />
          </div>
        </section>

        <section className="pane" aria-label="Unrecognised constructs">
          <div className="toolbar">
            <strong>Parse Quality Queue</strong>
            <span className="muted">
              {constructs
                ? `${ordered.length} construct${ordered.length === 1 ? '' : 's'} · ${releasable} workbook${
                    releasable === 1 ? '' : 's'
                  } released by fixing them`
                : '—'}
            </span>
            <span className="spacer" style={{ flex: 1 }} />
            <button type="button" className="btn small" onClick={reload}>
              Refresh
            </button>
          </div>

          <div className="pane-body">
            {loading && !constructs ? (
              <p className="empty">Reading the queue…</p>
            ) : ordered.length === 0 ? (
              <p className="empty">
                Nothing is held.
                <br />
                Every workbook parsed above the {percent(constructs?.threshold)} threshold.
              </p>
            ) : (
              <ConstructTable
                groups={ordered}
                selected={selected}
                onSelect={(group) => setSelected(group.construct)}
              />
            )}
          </div>

          <footer className="statusbar">
            {notice && <span>{notice}</span>}
            <span className="spacer" />
            {constructs && (
              <span>
                Threshold {percent(constructs.threshold)} · spec §4.1.4
              </span>
            )}
          </footer>
        </section>

        <section className="pane" aria-label="Selected construct">
          <header className="pane-header">
            <h2>Construct</h2>
          </header>
          <div className="pane-body">
            <ConstructPanel
              group={current}
              busy={busy}
              pending={pending}
              error={actionError}
              reharvesting={reharvesting}
              onMarkIgnorable={() => setPending('ignorable')}
              onOpenIssue={() => setPending('issue')}
              onReharvest={() => reharvest(null)}
              onConfirm={act}
              onCancel={() => {
                setPending(null);
                setActionError(null);
              }}
            />
          </div>
        </section>
      </div>
    </>
  );
}

function ConstructTable({
  groups,
  selected,
  onSelect,
}: {
  groups: ConstructGroup[];
  selected: string | null;
  onSelect: (group: ConstructGroup) => void;
}): JSX.Element {
  return (
    <table className="estate">
      <caption className="visually-hidden">
        Unrecognised constructs, ordered by how many workbooks fixing each would release
      </caption>
      <thead>
        <tr>
          <th scope="col">Construct</th>
          <th scope="col" className="numeric">
            Releases
          </th>
          <th scope="col" className="numeric">
            Workbooks
          </th>
          <th scope="col" className="numeric">
            Occurrences
          </th>
          <th scope="col">Sites</th>
          <th scope="col">Issue</th>
        </tr>
      </thead>
      <tbody>
        {groups.map((group) => (
          <tr
            key={group.construct}
            aria-selected={group.construct === selected}
            tabIndex={0}
            onClick={() => onSelect(group)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                onSelect(group);
              }
            }}
          >
            <td className="mono" title={group.construct}>
              {group.construct}
            </td>
            <td className="numeric">
              {group.workbooks_released_if_resolved > 0 ? (
                <span
                  className="pill ok"
                  title="Held workbooks for which this is the only remaining unrecognised construct"
                >
                  {group.workbooks_released_if_resolved}
                </span>
              ) : (
                <span className="faint" title="Every workbook holding this has other gaps too">
                  —
                </span>
              )}
            </td>
            <td
              className="numeric"
              title="Workbooks containing this construct, held or not"
            >
              {group.workbooks.toLocaleString('en-GB')}
            </td>
            <td className="numeric">{group.occurrences.toLocaleString('en-GB')}</td>
            <td>{group.sites.join(', ')}</td>
            <td>
              {group.issue ? (
                <span className="pill warn" title={`Opened by ${group.issue.opened_by}`}>
                  {group.issue.external.ref ?? group.issue.state.toLowerCase()}
                </span>
              ) : (
                <span className="faint">—</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function percent(value: number | undefined): string {
  return value === undefined ? '—' : `${Math.round(value * 100)}%`;
}
