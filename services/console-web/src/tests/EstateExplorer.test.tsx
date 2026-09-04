/**
 * The Estate Explorer, S1.4.1.
 *
 * Driven through the rendered screen rather than through the components' props, because
 * what the story asks for is what a programme manager can see and do — three panes, the
 * facets, and the four actions with their role gate.
 */

import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { App } from '../App';
import { EstateExplorer, LOAD_BUDGET_MS } from '../estate/EstateExplorer';
import { ApiError, type Identity } from '../lib/api';
import { HELD, estateResponse, fakeApi, workbook } from './fixtures';

const PM: Identity = { principal: 'user:pm@artizent.example', roles: ['programme_manager'] };
const ENGINEER: Identity = {
  principal: 'user:eng@artizent.example',
  roles: ['migration_engineer'],
};

function renderExplorer(api = fakeApi(), identity: Identity = PM) {
  const rendered = render(<EstateExplorer api={api} identity={identity} />);
  return { api, ...rendered };
}

describe('the three panes', () => {
  it('shows the site and project tree with counts and parse status', async () => {
    renderExplorer();

    const tree = await screen.findByRole('navigation', { name: 'Estate' });
    expect(within(tree).getByRole('button', { name: /RQA/ })).toBeInTheDocument();
    expect(within(tree).getByRole('button', { name: /Risk Core/ })).toBeInTheDocument();
    expect(within(tree).getByRole('button', { name: /Treasury/ })).toBeInTheDocument();
    // The site rolls up both projects.
    expect(within(tree).getByRole('button', { name: /All sites/ })).toBeInTheDocument();
  });

  it('lists workbooks with their parse status, usage and owner', async () => {
    renderExplorer();

    const table = await screen.findByRole('table');
    const rows = within(table).getAllByRole('row');
    expect(rows).toHaveLength(3); // header + two workbooks

    const held = within(table).getByRole('row', { name: /Liquidity Ladder/ });
    expect(within(held).getByText(/83%/)).toBeInTheDocument();
    expect(within(held).getByText(/held/)).toBeInTheDocument();
  });

  it('shows the selected workbook with a lineage mini-graph', async () => {
    const user = userEvent.setup();
    renderExplorer();

    await user.click(await screen.findByRole('row', { name: /Daily VaR/ }));

    const panel = await screen.findByRole('region', { name: 'Selected workbook' });
    expect(await within(panel).findByRole('img', { name: /Lineage of Daily VaR/ })).toBeInTheDocument();
    expect(within(panel).getByText('wb-daily-var')).toBeInTheDocument();
  });

  it('says nothing is selected rather than showing an empty panel', async () => {
    renderExplorer();

    const panel = await screen.findByRole('region', { name: 'Selected workbook' });
    expect(within(panel).getByText(/Select a workbook/)).toBeInTheDocument();
  });
});

describe('faceted filters', () => {
  it('offers the facets the estate can answer, with counts', async () => {
    renderExplorer();

    expect(await screen.findByRole('button', { name: /under 90%\s*1/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /A Mehta\s*1/ })).toBeInTheDocument();
  });

  it('sends a facet to the API when picked, and clears it when picked again', async () => {
    const user = userEvent.setup();
    const { api } = renderExplorer();

    const option = await screen.findByRole('button', { name: /under 90%/ });
    await user.click(option);
    await waitFor(() =>
      expect(api.calls.estate.at(-1)?.parse_quality_band).toBe('poor'),
    );

    await user.click(screen.getByRole('button', { name: /under 90%/ }));
    await waitFor(() => expect(api.calls.estate.at(-1)?.parse_quality_band).toBeNull());
  });

  it('names the facets that cannot exist yet instead of showing empty dropdowns', async () => {
    renderExplorer();

    expect(await screen.findByText(/Not available yet/)).toBeInTheDocument();
    expect(screen.getByText(/Migration Unit state machine/)).toBeInTheDocument();
  });

  it('filters by tree selection', async () => {
    const user = userEvent.setup();
    const { api } = renderExplorer();

    await user.click(await screen.findByRole('button', { name: /Treasury/ }));

    await waitFor(() => {
      expect(api.calls.estate.at(-1)?.site).toBe('RQA');
      expect(api.calls.estate.at(-1)?.project).toBe('Treasury');
    });
  });

  it('searches', async () => {
    const user = userEvent.setup();
    const { api } = renderExplorer();

    await user.type(await screen.findByRole('searchbox'), 'liquid');

    await waitFor(() => expect(api.calls.estate.at(-1)?.search).toBe('liquid'));
  });

  it('clears every filter at once, and says how many are active', async () => {
    const user = userEvent.setup();
    const { api } = renderExplorer();

    await user.click(await screen.findByRole('button', { name: /under 90%/ }));
    const clear = await screen.findByRole('button', { name: /Clear filters \(1\)/ });
    await user.click(clear);

    await waitFor(() => expect(api.calls.estate.at(-1)?.parse_quality_band).toBeNull());
  });
});

