/**
 * The Parity Dashboard and per-run view — S7.4.2, plus §10.4 sampling's own SAMPLED
 * label on a sampled PASS (S7.5.1).
 *
 * Tested through what a report owner sees and does: load a workbook's dashboard, read
 * the plain-language pass/fail statement, drill into a sheet's own failing cells, read
 * the per-MU trend and the disclosed-absent Mender passes, and read the per-run cases
 * table — plus that only the Parity Engineer gets the Re-run button, the same
 * hide-not-disable convention `ToleranceCharter.tsx`'s own Save button already follows.
 */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { App, surfaceFromPath } from '../App';
import { ApiError, type Identity } from '../lib/api';
import { ParityDashboard } from '../parity/ParityDashboard';
import {
  fakeApi,
  parityDashboardResponse,
  parityRunResponse,
  parityRunTrendEntry,
  sheetParityStats,
  verdictRow,
} from './fixtures';

const PARITY: Identity = { principal: 'user:parity@artizent.example', roles: ['parity_engineer'] };
const REPORT_OWNER: Identity = { principal: 'user:owner@client.example', roles: ['client_report_owner'] };
const OTHER: Identity = { principal: 'user:pm@artizent.example', roles: ['programme_manager'] };

const WORKBOOK_ID = '01ARZ3NDEKTSV4RRFFQ69G5FAV';

function renderScreen(identity: Identity = REPORT_OWNER, api = fakeApi()) {
  return { api, ...render(<ParityDashboard api={api} identity={identity} />) };
}

async function load(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('Workbook'), WORKBOOK_ID);
  await user.click(screen.getByRole('button', { name: 'Load' }));
}

describe('reading the dashboard', () => {
  it('shows the plain-language pass statement and the per-sheet stats', async () => {
    const user = userEvent.setup();
    renderScreen();
    await load(user);

    expect(
      await screen.findByText('This report does not yet pass the Tolerance Charter, version 1.'),
    ).toBeInTheDocument();
    expect(screen.getByText('Bar sheet')).toBeInTheDocument();
    // 75% appears twice: the sheet's own first-pass rate and the trend row's pass rate.
    expect(screen.getAllByText('75%')).toHaveLength(2);
  });

  it('shows a sheet\'s own failing cells, with expected / candidate / delta and filter context', async () => {
    const user = userEvent.setup();
    renderScreen();
    await load(user);

    await user.click((await screen.findByText('Bar sheet')).closest('tr')!);

    expect(await screen.findByText('Failing cells — Bar sheet')).toBeInTheDocument();
    expect(screen.getByText('Margin')).toBeInTheDocument();
    expect(screen.getByText('100')).toBeInTheDocument();
    expect(screen.getByText('101.2')).toBeInTheDocument();
    expect(screen.getByText('1.2')).toBeInTheDocument();
    expect(screen.getByText('{"kind":"default","filters":[]}')).toBeInTheDocument();
  });

  it('shows the per-MU pass rate trend across runs', async () => {
    const user = userEvent.setup();
    renderScreen();
    await load(user);

    expect(await screen.findByText('Per MU — pass rate trend across runs')).toBeInTheDocument();
    // run_1 appears twice: the trend row and the Parity Run pane's own run-id pill.
    expect(screen.getAllByText('run_1')).toHaveLength(2);
  });

  it('discloses Mender passes as not yet available', async () => {
    const user = userEvent.setup();
    renderScreen();
    await load(user);

    expect(await screen.findByText(/Mender passes: not yet available/)).toBeInTheDocument();
  });

  it('shows the real mean passes-to-pass once a case has closed through the Mender', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.parityDashboard = async () =>
      parityDashboardResponse({
        trend: {
          runs: [parityRunTrendEntry()],
          mender_passes: { available: true, closed_count: 3, mean_passes_to_pass: 2 },
        },
      });
    renderScreen(REPORT_OWNER, api);
    await load(user);

    expect(
      await screen.findByText('Mender passes: mean 2.00 to pass, over 3 closed cases.'),
    ).toBeInTheDocument();
  });

  it('surfaces a read failure', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.parityDashboard = async () => {
      throw new ApiError(404, 'not_found', `no ParityRun exists yet for workbook '${WORKBOOK_ID}'`);
    };
    renderScreen(REPORT_OWNER, api);
    await load(user);

    expect(await screen.findByText(/no ParityRun exists yet/)).toBeInTheDocument();
  });

  it('shows the per-run cases table alongside the dashboard', async () => {
    const user = userEvent.setup();
    renderScreen();
    await load(user);

    await screen.findByRole('heading', { name: 'Parity Run' });
    expect(screen.getByText('case_1')).toBeInTheDocument();
    expect(screen.getByText('FAIL')).toBeInTheDocument();
    expect(screen.getByText('case_2')).toBeInTheDocument();
    expect(screen.getByText('PASS')).toBeInTheDocument();
  });

  it('labels a sampled PASS as SAMPLED (§10.4)', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.parityRun = async () =>
      parityRunResponse({
        verdicts: [verdictRow({ id: 'v_sampled', case_ref: 'case_3', result: 'PASS', failing_cells: [], sampled: true })],
      });
    renderScreen(REPORT_OWNER, api);
    await load(user);

    await screen.findByRole('heading', { name: 'Parity Run' });
    expect(screen.getByText('SAMPLED')).toBeInTheDocument();
  });

  it('does not label an unsampled PASS', async () => {
    const user = userEvent.setup();
    renderScreen();
    await load(user);

    // The default fixture's own PASS verdict (case_2) is not sampled.
    await screen.findByRole('heading', { name: 'Parity Run' });
    expect(screen.queryByText('SAMPLED')).not.toBeInTheDocument();
  });
});

