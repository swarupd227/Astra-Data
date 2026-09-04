/**
 * The Programme Board's family-count figure, S3.1.3.
 *
 * Tested through what a Programme Manager sees and does: planned against measured with the
 * delta, and a "Confirm family count" action gated to their role.
 */

import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { App, surfaceFromPath } from '../App';
import { ApiError, type Identity } from '../lib/api';
import { ProgrammeBoard } from '../programme/ProgrammeBoard';
import {
  awaitingG2Response,
  awaitingG2Review,
  classMix,
  fakeApi,
  programmesResponse,
  ruleCoverage,
  trainProjection,
  trainProjectionsResponse,
} from './fixtures';

const PM: Identity = { principal: 'user:pm@artizent.example', roles: ['programme_manager'] };
const ENGINEER: Identity = {
  principal: 'user:engineer@artizent.example',
  roles: ['migration_engineer'],
};
const PARITY_ENGINEER: Identity = {
  principal: 'user:parity@artizent.example',
  roles: ['parity_engineer'],
};
const PLATFORM_ENGINEER: Identity = {
  principal: 'user:platform@artizent.example',
  roles: ['platform_engineer'],
};

function fakeApiWithClassMix(mix: ReturnType<typeof classMix>) {
  return fakeApi(
    undefined, undefined, undefined, undefined, undefined, undefined, undefined,
    undefined, undefined, undefined, undefined, undefined, undefined, mix,
  );
}

function fakeApiWithRuleCoverage(coverage: ReturnType<typeof ruleCoverage>) {
  return fakeApi(
    undefined, undefined, undefined, undefined, undefined, undefined, undefined,
    undefined, undefined, undefined, undefined, undefined, undefined, undefined, coverage,
  );
}

function renderBoard(identity: Identity = PM, api = fakeApi()) {
  return { api, ...render(<ProgrammeBoard api={api} identity={identity} />) };
}

describe('the figures', () => {
  it('shows the planned figure against an unconfirmed measured one', async () => {
    renderBoard();

    const pane = await screen.findByRole('region', { name: 'Family count calibration' });
    expect(within(pane).getByText('150')).toBeInTheDocument();
    expect(within(pane).getByText('not yet confirmed')).toBeInTheDocument();
  });

  it('says no programme is open yet when none is', async () => {
    renderBoard(PM, fakeApi(undefined, undefined, undefined, programmesResponse({ programmes: [] })));

    expect(await screen.findByText(/No programme is open yet/)).toBeInTheDocument();
  });

  it('surfaces a read failure', async () => {
    const api = fakeApi();
    api.programmes = async () => {
      throw new ApiError(503, 'unavailable', 'the programme record is not available');
    };

    render(<ProgrammeBoard api={api} identity={PM} />);

    expect(
      await screen.findByText(/the programme record is not available/),
    ).toBeInTheDocument();
  });
});

describe('confirm family count', () => {
  it('records the confirmation and shows the measured value against the delta', async () => {
    const user = userEvent.setup();
    const { api } = renderBoard(PM);

    await user.click(await screen.findByRole('button', { name: 'Confirm family count' }));

    expect(api.recorded).toContainEqual({
      kind: 'CONFIRM_FAMILY_COUNT',
      id: 'prg_01M1RQA',
      reason: '',
    });
    expect(await screen.findByText(/Confirmed 142 families/)).toBeInTheDocument();
    expect(await screen.findByText('142')).toBeInTheDocument();
    expect(await screen.findByText('-8')).toBeInTheDocument();
  });

  it('hides the action from anyone but the Programme Manager', async () => {
    renderBoard(ENGINEER);

    expect(
      screen.queryByRole('button', { name: 'Confirm family count' }),
    ).not.toBeInTheDocument();
    expect(
      await screen.findByText(/Confirming the family count is the Programme Manager/),
    ).toBeInTheDocument();
  });

  it('shows the API refusal rather than a generic failure', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.failNext(new ApiError(403, 'forbidden', 'programme manager only'));
    renderBoard(PM, api);

    await user.click(await screen.findByRole('button', { name: 'Confirm family count' }));

    expect(
      await screen.findByText(/Confirming the family count is the Programme Manager/),
    ).toBeInTheDocument();
  });
});

