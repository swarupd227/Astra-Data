/**
 * The Calibration Report -- story S10.2.1, opening F10.2.
 *
 * Tested through what a Programme Manager and a client analytics lead see and do: the
 * real report sections, the PM-gated sign form, and the PDF export -- see
 * `calibration/CalibrationReport.tsx`'s own docstring for the F13.1/F13.2 reading.
 */

import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { CalibrationReport } from '../calibration/CalibrationReport';
import { ApiError, type Identity } from '../lib/api';
import { calibrationBaseline, calibrationReportData, calibrationReportResponse, fakeApi } from './fixtures';

const PM: Identity = { principal: 'user:pm@artizent.example', roles: ['programme_manager'] };
const CLIENT_ANALYTICS_LEAD: Identity = {
  principal: 'user:lead@client.example',
  roles: ['client_analytics_lead'],
};

describe('reading the report', () => {
  it('shows class mix, coverage, parity by tier, C4 reasons, family count and cost per tier', async () => {
    const api = fakeApi();
    api.calibrationReport = async () =>
      calibrationReportResponse({
        report: calibrationReportData({
          class_mix: {
            total: 20, unclassified: 5,
            counts: { C1: 9, C2: 4, C3: 1, C4: 1 },
            percentages: { C1: 60, C2: 27, C3: 6, C4: 7 },
            targets: { C1: 44, C2: 30, C3: 18, C4: 7 },
          },
          calibration_targets: { C1: 44, C2: 30, C3: 18, C4: 7 },
        }),
      });
    render(<CalibrationReport api={api} identity={CLIENT_ANALYTICS_LEAD} />);

    const pane = await screen.findByRole('region', { name: 'Calibration Report' });
    expect(within(pane).getByText('60%')).toBeInTheDocument();
    expect(within(pane).getByText('44%')).toBeInTheDocument();
    // The default fixture's own C4 rate (unrelated to the class-mix overrides above).
    expect(within(pane).getByText(/1 of 15 calculated fields/)).toBeInTheDocument();
  });

  it('discloses elapsed time per stage and executor strategy mix as honestly not available', async () => {
    const api = fakeApi();
    api.calibrationReport = async () =>
      calibrationReportResponse({
        report: calibrationReportData({
          elapsed_time_per_stage: { available: false, detail: 'no uniform stage-timestamp series exists yet' },
          executor_strategy_mix: { available: false, detail: 'no execution-strategy concept exists yet' },
        }),
      });
    render(<CalibrationReport api={api} identity={PM} />);

    expect(await screen.findByText('no uniform stage-timestamp series exists yet')).toBeInTheDocument();
    expect(await screen.findByText('no execution-strategy concept exists yet')).toBeInTheDocument();
  });

  it('shows a comparison panel once a baseline has been signed', async () => {
    const api = fakeApi();
    api.calibrationReport = async () =>
      calibrationReportResponse({
        baseline: calibrationBaseline({ version: 2 }),
        comparison: { class_mix_delta: { C1: 2 } },
      });
    render(<CalibrationReport api={api} identity={PM} />);

    expect(await screen.findByText('last signed v2')).toBeInTheDocument();
    expect(await screen.findByText(/Comparison to the last signed baseline/)).toBeInTheDocument();
  });

  it('surfaces a read failure', async () => {
    const api = fakeApi();
    api.calibrationReport = async () => {
      throw new ApiError(503, 'unavailable', 'the Calibration Report is not available');
    };
    render(<CalibrationReport api={api} identity={PM} />);

    expect(await screen.findByText(/the Calibration Report is not available/)).toBeInTheDocument();
  });
});

describe('sign report', () => {
  it('lets the Programme Manager sign with a countersigner name', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<CalibrationReport api={api} identity={PM} />);
    await screen.findByRole('region', { name: 'Calibration Report' });

    await user.type(screen.getByLabelText(/Countersigned by/), 'A. Mehta');
    await user.click(screen.getByRole('button', { name: 'Sign report' }));

    expect(await screen.findByText(/Signed as version 1 -- countersigned by A. Mehta/)).toBeInTheDocument();
    expect(api.recorded).toContainEqual(
      expect.objectContaining({ kind: 'SIGN_CALIBRATION_REPORT', reason: 'A. Mehta' }),
    );
  });

  it('hides the sign form from anyone but the Programme Manager', async () => {
    render(<CalibrationReport api={fakeApi()} identity={CLIENT_ANALYTICS_LEAD} />);

    await screen.findByRole('region', { name: 'Calibration Report' });
    expect(screen.queryByRole('button', { name: 'Sign report' })).not.toBeInTheDocument();
    expect(
      screen.getByText(/Signing the Calibration Report is the Programme Manager/),
    ).toBeInTheDocument();
  });

  it('disables Sign report until a countersigner name is entered', async () => {
    render(<CalibrationReport api={fakeApi()} identity={PM} />);
    await screen.findByRole('region', { name: 'Calibration Report' });

    expect(screen.getByRole('button', { name: 'Sign report' })).toBeDisabled();
  });

  it('shows the API refusal rather than a generic failure', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.failNext(new ApiError(403, 'forbidden', 'programme manager only'));
    render(<CalibrationReport api={api} identity={PM} />);
    await screen.findByRole('region', { name: 'Calibration Report' });

    await user.type(screen.getByLabelText(/Countersigned by/), 'A. Mehta');
    await user.click(screen.getByRole('button', { name: 'Sign report' }));

    expect(
      await screen.findByText(/Signing the Calibration Report is the Programme Manager's action/),
    ).toBeInTheDocument();
  });
});

describe('export', () => {
  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => 'blob:test');
    URL.revokeObjectURL = vi.fn();
  });

  it('exports the report as a PDF', async () => {
    const user = userEvent.setup();
    const clicked = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    render(<CalibrationReport api={fakeApi()} identity={PM} />);
    await screen.findByRole('region', { name: 'Calibration Report' });

    await user.click(screen.getByRole('button', { name: 'Export as PDF' }));

    await screen.findByRole('button', { name: 'Export as PDF' });
    expect(clicked).toHaveBeenCalled();
    clicked.mockRestore();
  });
});
