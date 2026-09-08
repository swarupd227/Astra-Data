/**
 * The Regression Monitor, S7.7.1, closing F7.7/E7.
 *
 * Tested through what each role sees and can do: every released workbook with its own
 * schedule/last result/drift alert; "Schedule" gated to the Programme Manager; "Export for
 * handover" gated to any Artizent role.
 */

import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { ApiError, type Identity } from '../lib/api';
import { RegressionMonitor } from '../regression/RegressionMonitor';
import {
  fakeApi,
  regressionMonitorResponse,
  regressionMonitorRow,
  regressionScheduleRecord,
} from './fixtures';

const PM: Identity = { principal: 'user:pm@artizent.example', roles: ['programme_manager'] };
const ENGINEER: Identity = {
  principal: 'user:engineer@artizent.example',
  roles: ['migration_engineer'],
};
const REPORT_OWNER: Identity = {
  principal: 'user:owner@client.example',
  roles: ['client_report_owner'],
};

function renderMonitor(identity: Identity = PM, api = fakeApi()) {
  return { api, ...render(<RegressionMonitor api={api} identity={identity} />) };
}

describe('the listing', () => {
  it('shows a released workbook with its own schedule, last result and drift alert', async () => {
    const api = fakeApi();
    api.regressionMonitor = async () =>
      regressionMonitorResponse({
        workbooks: [
          regressionMonitorRow({
            workbook_id: 'wb_1',
            workbook_name: 'Daily VaR',
            last_result: 'FAIL',
            drift_alert: { unaddressed: true, last_drift_at: '2027-06-05T00:00:00.000Z' },
          }),
        ],
      });
    render(<RegressionMonitor api={api} identity={PM} />);

    const row = await screen.findByText('Daily VaR');
    const tr = row.closest('tr')!;
    expect(within(tr).getByText('every 10080 minutes')).toBeInTheDocument();
    expect(within(tr).getByText('FAIL')).toBeInTheDocument();
    expect(within(tr).getByText('unaddressed')).toBeInTheDocument();
  });

  it('shows "not scheduled" for a released workbook with no schedule', async () => {
    const api = fakeApi();
    api.regressionMonitor = async () =>
      regressionMonitorResponse({
        workbooks: [regressionMonitorRow({ workbook_id: 'wb_1', schedule: null, last_result: null })],
      });
    render(<RegressionMonitor api={api} identity={PM} />);

    expect(await screen.findByText('not scheduled')).toBeInTheDocument();
    expect(await screen.findByText('never run')).toBeInTheDocument();
  });

  it('says nothing has been released yet when the list is empty', async () => {
    const api = fakeApi();
    api.regressionMonitor = async () => regressionMonitorResponse({ workbooks: [] });
    renderMonitor(PM, api);

    expect(await screen.findByText(/No workbook has been released yet/)).toBeInTheDocument();
  });

  it('surfaces a read failure', async () => {
    const api = fakeApi();
    api.regressionMonitor = async () => {
      throw new ApiError(503, 'unavailable', 'the Regression Monitor is not available on this deployment');
    };
    render(<RegressionMonitor api={api} identity={PM} />);

    expect(
      await screen.findByText(/the Regression Monitor is not available/),
    ).toBeInTheDocument();
  });
});

describe('scheduling', () => {
  it('lets a programme manager schedule an unscheduled workbook', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.regressionMonitor = async () =>
      regressionMonitorResponse({
        workbooks: [regressionMonitorRow({ workbook_id: 'wb_1', schedule: null, last_result: null })],
      });
    let scheduled = false;
    api.scheduleRegression = async (workbookId) => {
      scheduled = true;
      return regressionScheduleRecord({ workbook_id: workbookId });
    };
    render(<RegressionMonitor api={api} identity={PM} />);

    const button = await screen.findByRole('button', { name: 'Schedule' });
    await user.click(button);

    expect(await screen.findByText(/Scheduled wb_1 for regression/)).toBeInTheDocument();
    expect(scheduled).toBe(true);
  });

  it('hides the Schedule action for a non-programme-manager', async () => {
    const api = fakeApi();
    api.regressionMonitor = async () =>
      regressionMonitorResponse({
        workbooks: [regressionMonitorRow({ workbook_id: 'wb_1', schedule: null, last_result: null })],
      });
    render(<RegressionMonitor api={api} identity={ENGINEER} />);

    await screen.findByText('not scheduled');
    expect(screen.queryByRole('button', { name: 'Schedule' })).not.toBeInTheDocument();
  });

  it('shows a forbidden message if the API refuses the schedule call', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.regressionMonitor = async () =>
      regressionMonitorResponse({
        workbooks: [regressionMonitorRow({ workbook_id: 'wb_1', schedule: null, last_result: null })],
      });
    api.scheduleRegression = async () => {
      throw new ApiError(403, 'forbidden', 'scheduling regression is the Programme Manager\'s action');
    };
    render(<RegressionMonitor api={api} identity={PM} />);

    await user.click(await screen.findByRole('button', { name: 'Schedule' }));

    expect(
      await screen.findByText(/Scheduling regression is the Programme Manager/),
    ).toBeInTheDocument();
  });
});

describe('export for handover', () => {
  it('lets an Artizent role export a suite and shows the artefact id', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.regressionMonitor = async () =>
      regressionMonitorResponse({ workbooks: [regressionMonitorRow({ workbook_id: 'wb_1' })] });
    api.exportRegressionSuite = async (workbookId) => ({
      id: 'af_export_1', kind: 'regression_export', mu_ref: workbookId, size_bytes: 2048,
    });
    render(<RegressionMonitor api={api} identity={ENGINEER} />);

    await user.click(await screen.findByRole('button', { name: 'Export for handover' }));

    expect(await screen.findByText(/Exported wb_1 -- artefact af_export_1/)).toBeInTheDocument();
  });

  it('hides the export action for a client role', async () => {
    const api = fakeApi();
    api.regressionMonitor = async () =>
      regressionMonitorResponse({ workbooks: [regressionMonitorRow({ workbook_id: 'wb_1' })] });
    render(<RegressionMonitor api={api} identity={REPORT_OWNER} />);

    await screen.findByText('Daily VaR');
    expect(screen.queryByRole('button', { name: 'Export for handover' })).not.toBeInTheDocument();
  });
});