describe('projected vs. planned (S3.2.3)', () => {
  it('reports "insufficient data" honestly rather than a fabricated date', async () => {
    renderBoard(PM, fakeApi(undefined, undefined, undefined, undefined, undefined, trainProjectionsResponse()));

    const pane = await screen.findByRole('region', {
      name: 'Projected versus planned dates per train',
    });
    expect(within(pane).getAllByText('insufficient data')).toHaveLength(2);
    expect(within(pane).getByText('0 at risk')).toBeInTheDocument();
  });

  it('flags a train projected to miss its planned date by more than 5 working days', async () => {
    const projections = trainProjectionsResponse({
      projections: [
        trainProjection({
          train_id: 'trn_one',
          train_name: 'Train 1',
          planned_end: '2027-01-31',
          bottleneck_state: 'PROVING',
          remaining_in_bottleneck: 4,
          projected_end: '2027-02-15',
          projected_end_early: '2027-02-12',
          projected_end_late: '2027-02-19',
          days_late: 11,
          flagged: true,
          reason: 'bottleneck is PROVING (4 MUs remaining there, 1.00/day measured over the trailing 14 days)',
        }),
      ],
    });
    renderBoard(PM, fakeApi(undefined, undefined, undefined, undefined, undefined, projections));

    const pane = await screen.findByRole('region', {
      name: 'Projected versus planned dates per train',
    });
    expect(within(pane).getByText('1 at risk')).toBeInTheDocument();
    expect(within(pane).getByText('11 working days late')).toBeInTheDocument();
    expect(within(pane).getByText('2027-02-15')).toBeInTheDocument();
    expect(within(pane).getByText('2027-02-12 – 2027-02-19')).toBeInTheDocument();
  });

  it('surfaces a read failure', async () => {
    const api = fakeApi();
    api.trainProjections = async () => {
      throw new ApiError(503, 'unavailable', 'projections are not available');
    };

    render(<ProgrammeBoard api={api} identity={PM} />);

    expect(await screen.findByText(/projections are not available/)).toBeInTheDocument();
  });
});

describe('G2 cycle time (S4.2.2)', () => {
  it('says no family is awaiting G2 when none is', async () => {
    renderBoard();

    const pane = await screen.findByRole('region', { name: 'G2 cycle time' });
    expect(within(pane).getByText(/No family is awaiting G2 review/)).toBeInTheDocument();
  });

  it('shows days waiting and the approver, and highlights an SLA breach', async () => {
    const reviews = awaitingG2Response({
      reviews: [
        awaitingG2Review({
          family_id: 'fam_one', name: 'Risk Positions', days_waiting: 2, breached: false,
        }),
        awaitingG2Review({
          family_id: 'fam_two', name: 'Liquidity Ladder', approver: null, days_waiting: 7,
          breached: true, open_questions: 0,
        }),
      ],
    });
    renderBoard(
      PM,
      fakeApi(undefined, undefined, undefined, undefined, undefined, undefined, undefined, undefined, undefined, reviews),
    );

    const pane = await screen.findByRole('region', { name: 'G2 cycle time' });
    expect(within(pane).getByText('owner@client.example')).toBeInTheDocument();
    expect(within(pane).getByText('unassigned')).toBeInTheDocument();
    expect(within(pane).getByText('over SLA')).toBeInTheDocument();
    expect(within(pane).getByText('within SLA')).toBeInTheDocument();
    expect(within(pane).getByText('1 over SLA')).toBeInTheDocument();
  });

  it('surfaces a read failure', async () => {
    const api = fakeApi();
    api.awaitingG2 = async () => {
      throw new ApiError(503, 'unavailable', 'families awaiting G2 are not available');
    };

    render(<ProgrammeBoard api={api} identity={PM} />);

    expect(await screen.findByText(/families awaiting G2 are not available/)).toBeInTheDocument();
  });

  it('sends due reminders and reports none due on a second click', async () => {
    const user = userEvent.setup();
    const reviews = awaitingG2Response({
      reviews: [awaitingG2Review({ family_id: 'fam_one', days_waiting: 5 })],
    });
    const { api } = renderBoard(
      PM,
      fakeApi(undefined, undefined, undefined, undefined, undefined, undefined, undefined, undefined, undefined, reviews),
    );

    await screen.findByRole('region', { name: 'G2 cycle time' });
    await user.click(screen.getByRole('button', { name: 'Send reminders' }));

    expect(await screen.findByText('Sent 2 reminders.')).toBeInTheDocument();
    expect(api.recorded).toContainEqual({ kind: 'SEND_G2_REMINDERS', id: '', reason: '' });

    await user.click(screen.getByRole('button', { name: 'Send reminders' }));
    expect(await screen.findByText(/No reminders were due/)).toBeInTheDocument();
  });
});

