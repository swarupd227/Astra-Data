/**
 * The `Explain` affordance -- story S10.1.2's own "every number on a screen has an
 * 'explain' affordance that opens the query or the events behind it."
 *
 * Tested standalone (not through a screen) since the component is deliberately generic:
 * a trigger that fetches on open, shows the real query/computation text and its source,
 * and -- when given a `subjectId` -- the real recent events for that subject too.
 */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { Explain } from '../components/Explain';
import { ApiError, type Identity } from '../lib/api';
import { explainEntry, fakeApi, subjectEventsResponse } from './fixtures';

const IDENTITY: Identity = { principal: 'user:pm@artizent.example', roles: ['programme_manager'] };

describe('the trigger', () => {
  it('fetches nothing until opened', () => {
    let called = false;
    const api = fakeApi();
    api.explain = async (metricKey) => {
      called = true;
      return explainEntry({ metric_key: metricKey });
    };
    render(<Explain api={api} identity={IDENTITY} metricKey="estate.total" />);

    expect(called).toBe(false);
  });

  it('has an accessible label naming the metric by default', () => {
    render(<Explain api={fakeApi()} identity={IDENTITY} metricKey="estate.total" />);

    expect(screen.getByRole('button', { name: 'Explain estate.total' })).toBeInTheDocument();
  });
});

describe('opening the panel', () => {
  it('shows the real query text and its source', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.explain = async (metricKey) =>
      explainEntry({
        metric_key: metricKey,
        title: 'Estate Explorer — workbook count',
        text: 'total = len(matching)',
        source: 'astra_graph/estate.py:271-283',
      });
    render(<Explain api={api} identity={IDENTITY} metricKey="estate.total" />);

    await user.click(screen.getByRole('button', { name: 'Explain estate.total' }));

    expect(await screen.findByText('Estate Explorer — workbook count')).toBeInTheDocument();
    expect(screen.getByText('total = len(matching)')).toBeInTheDocument();
    expect(screen.getByText('astra_graph/estate.py:271-283')).toBeInTheDocument();
  });

  it('also shows the real events for a subject-scoped figure', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.explain = async (metricKey) => explainEntry({ metric_key: metricKey, subject_kind: 'workbook' });
    let requestedSubject: string | null = null;
    api.subjectEvents = async (subjectId) => {
      requestedSubject = subjectId;
      return subjectEventsResponse({
        events: [
          { sequence: 7, event: { subject: subjectId, type: 'estate.mu.accepted', time: '2027-06-01T09:00:00.000Z' } },
        ],
      });
    };
    render(
      <Explain
        api={api}
        identity={IDENTITY}
        metricKey="g3.parity_cases"
        subjectId="01ARZ3NDEKTSV4RRFFQ69G5FAV"
      />,
    );

    await user.click(screen.getByRole('button', { name: 'Explain g3.parity_cases' }));

    expect(await screen.findByText(/estate\.mu\.accepted/)).toBeInTheDocument();
    expect(requestedSubject).toBe('01ARZ3NDEKTSV4RRFFQ69G5FAV');
  });

  it('discloses honestly when a subject has no recorded events', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.subjectEvents = async () => subjectEventsResponse({ events: [] });
    render(<Explain api={api} identity={IDENTITY} metricKey="estate.total" subjectId="wb_1" />);

    await user.click(screen.getByRole('button', { name: 'Explain estate.total' }));

    expect(await screen.findByText('No events recorded for this subject.')).toBeInTheDocument();
  });

  it('surfaces a read failure rather than hanging silently', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.explain = async () => {
      throw new ApiError(404, 'not_found', "no explain entry for 'unknown.metric'");
    };
    render(<Explain api={api} identity={IDENTITY} metricKey="unknown.metric" />);

    await user.click(screen.getByRole('button', { name: 'Explain unknown.metric' }));

    expect(await screen.findByText(/no explain entry for/)).toBeInTheDocument();
  });

  it('closes and reopens without re-fetching once already loaded', async () => {
    const user = userEvent.setup();
    let calls = 0;
    const api = fakeApi();
    api.explain = async (metricKey) => {
      calls += 1;
      return explainEntry({ metric_key: metricKey });
    };
    render(<Explain api={api} identity={IDENTITY} metricKey="estate.total" />);

    const trigger = screen.getByRole('button', { name: 'Explain estate.total' });
    await user.click(trigger);
    await screen.findByText('Estate Explorer — workbook count');
    await user.click(trigger);
    expect(screen.queryByText('Estate Explorer — workbook count')).not.toBeInTheDocument();
    await user.click(trigger);
    expect(await screen.findByText('Estate Explorer — workbook count')).toBeInTheDocument();

    expect(calls).toBe(1);
  });
});
