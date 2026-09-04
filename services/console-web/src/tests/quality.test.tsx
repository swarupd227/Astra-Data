/**
 * The Parse Quality Queue, S1.4.3.
 *
 * Tested through what a platform engineer sees and does: what is holding up how much, in
 * what order, and the three actions per construct.
 */

import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { App, surfaceFromPath } from '../App';
import { ApiError, type Identity } from '../lib/api';
import { ParseQualityQueue } from '../quality/ParseQualityQueue';
import {
  CONSTRUCT,
  RAISED_ISSUE,
  constructsResponse,
  fakeApi,
  queueResponse,
} from './fixtures';

const ENGINEER: Identity = {
  principal: 'user:p.eng@artizent.example',
  roles: ['platform_engineer'],
};

function renderQueue(api = fakeApi()) {
  return { api, ...render(<ParseQualityQueue api={api} identity={ENGINEER} />) };
}

async function selectConstruct(user: ReturnType<typeof userEvent.setup>, text = CONSTRUCT) {
  await user.click(await screen.findByRole('row', { name: new RegExp(escape(text)) }));
}

function escape(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// ------------------------------------------------------------- what it lists

describe('the queue', () => {
  it('lists the workbooks under the threshold', async () => {
    renderQueue();

    const pane = await screen.findByRole('region', { name: 'Held workbooks' });
    expect(within(pane).getByText('Daily VaR')).toBeInTheDocument();
    expect(within(pane).getByText('Weekly VaR')).toBeInTheDocument();
    expect(within(pane).getByText('86%')).toBeInTheDocument();
  });

  it('says how many gaps each held workbook still has', async () => {
    // One remaining construct is nearly free to release; three is not.
    renderQueue();

    const pane = await screen.findByRole('region', { name: 'Held workbooks' });
    expect(within(pane).getByText(/1 unrecognised of 7/)).toBeInTheDocument();
    expect(within(pane).getByText(/3 unrecognised of 8/)).toBeInTheDocument();
  });

  it('groups the unrecognised constructs with their estate-wide frequency', async () => {
    renderQueue();

    const row = await screen.findByRole('row', { name: new RegExp(escape(CONSTRUCT)) });
    expect(within(row).getByText('12')).toBeInTheDocument(); // occurrences
    expect(within(row).getByText('9')).toBeInTheDocument(); // workbooks containing it
    expect(within(row).getByText('rqa, gtaa')).toBeInTheDocument();
  });
});

describe('the release count', () => {
  it('shows how many workbooks fixing each construct would release', async () => {
    // S1.4.3's third criterion, and the number the queue is ordered on.
    renderQueue();

    const row = await screen.findByRole('row', { name: new RegExp(escape(CONSTRUCT)) });
    expect(within(row).getByText('6')).toBeInTheDocument();
  });

  it('orders the queue by it, because that is the working order', async () => {
    renderQueue();

    const table = await screen.findByRole('table');
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows[0]).toHaveTextContent(CONSTRUCT);
    expect(rows[1]).toHaveTextContent('WINDOW_SUM');
  });

  it('totals what the whole queue would release', async () => {
    renderQueue();

    expect(await screen.findByText(/6 workbooks released by fixing them/)).toBeInTheDocument();
  });

  it('explains a construct that releases nothing on its own', async () => {
    const user = userEvent.setup();
    renderQueue();

    await selectConstruct(user, 'WINDOW_SUM(<expr>)');

    expect(
      await screen.findByText(/every workbook holding it has other unrecognised constructs/),
    ).toBeInTheDocument();
  });
});

// ------------------------------------------------------------------ actions

describe('mark ignorable', () => {
  it('asks for a reason and reports what was released', async () => {
    const user = userEvent.setup();
    const { api } = renderQueue();
    await selectConstruct(user);

    await user.click(await screen.findByRole('button', { name: /Mark ignorable…/ }));
    const dialog = await screen.findByRole('dialog');
    const confirm = within(dialog).getByRole('button', { name: 'Accept and re-score' });
    expect(confirm).toBeDisabled();

    await user.type(
      within(dialog).getByLabelText('Reason'),
      'Redesigned per Appendix B; no DAX equivalent',
    );
    await user.click(confirm);

    await waitFor(() =>
      expect(api.recorded).toContainEqual({
        kind: 'IGNORABLE',
        id: CONSTRUCT,
        reason: 'Redesigned per Appendix B; no DAX equivalent',
      }),
    );
    expect(await screen.findByText(/6 workbooks released, 8 re-scored/)).toBeInTheDocument();
  });

  it('says the grammar still cannot read it', async () => {
    // Accepting is a judgement that the platform may proceed anyway, not a fix.
    const user = userEvent.setup();
    renderQueue();
    await selectConstruct(user);
    await user.click(await screen.findByRole('button', { name: /Mark ignorable…/ }));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent(/grammar still cannot read it/);
  });
});