describe('calculation class mix (S5.1.1)', () => {
  it('shows every class against its calibration target, and how many fields are classified', async () => {
    renderBoard(
      PM,
      fakeApiWithClassMix(
        classMix({
          total: 20, unclassified: 5,
          counts: { C1: 9, C2: 4, C3: 1, C4: 1 },
          percentages: { C1: 60, C2: 27, C3: 6, C4: 7 },
          classifier_version: 3,
        }),
      ),
    );

    const pane = await screen.findByRole('region', { name: 'Calculation class mix' });
    expect(within(pane).getByText('15 of 20 classified')).toBeInTheDocument();
    expect(within(pane).getByText('60%')).toBeInTheDocument();
    expect(within(pane).getByText('45%')).toBeInTheDocument();
    expect(within(pane).getByText('ruleset version 3')).toBeInTheDocument();
  });

  it('says nothing has been harvested yet when the estate has no calculated fields', async () => {
    renderBoard(PM, fakeApiWithClassMix(classMix({ total: 0, unclassified: 0 })));

    const pane = await screen.findByRole('region', { name: 'Calculation class mix' });
    expect(within(pane).getByText(/No calculated fields have been harvested yet/)).toBeInTheDocument();
  });

  it('re-classifies and reports how many fields moved class', async () => {
    const user = userEvent.setup();
    const { api } = renderBoard(PARITY_ENGINEER, fakeApiWithClassMix(classMix({ total: 20, unclassified: 20 })));

    await screen.findByRole('region', { name: 'Calculation class mix' });
    await user.click(screen.getByRole('button', { name: 'Re-classify' }));

    expect(api.recorded).toContainEqual({ kind: 'RECLASSIFY', id: '', reason: '' });
    expect(await screen.findByText(/Re-classified 20 fields — 1 moved class/)).toBeInTheDocument();
  });

  it('hides the re-classify action from anyone but the parity engineer', async () => {
    renderBoard(PM, fakeApiWithClassMix(classMix()));

    await screen.findByRole('region', { name: 'Calculation class mix' });
    expect(screen.queryByRole('button', { name: 'Re-classify' })).not.toBeInTheDocument();
    expect(screen.getByText(/Re-classifying is the parity engineer/)).toBeInTheDocument();
  });

  it('surfaces a read failure', async () => {
    const api = fakeApi();
    api.classMix = async () => {
      throw new ApiError(503, 'unavailable', 'class mix is not available');
    };

    render(<ProgrammeBoard api={api} identity={PM} />);

    expect(await screen.findByText(/class mix is not available/)).toBeInTheDocument();
  });
});

describe('rule coverage (S5.2.1)', () => {
  it('shows how many fields are converted, by rule family', async () => {
    renderBoard(
      PM,
      fakeApiWithRuleCoverage(
        ruleCoverage({
          total: 20, matched: 9, percentage: 45,
          by_family: { aggregate: 6, operator: 2, logical: 1 },
          rules_version: 1,
        }),
      ),
    );

    const pane = await screen.findByRole('region', { name: 'Rule coverage' });
    expect(within(pane).getByText('9 of 20 converted')).toBeInTheDocument();
    expect(within(pane).getByText('aggregate')).toBeInTheDocument();
    expect(within(pane).getByText('6')).toBeInTheDocument();
    expect(within(pane).getByText(/45% covered · rules version 1/)).toBeInTheDocument();
  });

  it('says nothing has been harvested yet when the estate has no calculated fields', async () => {
    renderBoard(PM, fakeApiWithRuleCoverage(ruleCoverage({ total: 0, matched: 0 })));

    const pane = await screen.findByRole('region', { name: 'Rule coverage' });
    expect(within(pane).getByText(/No calculated fields have been harvested yet/)).toBeInTheDocument();
  });

  it('says nothing has been converted yet when no field has matched a rule', async () => {
    renderBoard(PM, fakeApiWithRuleCoverage(ruleCoverage({ total: 20, matched: 0, by_family: {} })));

    const pane = await screen.findByRole('region', { name: 'Rule coverage' });
    expect(within(pane).getByText(/No calculated field has been converted by a rule yet/)).toBeInTheDocument();
  });

  it('applies rules and reports how many fields were newly converted', async () => {
    const user = userEvent.setup();
    const { api } = renderBoard(
      PLATFORM_ENGINEER,
      fakeApiWithRuleCoverage(ruleCoverage({ total: 20, matched: 0, by_family: {} })),
    );

    await screen.findByRole('region', { name: 'Rule coverage' });
    await user.click(screen.getByRole('button', { name: 'Apply rules' }));

    expect(api.recorded).toContainEqual({ kind: 'APPLY_RULES', id: '', reason: '' });
    expect(await screen.findByText(/Applied rules to 20 fields — 1 newly converted/)).toBeInTheDocument();
  });

  it('hides the apply-rules action from anyone but the platform engineer', async () => {
    renderBoard(PM, fakeApiWithRuleCoverage(ruleCoverage()));

    await screen.findByRole('region', { name: 'Rule coverage' });
    expect(screen.queryByRole('button', { name: 'Apply rules' })).not.toBeInTheDocument();
    expect(screen.getByText(/Applying the rules engine is the platform engineer/)).toBeInTheDocument();
  });

  it('surfaces a read failure', async () => {
    const api = fakeApi();
    api.ruleCoverage = async () => {
      throw new ApiError(503, 'unavailable', 'rule coverage is not available');
    };

    render(<ProgrammeBoard api={api} identity={PM} />);

    expect(await screen.findByText(/rule coverage is not available/)).toBeInTheDocument();
  });
});

describe('the shell', () => {
  it('offers the Programme Board as a surface', async () => {
    const user = userEvent.setup();
    render(<App api={fakeApi()} environment="local" />);

    await user.click(screen.getByRole('button', { name: 'Programme Board' }));

    expect(
      await screen.findByRole('region', { name: 'Family count calibration' }),
    ).toBeInTheDocument();
  });

  it('maps /programme to the Programme Board', () => {
    expect(surfaceFromPath('/programme')).toBe('programme');
  });
});
