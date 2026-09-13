/**
 * The Programme Board's family-count figure (spec §15.3.1, story S3.1.3).
 *
 * The full Programme Board — KPI strip, train swimlanes, milestones — is S10.2.1's screen.
 * This is the one figure this story asks for: planned (150, §14.3 / Appendix A) against
 * measured, with the delta, and the "Confirm family count" action that turns the measured
 * figure from "whatever the last Cartographer run happened to produce" into a Programme
 * Manager's own dated, attributed calibration input. S10.2.1 absorbs this pane into the
 * fuller board rather than replacing it, the same way S3.1.1's clustering later gave the
 * Lineage View a graph-backed figure to prefer over its own computed one.
 *
 * No free-text count field: the count a Programme Manager confirms is read live from the
 * estate by the service, never typed — see graph-svc's `routes_provenance.py` and ADR 0024.
 *
 * The second pane — projected versus planned dates per train (story S3.2.3) — is this
 * story's literal "flagged on the Programme Board" ask. See ADR 0027: a projection is a
 * bottleneck estimate from measured throughput, and "insufficient data" (today, for every
 * real train — nothing yet drives a §3.2 state transition) is the honest, expected reading,
 * not an error.
 *
 * A third pane, since S4.2.2, is G2 cycle time: every family awaiting G2, how many working
 * days it has waited, its approver and open-question count, with a wait past the 5-working-
 * day default highlighted the same "flagged" way a late train already is — and a "Send
 * reminders" action for the 3-/5-day reminders that story asks for (see ADR 0031: the
 * action is a real, recorded, idempotent one; where a real notification channel would send
 * to is disclosed future scope, the same posture ADR 0030 already gave work tracking).
 *
 * A fourth pane, since S5.1.1, is the estate's calculation class mix — every calculated
 * field's C1-C4 class, measured against the calibration targets 45/30/18/7 (§9.1) — with a
 * "Re-classify" action for the parity engineer (the persona that story's own acceptance
 * criteria names) that reports how many fields moved class. See ADR 0035.
 *
 * A fifth pane, since S5.2.1, is rule coverage — the percentage of the estate's calculated
 * fields the shipped deterministic rules engine can render into real DAX, by rule family
 * (§9.2/§9.5) — with an "Apply rules" action for the platform engineer (the persona that
 * story's own acceptance criteria names) that reports how many fields it converted. See
 * ADR 0036.
 *
 * A sixth pane, since S8.3.2, is exception ageing and the Mender close rate — every real
 * live `ExceptionCase`, grouped by its §11.1 class and a real age band, alongside "failing
 * MUs closed without an ExceptionCase / failing MUs" (§16.6/§25's own R1 floor, >= 0.70),
 * read as "closed without ever needing a human Exception Desk decision" — no failure is
 * ever resolved without a real `ExceptionCase` existing (S8.1.1 opens one for every FAIL).
 * Read-only, no action of its own — see `exception_ageing.py`'s own docstring.
 *
 * A seventh pane, since S9.1.2 (closing F9.1), is accepted units by tier against plan —
 * every real row in the commercial ledger a G3 Approve has ever written, grouped by
 * §3.2's own tier, against `invoicing.PLANNED_BY_TIER`'s own disclosed planning split of
 * the identical 150 this board's own first pane already plans against. Read-only, no
 * action of its own — see `invoicing.py`'s own docstring.
 *
 * Three more panes, since S10.2.1 (opening F10.2), are §15.3.1's own "KPI strip", "train
 * swimlanes... blocked reasons" and "milestone rail and gate calendar" -- rendered first,
 * above every pane built so far, matching that row's own "Top... Middle... Bottom" layout.
 * "Exceptions ageing" is this story's own AC clause too, but the sixth pane above already
 * built it (S8.3.2) -- reused as-is, not rebuilt. Every KPI strip/swimlane/milestone figure
 * is real and live; see `programme_surface.py`'s own module docstring for exactly what
 * each one reads and which readings (absorption vs a "calibrated baseline", a "budget"
 * this codebase has never named in currency, "MUs by state" as the Wave Board's own
 * honestly-static state proxy) are disclosed rather than invented.
 */

import { useCallback, useEffect, useState } from 'react';