describe('open grammar issue', () => {
  it('raises a ticket with the construct and a description', async () => {
    const user = userEvent.setup();
    const { api } = renderQueue();
    await selectConstruct(user);

    await user.click(await screen.findByRole('button', { name: /Open grammar issue…/ }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('Summary'), 'RAWSQL in a calc');
    await user.type(
      within(dialog).getByLabelText(/What the grammar should do/),
      'Parse it as an opaque expression and classify C4.',
    );
    await user.click(within(dialog).getByRole('button', { name: 'Raise issue' }));

    await waitFor(() =>
      expect(api.recorded.at(-1)).toMatchObject({ kind: 'ISSUE', id: CONSTRUCT }),
    );
  });

  it('says the issue is held locally when no tracker is configured', async () => {
    const user = userEvent.setup();
    renderQueue();
    await selectConstruct(user);

    await user.click(await screen.findByRole('button', { name: /Open grammar issue…/ }));
    const dialog = await screen.findByRole('dialog');
    await user.type(
      within(dialog).getByLabelText(/What the grammar should do/),
      'Parse it as an opaque expression.',
    );
    await user.click(within(dialog).getByRole('button', { name: 'Raise issue' }));

    expect(await screen.findByText(/No work tracker is configured/)).toBeInTheDocument();
  });

  it('will not raise a second issue for a construct already raised', async () => {
    const user = userEvent.setup();
    const raised = constructsResponse();
    raised.constructs[0]!.issue = RAISED_ISSUE;
    renderQueue(fakeApi(undefined, undefined, { constructs: raised }));

    await selectConstruct(user);

    const button = await screen.findByRole('button', { name: /Open grammar issue…/ });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('title', expect.stringContaining('already open'));
  });

  it('marks a raised construct in the table', async () => {
    const raised = constructsResponse();
    raised.constructs[0]!.issue = RAISED_ISSUE;
    renderQueue(fakeApi(undefined, undefined, { constructs: raised }));

    const row = await screen.findByRole('row', { name: new RegExp(escape(CONSTRUCT)) });
    expect(within(row).getByText('open')).toBeInTheDocument();
  });
});

describe('request re-harvest', () => {
  it('re-harvests the site a construct appears in', async () => {
    const user = userEvent.setup();
    const { api } = renderQueue();
    await selectConstruct(user);

    await user.click(await screen.findByRole('button', { name: /Request re-harvest/ }));

    await waitFor(() =>
      expect(api.recorded).toContainEqual({ kind: 'HARVEST', id: 'rqa', reason: '' }),
    );
    expect(await screen.findByText(/re-parsed under the current grammar/)).toBeInTheDocument();
  });

  it('can be started from a held workbook', async () => {
    const user = userEvent.setup();
    const { api } = renderQueue();

    const pane = await screen.findByRole('region', { name: 'Held workbooks' });
    await user.click(within(pane).getAllByRole('button', { name: /Re-harvest site/ })[0]!);

    await waitFor(() =>
      expect(api.recorded).toContainEqual({ kind: 'HARVEST', id: 'rqa', reason: '' }),
    );
  });
});

// --------------------------------------------------------------- empty and error

describe('emptiness and failure', () => {
  it('says nothing is held rather than showing an empty table', async () => {
    renderQueue(
      fakeApi(undefined, undefined, {
        queue: queueResponse({ held: [], count: 0 }),
        constructs: constructsResponse({ constructs: [], count: 0 }),
      }),
    );

    expect(await screen.findByText(/Nothing is held/)).toBeInTheDocument();
    expect(screen.getByText(/parsed above the 98% threshold/)).toBeInTheDocument();
  });

  it('surfaces a read failure', async () => {
    const api = fakeApi();
    api.constructs = async () => {
      throw new ApiError(503, 'unavailable', 'parse-quality records are not available');
    };

    render(<ParseQualityQueue api={api} identity={ENGINEER} />);

    expect(await screen.findByText(/parse-quality records are not available/)).toBeInTheDocument();
  });

  it('shows the API refusal rather than a generic failure', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.failNext(new ApiError(400, 'invalid_request', 'an issue is already open'));
    renderQueue(api);
    await selectConstruct(user);

    await user.click(await screen.findByRole('button', { name: /Open grammar issue…/ }));
    const dialog = await screen.findByRole('dialog');
    await user.type(
      within(dialog).getByLabelText(/What the grammar should do/),
      'Parse it as an opaque expression.',
    );
    await user.click(within(dialog).getByRole('button', { name: 'Raise issue' }));

    expect(await within(dialog).findByText(/already open/)).toBeInTheDocument();
  });
});

describe('the shell', () => {
  it('offers the queue as a surface', async () => {
    const user = userEvent.setup();
    render(<App api={fakeApi()} environment="local" />);

    await user.click(screen.getByRole('button', { name: 'Parse Quality Queue' }));

    expect(
      await screen.findByRole('region', { name: 'Unrecognised constructs' }),
    ).toBeInTheDocument();
  });

  it('maps a path to a screen so any of them can be linked to', () => {
    expect(surfaceFromPath('/quality')).toBe('quality');
    expect(surfaceFromPath('/lineage')).toBe('lineage');
    expect(surfaceFromPath('/')).toBe('estate');
    expect(surfaceFromPath('/nonsense')).toBe('estate');
  });
});
