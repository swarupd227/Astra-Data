/**
 * Throughput and cost metrics -- story S6.2.3.
 *
 * Tested through what a Programme Manager and a read-only Artizent role see and do: the
 * generate action, the three real tables (custodians live per week, agent acceptance per
 * custodian per day, credits per custodian per day), and the CSV export -- see
 * `throughput-report/ThroughputReport.tsx`'s own docstring for the disclosed readings.
 */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, type Identity } from '../lib/api';
import { ThroughputReport } from '../throughput-report/ThroughputReport';
import { fakeApi, throughputReportData } from './fixtures';

const PM: Identity = { principal: 'user:pm@artizent.example', roles: ['programme_manager'] };
const PLATFORM_ENGINEER: Identity = {
  principal: 'user:p.eng@artizent.example',
  roles: ['platform_engineer'],
};

describe('reading the report', () => {
  it('says no throughput report has been generated yet when none exists', async () => {
    render(<ThroughputReport api={fakeApi()} identity={PM} />);

    expect(await screen.findByText('No throughput report has been generated yet.')).toBeInTheDocument();
  });

  it('shows all three real tables once a report exists', async () => {
    const api = fakeApi();
    const generated = throughputReportData();
    api.throughputReport = async () => generated;
    render(<ThroughputReport api={api} identity={PLATFORM_ENGINEER} />);

    const pane = await screen.findByRole('region', { name: 'Throughput and cost' });
    expect(pane).toHaveTextContent('RQA');
    expect(pane).toHaveTextContent('GTAA');
    expect(pane).toHaveTextContent('$0.2880');
  });

  it('surfaces a read failure other than the honest empty state', async () => {
    const api = fakeApi();
    api.throughputReport = async () => {
      throw new ApiError(503, 'unavailable', 'the throughput report is not available');
    };
    render(<ThroughputReport api={api} identity={PM} />);

    expect(await screen.findByText(/the throughput report is not available/)).toBeInTheDocument();
  });
});

describe('generate', () => {
  it('lets the Programme Manager generate the report', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<ThroughputReport api={api} identity={PM} />);
    await screen.findByText('No throughput report has been generated yet.');

    await user.click(screen.getByRole('button', { name: 'Generate' }));

    expect(await screen.findByRole('region', { name: 'Throughput and cost' })).toBeInTheDocument();
    expect(api.recorded).toContainEqual(expect.objectContaining({ kind: 'GENERATE_THROUGHPUT_REPORT' }));
  });

  it('hides Generate from anyone but the Programme Manager', async () => {
    const api = fakeApi();
    api.throughputReport = async () => throughputReportData();
    render(<ThroughputReport api={api} identity={PLATFORM_ENGINEER} />);

    await screen.findByRole('region', { name: 'Throughput and cost' });
    expect(screen.queryByRole('button', { name: 'Generate' })).not.toBeInTheDocument();
    expect(screen.getByText(/Generating is the Programme Manager/)).toBeInTheDocument();
  });

  it('shows the API refusal rather than a generic failure', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.failNext(new ApiError(403, 'forbidden', 'programme manager only'));
    render(<ThroughputReport api={api} identity={PM} />);
    await screen.findByText('No throughput report has been generated yet.');

    await user.click(screen.getByRole('button', { name: 'Generate' }));

    expect(await screen.findByText('programme manager only')).toBeInTheDocument();
  });
});

describe('export', () => {
  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => 'blob:test');
    URL.revokeObjectURL = vi.fn();
  });

  it('exports the report as CSV', async () => {
    const user = userEvent.setup();
    const clicked = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    const api = fakeApi();
    api.throughputReport = async () => throughputReportData();
    render(<ThroughputReport api={api} identity={PLATFORM_ENGINEER} />);
    await screen.findByRole('region', { name: 'Throughput and cost' });

    await user.click(screen.getByRole('button', { name: 'Export as CSV' }));

    expect(clicked).toHaveBeenCalledTimes(1);
    clicked.mockRestore();
  });
});
