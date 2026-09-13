/**
 * The Migration Unit page -- story S10.3.1, opening F10.3.
 *
 * Tested through what a migration engineer and a client report owner see: every real
 * section §15.4 names, the role-narrowed response (server-driven, not a console-side
 * filter), and the two lazy fetches (Provenance, an artefact preview) -- see
 * `mu/MigrationUnitPage.tsx`'s own docstring.
 */

import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, type Identity } from '../lib/api';
import { MigrationUnitPage } from '../mu/MigrationUnitPage';
import { fakeApi, muPageResponse } from './fixtures';

const ENGINEER: Identity = { principal: 'user:engineer@artizent.example', roles: ['migration_engineer'] };
const CLIENT_REPORT_OWNER: Identity = { principal: 'user:owner@client.example', roles: ['client_report_owner'] };

async function openPage(api: ReturnType<typeof fakeApi>, identity: Identity) {
  const user = userEvent.setup();
  render(<MigrationUnitPage api={api} identity={identity} />);
  await user.type(screen.getByLabelText('Workbook'), '01ARZ3NDEKTSV4RRFFQ69G5FAV');
  await user.click(screen.getByRole('button', { name: 'Open' }));
  return user;
}

describe('opening the page', () => {
  it('loads by a typed workbook id', async () => {
    const api = fakeApi();
    await openPage(api, ENGINEER);

    expect(await screen.findByRole('heading', { name: 'Daily VaR' })).toBeInTheDocument();
  });

  it('auto-loads from a deep link', async () => {
    const api = fakeApi();
    render(<MigrationUnitPage api={api} identity={ENGINEER} initialWorkbookId="01ARZ3NDEKTSV4RRFFQ69G5FAV" />);

    expect(await screen.findByRole('heading', { name: 'Daily VaR' })).toBeInTheDocument();
  });

  it('surfaces a read failure', async () => {
    const api = fakeApi();
    api.muPage = async () => {
      throw new ApiError(503, 'unavailable', 'the Migration Unit page is not available');
    };
    await openPage(api, ENGINEER);

    expect(await screen.findByText(/the Migration Unit page is not available/)).toBeInTheDocument();
  });
});

describe('header', () => {
  it('shows state, tier, family, train, owner and the gate status strip', async () => {
    const api = fakeApi();
    await openPage(api, ENGINEER);

    const header = await screen.findByRole('region', { name: 'Header' });
    expect(within(header).getByText('RQA / Risk Core')).toBeInTheDocument();
    expect(within(header).getByText('CLUSTERED')).toBeInTheDocument();
    expect(within(header).getByText('MODERATE')).toBeInTheDocument();
    expect(within(header).getByText(/Risk Positions/)).toBeInTheDocument();
    expect(within(header).getByText(/Train 1/)).toBeInTheDocument();
    expect(within(header).getByText('A. Mehta')).toBeInTheDocument();
    const strip = within(header).getByRole('group', { name: 'Gate status strip' });
    expect(within(strip).getByText('G1: APPROVED')).toBeInTheDocument();
    expect(within(strip).getByText('G3: pending')).toBeInTheDocument();
  });

  it('discloses honest absences rather than fabricating a state or a train', async () => {
    const api = fakeApi();
    api.muPage = async (workbookId) =>
      muPageResponse({
        workbook_id: workbookId,
        header: {
          ...muPageResponse().header,
          state: null, tier: null, family: null, train: null, owner: null,
        },
      });
    await openPage(api, ENGINEER);

    const header = await screen.findByRole('region', { name: 'Header' });
    expect(within(header).getByText('not yet in a train')).toBeInTheDocument();
    expect(within(header).getByText('not yet tiered')).toBeInTheDocument();
    expect(within(header).getByText('not yet clustered')).toBeInTheDocument();
    expect(within(header).getByText('not yet sequenced')).toBeInTheDocument();
    expect(within(header).getByText('unassigned')).toBeInTheDocument();
  });
});

describe('source, artefacts and parity', () => {
  it('lists real calculated fields with their class', async () => {
    const api = fakeApi();
    await openPage(api, ENGINEER);

    const pane = await screen.findByRole('region', { name: 'Source' });
    expect(within(pane).getByText('Margin %')).toBeInTheDocument();
    expect(within(pane).getByText('C1')).toBeInTheDocument();
  });

  it('shows the real per-sheet parity verdict grid and the pass statement', async () => {
    const api = fakeApi();
    await openPage(api, ENGINEER);

    const pane = await screen.findByRole('region', { name: 'Parity' });
    expect(within(pane).getByText(/charter 1/)).toBeInTheDocument();
  });

  it('shows measures and git links to an Artizent reader', async () => {
    const api = fakeApi();
    await openPage(api, ENGINEER);

    const pane = await screen.findByRole('region', { name: 'Artefacts' });
    expect(within(pane).getByText(/Margin %/)).toBeInTheDocument();
    expect(within(pane).getByText(/refs\/heads\/main/)).toBeInTheDocument();
  });

  it('hides measures and git links from a client report owner, per the server response', async () => {
    const api = fakeApi();
    await openPage(api, CLIENT_REPORT_OWNER);

    const pane = await screen.findByRole('region', { name: 'Artefacts' });
    expect(within(pane).queryByText('Measures')).not.toBeInTheDocument();
    expect(within(pane).queryByText('Git')).not.toBeInTheDocument();
  });
});

