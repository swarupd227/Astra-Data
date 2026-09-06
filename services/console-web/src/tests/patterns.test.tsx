/**
 * The Pattern Library — story S5.5.3.
 *
 * Tested through what a platform engineer sees and does: the queue of candidates awaiting
 * promotion, the full list by class and state with applications/pass/fail/first
 * seen/origin, promoting, retiring with a reason, editing guards into a new version,
 * exporting — and that nobody else gets the governing actions, the same hide-not-disable
 * convention every other role-gated action in this console already follows.
 */

import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { App, surfaceFromPath } from '../App';
import { ApiError, type Identity } from '../lib/api';
import { fakeApi, patternRecord, patternsResponse } from './fixtures';
import { PatternLibrary } from '../patterns/PatternLibrary';

const PLATFORM_ENGINEER: Identity = {
  principal: 'user:p.eng@artizent.example',
  roles: ['platform_engineer'],
};
const OTHER: Identity = {
  principal: 'user:parity@artizent.example',
  roles: ['parity_engineer'],
};

function apiWithPatterns(...overrides: Parameters<typeof patternRecord>[0][]) {
  const rows = (overrides.length > 0 ? overrides : [{}]).map((o) => patternRecord(o));
  return fakeApi(
    undefined, undefined, undefined, undefined, undefined, undefined, undefined,
    undefined, undefined, undefined, undefined, undefined, undefined, undefined, undefined,
    patternsResponse({ patterns: rows }),
  );
}

function renderScreen(identity: Identity = PLATFORM_ENGINEER, api = apiWithPatterns()) {
  return { api, ...render(<PatternLibrary api={api} identity={identity} />) };
}

/** The "All patterns" table is the definitive list — a CANDIDATE also appears in the
 * queue above it, so an unscoped `getByText(id)` is ambiguous the moment a fixture has
 * any candidate. Every test that selects a row does it from here unless it is
 * specifically testing the queue's own, narrower content. Async: the table itself only
 * exists once the initial fetch resolves. */
async function mainTable(): Promise<HTMLElement> {
  return screen.findByRole('table', { name: 'Every pattern' });
}

async function selectPattern(user: ReturnType<typeof userEvent.setup>, id: string): Promise<void> {
  await user.click(await within(await mainTable()).findByText(id));
}

describe('reading the library', () => {
  it('lists a pattern with its class, state, applications, pass/fail, first seen and origin', async () => {
    renderScreen();

    const row = (await within(await mainTable()).findByText('pat_running_sum')).closest('tr')!;
    const cells = within(row).getAllByRole('cell').map((cell) => cell.textContent);
    // Pattern | Class | State | Applications | Pass | Fail | First seen | Origin
    expect(cells).toEqual(['pat_running_sum', 'C3', 'CANDIDATE', '2', '2', '0', 'calc_running_total', 'PROMOTED_FROM_LLM']);
  });

  it('shows a CANDIDATE pattern in the promotion queue', async () => {
    renderScreen();

    const queueHeading = await screen.findByText(/Candidates awaiting promotion/);
    const queueTable = queueHeading.nextElementSibling as HTMLElement;
    expect(within(queueTable).getByText('pat_running_sum')).toBeInTheDocument();
  });

  it('does not list an ACTIVE pattern in the promotion queue', async () => {
    renderScreen(PLATFORM_ENGINEER, apiWithPatterns({ id: 'pat_active', promotion_state: 'ACTIVE' }));

    await screen.findByText('pat_active');
    expect(screen.getByText(/Candidates awaiting promotion \(0\)/)).toBeInTheDocument();
  });

  it('shows every pattern selected in the detail panel, including its guards', async () => {
    const user = userEvent.setup();
    renderScreen();

    await selectPattern(user, 'pat_running_sum');
    expect(screen.getByText('a is real')).toBeInTheDocument();
    expect(screen.getByText('CALCULATE(SUM({a}))')).toBeInTheDocument();
  });

  it('surfaces a read failure', async () => {
    const api = apiWithPatterns();
    api.patterns = async () => {
      throw new ApiError(503, 'unavailable', 'the Pattern Library is not available');
    };
    render(<PatternLibrary api={api} identity={PLATFORM_ENGINEER} />);

    expect(await screen.findByText(/the Pattern Library is not available/)).toBeInTheDocument();
  });
});