describe('a filtered view is shareable', () => {
  it('puts the filters in the URL', async () => {
    const user = userEvent.setup();
    renderExplorer();

    await user.click(await screen.findByRole('button', { name: /under 90%/ }));

    await waitFor(() =>
      expect(window.location.search).toContain('parse_quality_band=poor'),
    );
  });

  it('restores them from the URL', async () => {
    window.history.replaceState(null, '', '/?parse_quality_band=poor&unowned_only=true');
    const { api } = renderExplorer();

    await waitFor(() => {
      expect(api.calls.estate[0]?.parse_quality_band).toBe('poor');
      expect(api.calls.estate[0]?.unowned_only).toBe(true);
    });
  });
});

describe('actions', () => {
  it('requires a reason before a scope decision can be recorded', async () => {
    const user = userEvent.setup();
    const { api } = renderExplorer();

    await user.click(await screen.findByRole('row', { name: /Daily VaR/ }));
    await user.click(await screen.findByRole('button', { name: /Withdraw…/ }));

    const dialog = await screen.findByRole('dialog');
    const confirm = within(dialog).getByRole('button', { name: 'Withdraw' });
    expect(confirm).toBeDisabled();

    await user.type(within(dialog).getByLabelText('Reason'), 'Report retires in Q3');
    expect(confirm).toBeEnabled();

    await user.click(confirm);
    await waitFor(() =>
      expect(api.recorded).toContainEqual({
        kind: 'WITHDRAW',
        id: '01ARZ3NDEKTSV4RRFFQ69G5FAV',
        reason: 'Report retires in Q3',
      }),
    );
  });

  it('records a tier with the reason', async () => {
    const user = userEvent.setup();
    const { api } = renderExplorer();

    await user.click(await screen.findByRole('row', { name: /Daily VaR/ }));
    await user.click(await screen.findByRole('button', { name: /Re-tier…/ }));

    const dialog = await screen.findByRole('dialog');
    await user.selectOptions(within(dialog).getByLabelText('Tier'), 'COMPLEX');
    await user.type(within(dialog).getByLabelText('Reason'), 'Joint review found nested LODs');
    await user.click(within(dialog).getByRole('button', { name: 'Record tier' }));

    await waitFor(() =>
      expect(api.recorded.at(-1)).toMatchObject({ kind: 'RE_TIER', tier: 'COMPLEX' }),
    );
  });

  it('disables the scope actions for anyone who is not the Programme Manager', async () => {
    const user = userEvent.setup();
    renderExplorer(fakeApi(), ENGINEER);

    await user.click(await screen.findByRole('row', { name: /Daily VaR/ }));

    expect(await screen.findByRole('button', { name: /Re-tier…/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Withdraw…/ })).toBeDisabled();
    expect(screen.getByText(/Programme Manager’s/)).toBeInTheDocument();
  });

  it('disables Open MU and says what would create one', async () => {
    const user = userEvent.setup();
    renderExplorer();

    await user.click(await screen.findByRole('row', { name: /Daily VaR/ }));

    const open = await screen.findByRole('button', { name: 'Open MU' });
    expect(open).toBeDisabled();
    await waitFor(() => expect(open).toHaveAttribute('title', expect.stringContaining('E3')));
  });

  it('starts a re-harvest of the selected workbook’s site', async () => {
    const user = userEvent.setup();
    const { api } = renderExplorer();

    await user.click(await screen.findByRole('row', { name: /Daily VaR/ }));
    await user.click(await screen.findByRole('button', { name: /Re-harvest site/ }));

    await waitFor(() =>
      expect(api.recorded).toContainEqual({ kind: 'HARVEST', id: 'RQA', reason: '' }),
    );
    expect(await screen.findByText(/Harvest 01M1HARVEST started/)).toBeInTheDocument();
  });

  it('shows the API’s refusal rather than a generic failure', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.failNext(new ApiError(403, 'forbidden', 'changing programme scope is the Programme Manager’s'));
    renderExplorer(api);

    await user.click(await screen.findByRole('row', { name: /Daily VaR/ }));
    await user.click(await screen.findByRole('button', { name: /Withdraw…/ }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('Reason'), 'Superseded by Treasury pack');
    await user.click(within(dialog).getByRole('button', { name: 'Withdraw' }));

    expect(await within(dialog).findByText(/Programme Manager/)).toBeInTheDocument();
  });

  it('closes the dialog on Escape without recording anything', async () => {
    const user = userEvent.setup();
    const { api } = renderExplorer();

    await user.click(await screen.findByRole('row', { name: /Daily VaR/ }));
    await user.click(await screen.findByRole('button', { name: /Withdraw…/ }));
    await screen.findByRole('dialog');
    await user.keyboard('{Escape}');

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(api.recorded).toHaveLength(0);
  });
});

