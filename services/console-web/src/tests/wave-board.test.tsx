/**
 * The Wave Board, S3.2.2.
 *
 * Tested through what a Programme Manager sees and does: trains as columns, cards
 * grouped by state, drag to re-sequence or move, a WIP-exceeded move prompting for a
 * reason, and the role gate on every write.
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { App, surfaceFromPath } from '../App';
import { ApiError, type Identity } from '../lib/api';
import { WaveBoard } from '../trains/WaveBoard';
import {
  fakeApi,
  trainMember,
  trainProjection,
  trainProjectionsResponse,
  trainRecord,
  trainsResponse,
} from './fixtures';

const PM: Identity = { principal: 'user:pm@artizent.example', roles: ['programme_manager'] };
const ENGINEER: Identity = {
  principal: 'user:engineer@artizent.example',
  roles: ['migration_engineer'],
};

function renderBoard(identity: Identity = PM, api = fakeApi()) {
  return { api, ...render(<WaveBoard api={api} identity={identity} />) };
}

function drag(card: HTMLElement, target: HTMLElement): void {
  fireEvent.dragStart(card);
  fireEvent.dragOver(target);
  fireEvent.drop(target);
}

describe('the board', () => {
  it('shows every train as a column with its members', async () => {
    renderBoard();

    expect(await screen.findByRole('region', { name: 'Train 1' })).toBeInTheDocument();
    const train1 = screen.getByRole('region', { name: 'Train 1' });
    expect(within(train1).getByText('Daily VaR')).toBeInTheDocument();
    expect(within(train1).getByText('Weekly VaR')).toBeInTheDocument();
  });

  it('groups cards by their MU state', async () => {
    renderBoard(
      PM,
      fakeApi(
        undefined,
        undefined,
        undefined,
        undefined,
        trainsResponse({
          trains: [
            trainRecord({
              id: 'trn_one',
              name: 'Train 1',
              size: 2,
              members: [
                trainMember({ id: 'a', name: 'Alpha', state: 'CLUSTERED', sequence: 1 }),
                trainMember({ id: 'b', name: 'Beta', state: 'MODEL_READY', sequence: 2 }),
              ],
            }),
          ],
        }),
      ),
    );

    expect(await screen.findByText('clustered')).toBeInTheDocument();
    expect(screen.getByText('model_ready')).toBeInTheDocument();
  });

  it('shows a projected-date badge per train, from S3.2.3 (story S3.2.3)', async () => {
    const projections = trainProjectionsResponse({
      projections: [
        trainProjection({
          train_id: 'trn_one',
          train_name: 'Train 1',
          projected_end: '2027-02-15',
          flagged: true,
          days_late: 11,
          reason: 'bottleneck is PROVING',
        }),
        trainProjection({ train_id: 'trn_two', train_name: 'Train 2' }),
      ],
    });
    renderBoard(PM, fakeApi(undefined, undefined, undefined, undefined, undefined, projections));

    const train1 = await screen.findByRole('region', { name: 'Train 1' });
    expect(within(train1).getByText('proj. 2027-02-15')).toBeInTheDocument();
    const train2 = screen.getByRole('region', { name: 'Train 2' });
    expect(within(train2).getByText('no projection')).toBeInTheDocument();
  });

  it('shows a WIP pill when a limit is configured', async () => {
    renderBoard(
      PM,
      fakeApi(
        undefined,
        undefined,
        undefined,
        undefined,
        trainsResponse({
          trains: [
            trainRecord({
              id: 'trn_one',
              name: 'Train 1',
              size: 2,
              members: [trainMember({ id: 'a' }), trainMember({ id: 'b', sequence: 2 })],
              wip_limits: { train: 5, states: {} },
            }),
          ],
        }),
      ),
    );

    expect(await screen.findByText('WIP 2/5')).toBeInTheDocument();
  });
});

describe('dragging', () => {
  it('moves a card into a different train', async () => {
    const { api } = renderBoard();

    const card = await screen.findByText('Daily VaR');
    const train2 = screen.getByRole('region', { name: 'Train 2' });

    fireEvent.dragStart(card);
    fireEvent.dragOver(train2);
    fireEvent.drop(train2);

    await waitFor(() => {
      expect(api.recorded).toContainEqual({ kind: 'MOVE_MEMBER', id: 'wb1', reason: '' });
    });
    expect(
      await within(screen.getByRole('region', { name: 'Train 2' })).findByText('Daily VaR'),
    ).toBeInTheDocument();
  });

  it('resequences a card dropped on another card in the same train', async () => {
    const { api } = renderBoard();

    const first = await screen.findByText('Daily VaR');
    const second = screen.getByText('Weekly VaR');

    drag(second, first);

    expect(api.recorded).toContainEqual({ kind: 'RESEQUENCE_MEMBER', id: 'wb2', reason: '' });
  });

  it('is not offered to a non Programme Manager', async () => {
    renderBoard(ENGINEER);

    const card = await screen.findByText('Daily VaR');
    expect(card).toHaveAttribute('draggable', 'false');
    expect(
      screen.getByText(/Moving and resequencing is the Programme Manager/),
    ).toBeInTheDocument();
  });
});

describe('WIP-exceeded moves', () => {
  it('prompts for a reason and resubmits the same move with it', async () => {
    const user = userEvent.setup();
    const { api } = renderBoard(
      PM,
      fakeApi(
        undefined,
        undefined,
        undefined,
        undefined,
        trainsResponse({
          trains: [
            trainRecord({
              id: 'trn_one',
              name: 'Train 1',
              size: 1,
              members: [trainMember({ id: 'a', name: 'Alpha' })],
            }),
            trainRecord({
              id: 'trn_two',
              name: 'Train 2',
              size: 1,
              members: [trainMember({ id: 'b', name: 'Beta' })],
              wip_limits: { train: 1, states: {} },
            }),
          ],
        }),
      ),
    );

    const card = await screen.findByText('Alpha');
    const train2 = screen.getByRole('region', { name: 'Train 2' });
    drag(card, train2);

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent(/WIP limit/);
    const confirm = within(dialog).getByRole('button', { name: /Move anyway/ });
    expect(confirm).toBeDisabled();

    await user.type(within(dialog).getByLabelText(/Reason/), 'client escalation, prioritise now');
    await user.click(confirm);

    expect(api.recorded).toContainEqual({
      kind: 'MOVE_MEMBER',
      id: 'a',
      reason: 'client escalation, prioritise now',
    });
  });
});

describe('WIP limits dialog', () => {
  it('saves a train-level limit with a reason', async () => {
    const user = userEvent.setup();
    const { api } = renderBoard();

    await user.click((await screen.findAllByRole('button', { name: 'WIP limit…' }))[0]!);
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('Train limit'), '10');
    await user.type(within(dialog).getByLabelText('Reason'), 'agreed cadence with the client');
    await user.click(within(dialog).getByRole('button', { name: 'Save limits' }));

    expect(api.recorded).toContainEqual({
      kind: 'SET_WIP_LIMITS',
      id: 'trn_one',
      reason: 'agreed cadence with the client',
    });
  });
});

describe('activity', () => {
  it('shows recent events for a train', async () => {
    const user = userEvent.setup();
    renderBoard();

    await user.click((await screen.findAllByRole('button', { name: 'Activity' }))[0]!);

    expect(await screen.findByText(/astra.data.node.upserted/)).toBeInTheDocument();
  });
});

describe('emptiness and failure', () => {
  it('says no trains exist rather than showing an empty board', async () => {
    renderBoard(PM, fakeApi(undefined, undefined, undefined, undefined, trainsResponse({ trains: [] })));

    expect(await screen.findByText(/No release trains yet/)).toBeInTheDocument();
  });

  it('surfaces a read failure', async () => {
    const api = fakeApi();
    api.trains = async () => {
      throw new ApiError(503, 'unavailable', 'trains are not available');
    };

    render(<WaveBoard api={api} identity={PM} />);

    expect(await screen.findByText(/trains are not available/)).toBeInTheDocument();
  });
});

describe('the shell', () => {
  it('offers the Wave Board as a surface', async () => {
    const user = userEvent.setup();
    render(<App api={fakeApi()} environment="local" />);

    await user.click(screen.getByRole('button', { name: 'Wave Board' }));

    expect(await screen.findByRole('region', { name: 'Train 1' })).toBeInTheDocument();
  });

  it('maps /trains to the Wave Board', () => {
    expect(surfaceFromPath('/trains')).toBe('trains');
  });
});