import { Explain } from '../components/Explain';
import type {
  AcceptanceSummary,
  AwaitingG2Review,
  Api,
  ClassMix,
  ExceptionAgeingResponse,
  Identity,
  KpiStrip,
  MilestoneRailResponse,
  ProgrammeRecord,
  RuleCoverage,
  TrainProjection,
  TrainSwimlanesResponse,
} from '../lib/api';
import { ApiError } from '../lib/api';

interface Props {
  api: Api;
  identity: Identity;
  /** A live-updates tick (story S10.1.2) -- incrementing it re-runs every pane's own
   * fetch effect, the identical "increment a number, the existing effect re-fetches"
   * shape each pane's own `nonce` already uses for its own actions. Every pane below
   * shares this one `Props` shape, so one prop threads through all six. */
  liveTick?: number;
}

export function ProgrammeBoard({ api, identity, liveTick }: Props): JSX.Element {
  const [programmes, setProgrammes] = useState<ProgrammeRecord[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .programmes(identity)
      .then((response) => {
        if (!live) return;
        setProgrammes(response.programmes);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!live) return;
        setError(
          caught instanceof ApiError ? caught.message : 'The programme record could not be read.',
        );
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [api, identity, nonce, liveTick]);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  // Same rule the Cartographer itself uses for "the" open programme (cartographer.py's
  // `_open_programme`): the most recently *started* one, so this pane and a re-cluster
  // agree on which programme a confirmation belongs to.
  const programme = (programmes ?? [])
    .filter((candidate) => candidate.open)
    .sort((a, b) => b.started_at.localeCompare(a.started_at))[0] ?? null;

  const canConfirm = identity.roles.includes('programme_manager');

  const confirm = useCallback(async () => {
    if (!programme) return;
    setBusy(true);
    setNotice(null);
    try {
      const updated = await api.confirmFamilyCount(programme.id, identity);
      setNotice(
        `Confirmed ${updated.family_count} families (planned ${updated.planned_family_count}, ` +
          `delta ${formatDelta(updated.family_count_delta)}).`,
      );
      reload();
    } catch (caught: unknown) {
      setNotice(
        caught instanceof ApiError
          ? caught.forbidden
            ? 'Confirming the family count is the Programme Manager\'s action.'
            : caught.message
          : 'The family count could not be confirmed.',
      );
    } finally {
      setBusy(false);
    }
  }, [api, identity, programme, reload]);

  return (
    <div className="workspace programme-board">
      <section className="pane" aria-label="Family count calibration">
        <header className="pane-header">
          <h2>Family count</h2>
        </header>
        <div className="pane-body">
          {error ? (
            <div className="banner">{error}</div>
          ) : loading && !programmes ? (
            <p className="empty">Reading the programme record…</p>
          ) : !programme ? (
            <p className="empty">No programme is open yet.</p>
          ) : (
            <div className="detail">
              <div className="family-count-figures">
                <div className="figure">
                  <span className="section-title">
                    Planned
                    <Explain api={api} identity={identity} metricKey="programme.family_count" />
                  </span>
                  <span className="figure-value numeric">{programme.planned_family_count}</span>
                </div>
                <div className="figure">
                  <span className="section-title">Measured</span>
                  <span className="figure-value numeric">
                    {programme.family_count ?? '—'}
                  </span>
                </div>
                <div className="figure">
                  <span className="section-title">Delta</span>
                  <span className="figure-value numeric">
                    {formatDelta(programme.family_count_delta)}
                  </span>
                </div>
              </div>

              <dl>
                <dt>Confirmed by</dt>
                <dd>{programme.family_count_confirmed_by ?? '—'}</dd>
                <dt>Confirmed at</dt>
                <dd>{programme.family_count_confirmed_at ?? 'not yet confirmed'}</dd>
              </dl>

              {canConfirm ? (
                <button type="button" className="btn primary" onClick={confirm} disabled={busy}>
                  {busy ? 'Confirming…' : 'Confirm family count'}
                </button>
              ) : (
                <p className="faint">
                  Confirming the family count is the Programme Manager&rsquo;s action.
                </p>
              )}
            </div>
          )}
        </div>
        <footer className="statusbar">
          {notice && <span>{notice}</span>}
          <span className="spacer" />
          {programme && (
            <span className="muted">§14.3 / Appendix A: ~150 shared governed models</span>
          )}
        </footer>
      </section>

      <KpiStripPane api={api} identity={identity} liveTick={liveTick} />
      <TrainSwimlanesPane api={api} identity={identity} liveTick={liveTick} />
      <MilestoneRailPane api={api} identity={identity} liveTick={liveTick} />
      <TrainProjectionsPane api={api} identity={identity} liveTick={liveTick} />
      <G2ReviewsPane api={api} identity={identity} liveTick={liveTick} />
      <ClassMixPane api={api} identity={identity} liveTick={liveTick} />
      <RuleCoveragePane api={api} identity={identity} liveTick={liveTick} />
      <ExceptionAgeingPane api={api} identity={identity} liveTick={liveTick} />
      <AcceptanceByTierPane api={api} identity={identity} liveTick={liveTick} />
    </div>
  );
}

function formatDelta(delta: number | null): string {
  if (delta === null) return '—';
  if (delta === 0) return '0';
  return delta > 0 ? `+${delta}` : String(delta);
}

// ---------------------------------------------------------- projected vs. planned (S3.2.3)

function TrainProjectionsPane({ api, identity, liveTick }: Props): JSX.Element {
  const [projections, setProjections] = useState<TrainProjection[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .trainProjections(identity)
      .then((response) => {
        if (!live) return;
        setProjections(response.projections);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!live) return;
        setError(
          caught instanceof ApiError ? caught.message : 'Train projections could not be read.',
        );
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [api, identity, liveTick]);

  const flaggedCount = (projections ?? []).filter((p) => p.flagged).length;

  return (
    <section className="pane" aria-label="Projected versus planned dates per train">
      <header className="pane-header">
        <h2>Projected vs. planned</h2>
        {projections && projections.length > 0 && (
          <span className={flaggedCount > 0 ? 'pill bad' : 'pill ok'}>
            {flaggedCount} at risk
          </span>
        )}
      </header>
      <div className="pane-body">
        {error ? (
          <div className="banner">{error}</div>
        ) : loading && !projections ? (
          <p className="empty">Reading train projections…</p>
        ) : !projections || projections.length === 0 ? (
          <p className="empty">No release trains yet.</p>
        ) : (
          <table className="estate">
            <caption className="visually-hidden">
              Each train&rsquo;s planned end date against its throughput-based projection
            </caption>
            <thead>
              <tr>
                <th>Train</th>
                <th>Planned end</th>
                <th>Projected end</th>
                <th>Confidence band</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {projections.map((p) => (
                <tr key={p.train_id} className={p.flagged ? 'flagged' : undefined}>
                  <td>{p.train_name}</td>
                  <td className="numeric">{p.planned_end ?? '—'}</td>
                  <td className="numeric">{p.projected_end ?? '—'}</td>
                  <td className="numeric">
                    {p.projected_end_early && p.projected_end_late
                      ? `${p.projected_end_early} – ${p.projected_end_late}`
                      : '—'}
                  </td>
                  <td>
                    {p.projected_end ? (
                      <span className={p.flagged ? 'pill bad' : 'pill ok'} title={p.reason}>
                        {p.flagged
                          ? `${p.days_late} working day${p.days_late === 1 ? '' : 's'} late`
                          : 'on track'}
                      </span>
                    ) : (
                      <span className="pill idle" title={p.reason}>
                        insufficient data
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}

// -------------------------------------------------------------- G2 cycle time (S4.2.2)

function G2ReviewsPane({ api, identity, liveTick }: Props): JSX.Element {
  const [slaWorkingDays, setSlaWorkingDays] = useState<number | null>(null);
  const [reviews, setReviews] = useState<AwaitingG2Review[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .awaitingG2(identity)
      .then((response) => {
        if (!live) return;
        setSlaWorkingDays(response.sla_working_days);
        setReviews(response.reviews);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!live) return;
        setError(
          caught instanceof ApiError ? caught.message : 'Families awaiting G2 could not be read.',
        );
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [api, identity, nonce, liveTick]);

  const sendReminders = useCallback(async () => {
    setBusy(true);
    setNotice(null);
    try {
      const result = await api.sendG2Reminders(identity);
      setNotice(
        result.count === 0
          ? 'No reminders were due — every family already has its latest one recorded.'
          : `Sent ${result.count} reminder${result.count === 1 ? '' : 's'}.`,
      );
      setNonce((value) => value + 1);
    } catch (caught: unknown) {
      setNotice(caught instanceof ApiError ? caught.message : 'Reminders could not be sent.');
    } finally {
      setBusy(false);
    }
  }, [api, identity]);

  const breachedCount = (reviews ?? []).filter((r) => r.breached).length;

  return (
    <section className="pane" aria-label="G2 cycle time">
      <header className="pane-header">
        <h2>G2 reviews</h2>
        {reviews && reviews.length > 0 && (
          <span className={breachedCount > 0 ? 'pill bad' : 'pill ok'}>
            {breachedCount} over SLA
          </span>
        )}
      </header>
      <div className="pane-body">
        {error ? (
          <div className="banner">{error}</div>
        ) : loading && !reviews ? (
          <p className="empty">Reading families awaiting G2…</p>
        ) : !reviews || reviews.length === 0 ? (
          <p className="empty">No family is awaiting G2 review.</p>
        ) : (
          <table className="estate">
            <caption className="visually-hidden">
              Every family awaiting G2, days waiting, its approver and open questions
            </caption>
            <thead>
              <tr>
                <th>Family</th>
                <th>Domain</th>
                <th>Days waiting</th>
                <th>Approver</th>
                <th>Open questions</th>
                <th>SLA</th>
              </tr>
            </thead>
            <tbody>
              {reviews.map((r) => (
                <tr key={r.family_id} className={r.breached ? 'flagged' : undefined}>
                  <td>{r.name ?? r.family_id}</td>
                  <td>{r.domain ?? '—'}</td>
                  <td className="numeric">{r.days_waiting ?? '—'}</td>
                  <td>{r.approver ?? 'unassigned'}</td>
                  <td className="numeric">{r.open_questions}</td>
                  <td>
                    <span className={r.breached ? 'pill bad' : 'pill ok'}>
                      {r.breached ? 'over SLA' : 'within SLA'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <footer className="statusbar">
        {notice && <span>{notice}</span>}
        <span className="spacer" />
        {slaWorkingDays !== null && (
          <span className="muted">SLA: {slaWorkingDays} working days</span>
        )}
        <button type="button" className="btn" onClick={sendReminders} disabled={busy}>
          {busy ? 'Sending…' : 'Send reminders'}
        </button>
      </footer>
    </section>
  );
}

// -------------------------------------------------------- calculation class mix (S5.1.1)

const CLASS_KEYS = ['C1', 'C2', 'C3', 'C4'] as const;

function ClassMixPane({ api, identity, liveTick }: Props): JSX.Element {
  const [mix, setMix] = useState<ClassMix | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .classMix(identity)
      .then((response) => {
        if (!live) return;
        setMix(response);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!live) return;
        setError(caught instanceof ApiError ? caught.message : 'Class mix could not be read.');
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [api, identity, nonce, liveTick]);

  const canReclassify = identity.roles.includes('parity_engineer');

  const reclassify = useCallback(async () => {
    setBusy(true);
    setNotice(null);
    try {
      const result = await api.reclassify(identity);
      setNotice(
        result.moved.length === 0
          ? `Re-classified ${result.total} field${result.total === 1 ? '' : 's'} — nothing moved class.`
          : `Re-classified ${result.total} field${result.total === 1 ? '' : 's'} — ${result.moved.length} moved class.`,
      );
      setNonce((value) => value + 1);
    } catch (caught: unknown) {
      setNotice(
        caught instanceof ApiError
          ? caught.forbidden
            ? 'Re-classifying calculated fields is the parity engineer\'s action.'
            : caught.message
          : 'Fields could not be re-classified.',
      );
    } finally {
      setBusy(false);
    }
  }, [api, identity]);

  return (
    <section className="pane" aria-label="Calculation class mix">
      <header className="pane-header">
        <h2>Calculation classes</h2>
        {mix && mix.total > 0 && (
          <span className="pill idle">
            {mix.total - mix.unclassified} of {mix.total} classified
            <Explain api={api} identity={identity} metricKey="programme.class_mix" />
          </span>
        )}
      </header>
      <div className="pane-body">
        {error ? (
          <div className="banner">{error}</div>
        ) : loading && !mix ? (
          <p className="empty">Reading the estate&rsquo;s class mix…</p>
        ) : !mix || mix.total === 0 ? (
          <p className="empty">No calculated fields have been harvested yet.</p>
        ) : (
          <table className="estate">
            <caption className="visually-hidden">
              Each calculation class against the calibration targets 45/30/18/7
            </caption>
            <thead>
              <tr>
                <th>Class</th>
                <th className="numeric">Count</th>
                <th className="numeric">Measured</th>
                <th className="numeric">Target</th>
              </tr>
            </thead>
            <tbody>
              {CLASS_KEYS.map((key) => (
                <tr key={key}>
                  <td>{key}</td>
                  <td className="numeric">{mix.counts[key]}</td>
                  <td className="numeric">{mix.percentages[key]}%</td>
                  <td className="numeric">{mix.targets[key]}%</td>
                </tr>
              ))}
              {mix.unclassified > 0 && (
                <tr>
                  <td>Unclassified</td>
                  <td className="numeric">{mix.unclassified}</td>
                  <td className="numeric" colSpan={2}>
                    <span className="faint">not yet classified — re-classify to measure</span>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </div>
      <footer className="statusbar">
        {notice && <span>{notice}</span>}
        <span className="spacer" />
        {mix && (
          <span className="muted">
            {mix.classifier_version !== null
              ? `ruleset version ${mix.classifier_version}`
              : mix.unclassified === mix.total
                ? 'never classified'
                : 'mixed ruleset versions'}
          </span>
        )}
        {canReclassify ? (
          <button type="button" className="btn" onClick={() => void reclassify()} disabled={busy}>
            {busy ? 'Re-classifying…' : 'Re-classify'}
          </button>
        ) : (
          <span className="faint">Re-classifying is the parity engineer&rsquo;s action.</span>
        )}
      </footer>
    </section>
  );
}

// ----------------------------------------------------------- rule coverage report (S5.2.1)

function RuleCoveragePane({ api, identity, liveTick }: Props): JSX.Element {
  const [coverage, setCoverage] = useState<RuleCoverage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .ruleCoverage(identity)
      .then((response) => {
        if (!live) return;
        setCoverage(response);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!live) return;
        setError(caught instanceof ApiError ? caught.message : 'Rule coverage could not be read.');
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [api, identity, nonce, liveTick]);

  const canApplyRules = identity.roles.includes('platform_engineer');

  const applyRules = useCallback(async () => {
    setBusy(true);
    setNotice(null);
    try {
      const result = await api.applyRules(identity);
      setNotice(
        result.matched === 0
          ? `Applied rules to ${result.total} field${result.total === 1 ? '' : 's'} — nothing new converted.`
          : `Applied rules to ${result.total} field${result.total === 1 ? '' : 's'} — ${result.matched} newly converted.`,
      );
      setNonce((value) => value + 1);
    } catch (caught: unknown) {
      setNotice(
        caught instanceof ApiError
          ? caught.forbidden
            ? 'Applying the rules engine is the platform engineer\'s action.'
            : caught.message
          : 'Rules could not be applied.',
      );
    } finally {
      setBusy(false);
    }
  }, [api, identity]);

  const families = coverage ? Object.entries(coverage.by_family).sort(([a], [b]) => a.localeCompare(b)) : [];

  return (
    <section className="pane" aria-label="Rule coverage">
      <header className="pane-header">
        <h2>Rule coverage</h2>
        {coverage && coverage.total > 0 && (
          <span className="pill idle">
            {coverage.matched} of {coverage.total} converted
            <Explain api={api} identity={identity} metricKey="programme.rule_coverage" />
          </span>
        )}
      </header>
      <div className="pane-body">
        {error ? (
          <div className="banner">{error}</div>
        ) : loading && !coverage ? (
          <p className="empty">Reading rule coverage…</p>
        ) : !coverage || coverage.total === 0 ? (
          <p className="empty">No calculated fields have been harvested yet.</p>
        ) : families.length === 0 ? (
          <p className="empty">No calculated field has been converted by a rule yet.</p>
        ) : (
          <table className="estate">
            <caption className="visually-hidden">Calculated fields matched by rule, by rule family</caption>
            <thead>
              <tr>
                <th>Rule family</th>
                <th className="numeric">Converted</th>
              </tr>
            </thead>
            <tbody>
              {families.map(([family, count]) => (
                <tr key={family}>
                  <td>{family}</td>
                  <td className="numeric">{count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <footer className="statusbar">
        {notice && <span>{notice}</span>}
        <span className="spacer" />
        {coverage && (
          <span className="muted">
            {coverage.percentage}% covered · rules version {coverage.rules_version}
          </span>
        )}
        {canApplyRules ? (
          <button type="button" className="btn" onClick={() => void applyRules()} disabled={busy}>
            {busy ? 'Applying…' : 'Apply rules'}
          </button>
        ) : (
          <span className="faint">Applying the rules engine is the platform engineer&rsquo;s action.</span>
        )}
      </footer>
    </section>
  );
}

// ------------------------------------------------- exception ageing & close rate (S8.3.2)

function closeRatePillClass(meetsTarget: boolean | null): string {
  if (meetsTarget === null) return 'pill idle';
  return meetsTarget ? 'pill ok' : 'pill bad';
}

function ExceptionAgeingPane({ api, identity, liveTick }: Props): JSX.Element {
  const [ageing, setAgeing] = useState<ExceptionAgeingResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .exceptionAgeing(identity)
      .then((response) => {
        if (!live) return;
        setAgeing(response);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!live) return;
        setError(caught instanceof ApiError ? caught.message : 'Exception ageing could not be read.');
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [api, identity, liveTick]);

  const classes = Array.from(
    new Set((ageing?.open_by_class_and_age_band ?? []).map((entry) => entry.class)),
  ).sort((a, b) => a.localeCompare(b));
  const countFor = (failureClass: string, ageBand: string): number =>
    ageing?.open_by_class_and_age_band.find(
      (entry) => entry.class === failureClass && entry.age_band === ageBand,
    )?.count ?? 0;
  const rate = ageing?.mender_close_rate ?? null;

  return (
    <section className="pane" aria-label="Exception ageing">
      <header className="pane-header">
        <h2>Exception ageing</h2>
        {rate && (
          <span className={closeRatePillClass(rate.meets_target)}>
            {rate.rate === null ? 'no failures yet' : `${Math.round(rate.rate * 100)}% Mender close rate`}
          </span>
        )}
      </header>
      <div className="pane-body">
        {error ? (
          <div className="banner">{error}</div>
        ) : loading && !ageing ? (
          <p className="empty">Reading exception ageing…</p>
        ) : !ageing || ageing.total_open === 0 ? (
          <p className="empty">No open or blocked exception right now.</p>
        ) : (
          <table className="estate">
            <caption className="visually-hidden">
              Open exceptions by §11.1 class and age band
            </caption>
            <thead>
              <tr>
                <th>Class</th>
                {ageing.age_bands.map((band) => (
                  <th key={band.key}>{band.label}</th>
                ))}
                <th>Total</th>
              </tr>
            </thead>
            <tbody>
              {classes.map((failureClass) => {
                const total = ageing.age_bands.reduce(
                  (sum, band) => sum + countFor(failureClass, band.key), 0,
                );
                return (
                  <tr key={failureClass}>
                    <td>{failureClass}</td>
                    {ageing.age_bands.map((band) => (
                      <td key={band.key} className="numeric">{countFor(failureClass, band.key)}</td>
                    ))}
                    <td className="numeric">{total}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
      <footer className="statusbar">
        {rate && (
          <span className="muted">
            {rate.total_failures === 0
              ? 'No failure has ever opened an ExceptionCase yet.'
              : `${rate.mender_closed} of ${rate.total_failures} failures closed without an Exception Desk decision · target ${Math.round(rate.target * 100)}%`}
          </span>
        )}
      </footer>
    </section>
  );
}

// ---------------------------------------------------- accepted units by tier (S9.1.2)

function deltaPillClass(delta: number): string {
  if (delta === 0) return 'pill ok';
  return delta > 0 ? 'pill ok' : 'pill bad';
}

function formatCurrency(value: number): string {
  return `$${Math.round(value).toLocaleString('en-US')}`;
}

function AcceptanceByTierPane({ api, identity, liveTick }: Props): JSX.Element {
  const [summary, setSummary] = useState<AcceptanceSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .acceptanceSummary(identity)
      .then((response) => {
        if (!live) return;
        setSummary(response);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!live) return;
        setError(caught instanceof ApiError ? caught.message : 'Accepted units could not be read.');
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [api, identity, liveTick]);

  return (
    <section className="pane" aria-label="Accepted units by tier">
      <header className="pane-header">
        <h2>Accepted units by tier</h2>
        {summary && (
          <span className="pill idle mono">
            {summary.total_accepted} of {summary.total_planned} planned
            <Explain api={api} identity={identity} metricKey="invoicing.accepted_by_tier" />
          </span>
        )}
      </header>
      <div className="pane-body">
        {error ? (
          <div className="banner">{error}</div>
        ) : loading && !summary ? (
          <p className="empty">Reading the commercial ledger…</p>
        ) : !summary ? null : (
          <table className="estate">
            <caption className="visually-hidden">
              Every real G3 acceptance, by tier, against the planned split and each tier's own unit price
            </caption>
            <thead>
              <tr>
                <th>Tier</th>
                <th>Accepted</th>
                <th>Planned</th>
                <th>Delta</th>
                <th>Unit price</th>
                <th>Accepted value</th>
              </tr>
            </thead>
            <tbody>
              {summary.by_tier.map((row) => (
                <tr key={row.tier}>
                  <td>{row.tier}</td>
                  <td className="numeric">{row.accepted}</td>
                  <td className="numeric">{row.planned}</td>
                  <td>
                    <span className={deltaPillClass(row.delta)}>
                      {row.delta > 0 ? `+${row.delta}` : row.delta}
                    </span>
                  </td>
                  <td className="numeric">{formatCurrency(row.unit_price)}</td>
                  <td className="numeric">{formatCurrency(row.accepted_value)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <footer className="statusbar">
        {summary && (
          <span className="muted">
            §3.1: invoicing is triggered by an MU reaching ACCEPTED — total accepted value{' '}
            {formatCurrency(summary.total_accepted_value)}
          </span>
        )}
      </footer>
    </section>
  );
}

// ------------------------------------------------------------------- KPI strip (S10.2.1)

function formatPercent(value: number | null): string {
  return value === null ? '—' : `${(value * 100).toFixed(1)}%`;
}

function KpiStripPane({ api, identity, liveTick }: Props): JSX.Element {
  const [kpis, setKpis] = useState<KpiStrip | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .kpiStrip(identity)
      .then((response) => {
        if (!live) return;
        setKpis(response);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!live) return;
        setError(caught instanceof ApiError ? caught.message : 'The KPI strip could not be read.');
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [api, identity, liveTick]);

  return (
    <section className="pane kpi-strip" aria-label="KPI strip">
      <header className="pane-header">
        <h2>KPI strip</h2>
      </header>
      <div className="pane-body">
        {error ? (
          <div className="banner">{error}</div>
        ) : loading && !kpis ? (
          <p className="empty">Reading the KPI strip…</p>
        ) : !kpis ? null : (
          <div className="kpi-tiles">
            <div className="kpi-tile">
              <span className="section-title">MUs by state</span>
              <span className="figure-value">
                {Object.entries(kpis.mus_by_state).length === 0
                  ? '—'
                  : Object.entries(kpis.mus_by_state)
                      .map(([state, count]) => `${count} ${state}`)
                      .join(', ')}
              </span>
            </div>
            <div className="kpi-tile">
              <span className="section-title">First-pass parity</span>
              <span className="figure-value numeric">{formatPercent(kpis.first_pass_parity.first_pass_rate)}</span>
            </div>
            <div className="kpi-tile">
              <span className="section-title">
                Absorption
                <Explain api={api} identity={identity} metricKey="programme.absorption" />
              </span>
              <span className="figure-value numeric">{formatPercent(kpis.absorption.mean_ratio)}</span>
              <span className="faint">vs {formatPercent(kpis.absorption.threshold)} threshold</span>
            </div>
            <div className="kpi-tile">
              <span className="section-title">Gates due this week</span>
              <span className="figure-value numeric">{kpis.gates_due_this_week.due_this_week_count}</span>
              <span className="faint" title={kpis.gates_due_this_week.scope}>
                {kpis.gates_due_this_week.already_breached_count} already past SLA
              </span>
            </div>
            <div className="kpi-tile">
              <span className="section-title">
                Spend vs budget
                <Explain api={api} identity={identity} metricKey="programme.spend_vs_budget" />
              </span>
              <span className="figure-value numeric">
                {formatCurrency(kpis.spend_vs_budget.spend)} / {formatCurrency(kpis.spend_vs_budget.budget)}
              </span>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

// ------------------------------------------------------------ train swimlanes (S10.2.1)

function TrainSwimlanesPane({ api, identity, liveTick }: Props): JSX.Element {
  const [swimlanes, setSwimlanes] = useState<TrainSwimlanesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .trainSwimlanes(identity)
      .then((response) => {
        if (!live) return;
        setSwimlanes(response);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!live) return;
        setError(caught instanceof ApiError ? caught.message : 'Train swimlanes could not be read.');
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [api, identity, liveTick]);

  return (
    <section className="pane" aria-label="Train swimlanes">
      <header className="pane-header">
        <h2>Train swimlanes</h2>
      </header>
      <div className="pane-body">
        {error ? (
          <div className="banner">{error}</div>
        ) : loading && !swimlanes ? (
          <p className="empty">Reading train swimlanes…</p>
        ) : !swimlanes || swimlanes.trains.length === 0 ? (
          <p className="empty">No release trains yet.</p>
        ) : (
          <table className="estate">
            <caption className="visually-hidden">
              Each train's planned vs actual dates, MU counts by state, and blocked reasons
            </caption>
            <thead>
              <tr>
                <th>Train</th>
                <th>Planned</th>
                <th>Actual</th>
                <th>MU counts by state</th>
                <th>Blocked</th>
              </tr>
            </thead>
            <tbody>
              {swimlanes.trains.map((train) => (
                <tr key={train.id} className={train.blocked_count > 0 ? 'flagged' : undefined}>
                  <td>{train.name ?? train.id}</td>
                  <td>
                    {train.planned_start ?? '—'} → {train.planned_end ?? '—'}
                  </td>
                  <td>
                    {train.actual_start ?? '—'} → {train.actual_end ?? '—'}
                  </td>
                  <td>
                    {Object.entries(train.state_counts)
                      .map(([state, count]) => `${count} ${state}`)
                      .join(', ') || '—'}
                  </td>
                  <td>
                    {train.blocked_count === 0 ? (
                      <span className="faint">none</span>
                    ) : (
                      <span className="pill bad" title={train.blocked.map((b) => b.reason).join('; ')}>
                        {train.blocked_count} blocked
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}

// -------------------------------------------------------------- milestone rail (S10.2.1)

function MilestoneRailPane({ api, identity, liveTick }: Props): JSX.Element {
  const [milestones, setMilestones] = useState<MilestoneRailResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .milestoneRail(identity)
      .then((response) => {
        if (!live) return;
        setMilestones(response);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!live) return;
        setError(caught instanceof ApiError ? caught.message : 'The milestone rail could not be read.');
      })
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [api, identity, liveTick]);

  return (
    <section className="pane" aria-label="Milestone rail">
      <header className="pane-header">
        <h2>Milestone rail and gate calendar</h2>
      </header>
      <div className="pane-body">
        {error ? (
          <div className="banner">{error}</div>
        ) : loading && !milestones ? (
          <p className="empty">Reading the milestone rail…</p>
        ) : !milestones || milestones.rail.length === 0 ? (
          <p className="empty">No dated milestone exists yet.</p>
        ) : (
          <ul className="milestone-rail">
            {milestones.rail.map((milestone) => (
              <li key={`${milestone.kind}-${milestone.ref}-${milestone.date}`}>
                <span className="mono">{milestone.date}</span>
                <span className={`pill ${milestone.kind === 'gate' ? 'ok' : 'idle'}`}>{milestone.kind}</span>
                <span>{milestone.label}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