describe('promoting', () => {
  it('lets a platform engineer promote a candidate', async () => {
    const user = userEvent.setup();
    const { api } = renderScreen();

    await selectPattern(user, 'pat_running_sum');
    await user.click(screen.getByRole('button', { name: 'Promote' }));

    expect(await screen.findByText(/Promoted pat_running_sum to ACTIVE/)).toBeInTheDocument();
    expect(api.recorded.some((r) => r.kind === 'PROMOTE_PATTERN')).toBe(true);
  });

  it('has no Promote button for an ACTIVE pattern', async () => {
    const user = userEvent.setup();
    renderScreen(PLATFORM_ENGINEER, apiWithPatterns({ id: 'pat_active', promotion_state: 'ACTIVE' }));

    await user.click(await screen.findByText('pat_active'));
    expect(screen.queryByRole('button', { name: 'Promote' })).not.toBeInTheDocument();
  });
});

describe('retiring', () => {
  it('retires a pattern with a reason', async () => {
    const user = userEvent.setup();
    const { api } = renderScreen();

    await selectPattern(user, 'pat_running_sum');
    await user.click(screen.getByRole('button', { name: 'Retire…' }));
    await user.type(screen.getByLabelText('Reason'), 'this pattern is clearly a mistake');
    await user.click(screen.getByRole('button', { name: 'Retire' }));

    expect(await screen.findByText(/Retired pat_running_sum/)).toBeInTheDocument();
    expect(api.recorded.some((r) => r.kind === 'RETIRE_PATTERN')).toBe(true);
  });

  it('requires a reason of a real length before Retire is enabled', async () => {
    const user = userEvent.setup();
    renderScreen();

    await selectPattern(user, 'pat_running_sum');
    await user.click(screen.getByRole('button', { name: 'Retire…' }));
    expect(screen.getByRole('button', { name: 'Retire' })).toBeDisabled();
    await user.type(screen.getByLabelText('Reason'), 'short');
    expect(screen.getByRole('button', { name: 'Retire' })).toBeDisabled();
  });

  it('has no Retire button for an already-RETIRED pattern', async () => {
    const user = userEvent.setup();
    renderScreen(PLATFORM_ENGINEER, apiWithPatterns({ id: 'pat_gone', promotion_state: 'RETIRED' }));

    await user.click(await screen.findByText('pat_gone'));
    expect(screen.queryByRole('button', { name: 'Retire…' })).not.toBeInTheDocument();
  });
});

describe('editing guards', () => {
  it('creates a new version and selects it', async () => {
    const user = userEvent.setup();
    const { api } = renderScreen(PLATFORM_ENGINEER, apiWithPatterns({ id: 'pat_active', promotion_state: 'ACTIVE' }));

    await user.click(await screen.findByText('pat_active'));
    await user.click(screen.getByRole('button', { name: 'Edit guards…' }));
    await user.type(screen.getByLabelText('Reason'), 'clarifying this guard for reviewers');
    await user.click(screen.getByRole('button', { name: 'Save new version' }));

    expect(await screen.findByText(/Created version 2/)).toBeInTheDocument();
    expect(api.recorded.some((r) => r.kind === 'EDIT_PATTERN_GUARDS')).toBe(true);
  });
});

describe('hiding governance from anyone but the Platform Engineer', () => {
  it('hides Promote, Retire and Edit guards, showing the reason instead', async () => {
    const user = userEvent.setup();
    renderScreen(OTHER);

    await selectPattern(user, 'pat_running_sum');
    expect(screen.queryByRole('button', { name: 'Promote' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retire…' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit guards…' })).not.toBeInTheDocument();
    expect(screen.getByText(/is the Platform Engineer/)).toBeInTheDocument();
  });
});

describe('export', () => {
  it('offers an Export button once the library has loaded', async () => {
    renderScreen();
    expect(await screen.findByRole('button', { name: 'Export' })).toBeEnabled();
  });

  it('is not gated to the Platform Engineer role', async () => {
    renderScreen(OTHER);
    expect(await screen.findByRole('button', { name: 'Export' })).toBeEnabled();
  });
});

describe('the shell', () => {
  it('offers Pattern Library as a surface', async () => {
    const user = userEvent.setup();
    render(<App api={apiWithPatterns()} environment="local" initialRole="platform_engineer" />);

    await user.click(screen.getByRole('button', { name: 'Pattern Library' }));

    const table = await screen.findByRole('table', { name: 'Every pattern' });
    expect(await within(table).findByText('pat_running_sum')).toBeInTheDocument();
  });

  it('maps /patterns to patterns', () => {
    expect(surfaceFromPath('/patterns')).toBe('patterns');
  });

  it('offers Platform Engineer as a role', () => {
    render(<App api={apiWithPatterns()} environment="local" />);
    expect(screen.getByRole('option', { name: 'Platform Engineer' })).toBeInTheDocument();
  });
});