describe('re-running', () => {
  it('lets the parity engineer re-run and reports the fresh counts', async () => {
    const user = userEvent.setup();
    const { api } = renderScreen(PARITY);
    await load(user);

    await user.click(screen.getByRole('button', { name: 'Re-run parity' }));

    expect(
      await screen.findByText('Ran 4 case(s) under charter version 1: 4 pass, 0 fail, 0 inconclusive.'),
    ).toBeInTheDocument();
    expect(api.recorded.some((r) => r.kind === 'RUN_PARITY')).toBe(true);
  });

  it('hides Re-run and Score visuals from anyone but the Parity Engineer', async () => {
    const user = userEvent.setup();
    renderScreen(REPORT_OWNER);
    await load(user);

    expect(screen.queryByRole('button', { name: 'Re-run parity' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Score visuals' })).not.toBeInTheDocument();
    expect(screen.getByText(/Running parity and scoring visuals are the Parity Engineer/)).toBeInTheDocument();
  });

  it('also hides Re-run from an Artizent role that is not the Parity Engineer', async () => {
    const user = userEvent.setup();
    renderScreen(OTHER);
    await load(user);

    expect(screen.queryByRole('button', { name: 'Re-run parity' })).not.toBeInTheDocument();
  });
});

describe('visual parity (§10.5, advisory)', () => {
  it('shows the structural and image scores in the per-sheet table', async () => {
    const user = userEvent.setup();
    renderScreen();
    await load(user);

    expect(screen.getByText('86%')).toBeInTheDocument();
    expect(screen.getByText('93%')).toBeInTheDocument();
  });

  it('shows the source screenshot and target render side by side for a scored sheet', async () => {
    const user = userEvent.setup();
    renderScreen();
    await load(user);

    await user.click((await screen.findByText('Bar sheet')).closest('tr')!);

    expect(await screen.findByText('Source screenshot')).toBeInTheDocument();
    expect(screen.getByText('Target render')).toBeInTheDocument();
    expect(screen.getByAltText('Source screenshot of Bar sheet')).toBeInTheDocument();
    expect(screen.getByAltText('Target render of Bar sheet')).toBeInTheDocument();
  });

  it('discloses that an unscored sheet has not been scored yet, with no images', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.parityDashboard = async () =>
      parityDashboardResponse({
        sheets: [
          sheetParityStats({
            structural_score: null, structural_score_breakdown: null, image_score: null,
            visual_score_computed_at: null, source_screenshot_ref: null, target_render_ref: null,
          }),
        ],
      });
    renderScreen(REPORT_OWNER, api);
    await load(user);

    await user.click((await screen.findByText('Bar sheet')).closest('tr')!);

    expect(await screen.findByText(/has not been scored yet/)).toBeInTheDocument();
    expect(screen.queryByText('Source screenshot')).not.toBeInTheDocument();
  });

  it('lets the parity engineer score visuals and reports how many were scored', async () => {
    const user = userEvent.setup();
    const { api } = renderScreen(PARITY);
    await load(user);

    await user.click(screen.getByRole('button', { name: 'Score visuals' }));

    expect(
      await screen.findByText('Scored 1 visual(s) against their own source sheet.'),
    ).toBeInTheDocument();
    expect(api.recorded.some((r) => r.kind === 'RUN_VISUAL_PARITY')).toBe(true);
  });
});

describe('the shell', () => {
  it('offers Parity Dashboard as a surface', async () => {
    const user = userEvent.setup();
    render(<App api={fakeApi()} environment="local" initialRole="client_report_owner" />);

    await user.click(screen.getByRole('button', { name: 'Parity Dashboard' }));

    expect(await screen.findByRole('heading', { name: 'Parity Dashboard' })).toBeInTheDocument();
  });

  it('maps /parity to the Parity Dashboard surface', () => {
    expect(surfaceFromPath('/parity')).toBe('parity');
  });
});