describe('exceptions', () => {
  it('shows open and closed cases with their decisions to an Artizent reader', async () => {
    const api = fakeApi();
    api.muPage = async (workbookId) =>
      muPageResponse({
        workbook_id: workbookId,
        exceptions: {
          cases: [
            { id: 'exc_1', mu_ref: workbookId, class: 'AGGREGATION', state: 'BLOCKED', decisions: [{ decision: 'REDESIGN', approver: null, countersigner: null, timestamp: null, rationale: null }] },
          ],
        },
      });
    await openPage(api, ENGINEER);

    const pane = await screen.findByRole('region', { name: 'Exceptions' });
    expect(within(pane).getByText('AGGREGATION')).toBeInTheDocument();
    expect(within(pane).getByText('REDESIGN')).toBeInTheDocument();
  });

  it('is absent entirely for a client report owner, not merely empty', async () => {
    const api = fakeApi();
    await openPage(api, CLIENT_REPORT_OWNER);

    await screen.findByRole('region', { name: 'Header' });
    expect(screen.queryByRole('region', { name: 'Exceptions' })).not.toBeInTheDocument();
  });
});

describe('gates and timeline', () => {
  it('shows G1/G2/G4 summaries and the real G3 card', async () => {
    const api = fakeApi();
    await openPage(api, ENGINEER);

    const pane = await screen.findByRole('region', { name: 'Gates' });
    expect(within(pane).getAllByText('APPROVED')).toHaveLength(2); // G1 and G2
    expect(within(pane).getByText(/41\/41 parity cases pass/)).toBeInTheDocument();
  });

  it('shows the real timeline of events', async () => {
    const api = fakeApi();
    await openPage(api, ENGINEER);

    const pane = await screen.findByRole('region', { name: 'Timeline' });
    expect(within(pane).getByText('estate.node.upserted')).toBeInTheDocument();
  });
});

describe('provenance (Artizent-only, lazily fetched)', () => {
  it('is not fetched until requested, then shows real records', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    let calls = 0;
    const real = api.muProvenance;
    api.muProvenance = async (...args) => {
      calls += 1;
      return real(...args);
    };
    await openPage(api, ENGINEER);
    await screen.findByRole('region', { name: 'Provenance' });
    expect(calls).toBe(0);

    await user.click(screen.getByRole('button', { name: 'Load provenance' }));

    expect(await screen.findByText(/DETERMINISTIC/)).toBeInTheDocument();
    expect(calls).toBe(1);
  });

  it('is absent entirely for a client report owner', async () => {
    const api = fakeApi();
    await openPage(api, CLIENT_REPORT_OWNER);

    await screen.findByRole('region', { name: 'Header' });
    expect(screen.queryByRole('region', { name: 'Provenance' })).not.toBeInTheDocument();
  });

  it('surfaces a read failure', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.failNext(new ApiError(403, 'forbidden', 'Provenance is an Artizent-only section'));
    await openPage(api, ENGINEER);
    await screen.findByRole('region', { name: 'Provenance' });

    await user.click(screen.getByRole('button', { name: 'Load provenance' }));

    expect(await screen.findByText('Provenance is an Artizent-only section')).toBeInTheDocument();
  });
});

describe('artefact previews (lazy-loaded)', () => {
  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => 'blob:test');
    URL.revokeObjectURL = vi.fn();
  });

  it('fetches the screenshot only once a reader asks to see it', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.muPage = async (workbookId) =>
      muPageResponse({
        workbook_id: workbookId,
        source: {
          ...muPageResponse().source,
          screenshot: {
            id: 'af_1', kind: 'visual_capture', mu_ref: workbookId, case_id: 'sheet1',
            content_hash: 'deadbeef', media_type: 'image/png', size_bytes: 100,
            width: null, height: null, produced_by: { adapter: null, adapter_version: null, interface_version: null },
            recorded_by: 'agent:harvester', recorded_at: '2027-06-01T09:00:00.000Z',
          },
        },
      });
    let calls = 0;
    const real = api.getArtefactContent;
    api.getArtefactContent = async (...args) => {
      calls += 1;
      return real(...args);
    };
    await openPage(api, ENGINEER);
    const pane = await screen.findByRole('region', { name: 'Source' });
    expect(calls).toBe(0);

    await user.click(within(pane).getByRole('button', { name: 'Show screenshot' }));

    expect(await within(pane).findByAltText('screenshot')).toBeInTheDocument();
    expect(calls).toBe(1);
  });
});