describe('the load budget', () => {
  it('reports how long the read took', async () => {
    renderExplorer();

    expect(await screen.findByText(/ms in the graph/)).toBeInTheDocument();
  });

  it('states the budget in one place so the screen and the story cannot drift', () => {
    expect(LOAD_BUDGET_MS).toBe(2000);
  });
});

describe('failure', () => {
  it('says the estate could not be read rather than showing an empty screen', async () => {
    const api = fakeApi();
    api.estate = async () => {
      throw new ApiError(503, 'unavailable', 'graph store is not ready');
    };

    render(<EstateExplorer api={api} identity={PM} />);

    expect(await screen.findByText(/graph store is not ready/)).toBeInTheDocument();
  });

  it('shows an empty table as an explained state, not a blank pane', async () => {
    renderExplorer(fakeApi(estateResponse({ workbooks: [], total: 0 })));

    expect(await screen.findByText(/No workbooks match these filters/)).toBeInTheDocument();
  });
});

describe('withdrawn workbooks', () => {
  it('marks them and offers reinstate instead of withdraw', async () => {
    const user = userEvent.setup();
    const withdrawn = workbook({ withdrawn: true, withdrawn_reason: 'Retiring in Q3' });
    renderExplorer(fakeApi(estateResponse({ workbooks: [withdrawn, HELD] })));

    await user.click(await screen.findByRole('row', { name: /Daily VaR/ }));

    expect(await screen.findByRole('button', { name: /Reinstate…/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Withdraw…/ })).not.toBeInTheDocument();
  });
});

describe('the shell', () => {
  it('says plainly that nobody is signed in', () => {
    render(<App api={fakeApi()} environment="local" />);

    expect(screen.getByText('not signed in')).toBeInTheDocument();
    expect(screen.getByLabelText(/Acting as/)).toBeInTheDocument();
  });

  it('marks the environment', () => {
    render(<App api={fakeApi()} environment="test" />);

    expect(screen.getByText('test')).toBeInTheDocument();
  });
});
