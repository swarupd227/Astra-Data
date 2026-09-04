/**
 * The Model Detail screen, S4.1.2.
 *
 * Tested through what a Semantic Model Engineer sees and does: pick a family, generate or
 * read its design, edit the grain statement / a table's storage mode / a relationship's
 * cardinality while DRAFT, accept a family, and submit it for G2 — plus the role gate and
 * the "pending" honesty on the Measures tab (no Transpiler yet).
 */

import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { App, surfaceFromPath } from '../App';
import type { Identity } from '../lib/api';
import { ModelDetail } from '../modeller/ModelDetail';
import {
  buildRecord,
  designDocument,
  familiesResponse,
  familyRecord,
  familyTransition,
  fakeApi,
  modelVersion,
} from './fixtures';

const SME: Identity = { principal: 'user:sme@artizent.example', roles: ['semantic_model_engineer'] };
const PM: Identity = { principal: 'user:pm@artizent.example', roles: ['programme_manager'] };

function renderScreen(identity: Identity = SME, api = fakeApi()) {
  return { api, ...render(<ModelDetail api={api} identity={identity} />) };
}

function withFamilies(...overrides: Parameters<typeof familyRecord>[0][]) {
  return familiesResponse({ families: overrides.map((o) => familyRecord(o)) });
}

describe('the family list', () => {
  it('shows every family with its state, and selects the first by default', async () => {
    renderScreen(
      SME,
      fakeApi(
        undefined, undefined, undefined, undefined, undefined, undefined,
        withFamilies({ id: 'fam_one', name: 'Risk Positions', state: 'PROPOSED' }, { id: 'fam_two', name: 'Liquidity', state: 'DRAFT' }),
      ),
    );

    const list = await screen.findByRole('region', { name: 'Model families' });
    expect(within(list).getByText('Risk Positions')).toBeInTheDocument();
    expect(within(list).getByText('Liquidity')).toBeInTheDocument();
    expect(await screen.findByRole('region', { name: /Model Detail — Risk Positions/ })).toBeInTheDocument();
  });

  it('switches the detail panel when another family is picked', async () => {
    const user = userEvent.setup();
    renderScreen(
      SME,
      fakeApi(
        undefined, undefined, undefined, undefined, undefined, undefined,
        withFamilies({ id: 'fam_one', name: 'Risk Positions' }, { id: 'fam_two', name: 'Liquidity' }),
      ),
    );

    await screen.findByRole('region', { name: /Risk Positions/ });
    await user.click(screen.getByText('Liquidity'));

    expect(await screen.findByRole('region', { name: /Model Detail — Liquidity/ })).toBeInTheDocument();
  });

  it('says no families exist rather than an empty screen', async () => {
    renderScreen(SME, fakeApi(undefined, undefined, undefined, undefined, undefined, undefined, { families: [], count: 0 }));

    expect(await screen.findByText(/No families yet/)).toBeInTheDocument();
  });
});

describe('generating and accepting a proposal', () => {
  it('offers to generate a proposal when none exists yet', async () => {
    renderScreen(
      SME,
      fakeApi(undefined, undefined, undefined, undefined, undefined, undefined, withFamilies({ id: 'fam_one', state: 'PROPOSED' })),
    );

    expect(await screen.findByText(/No design proposal has been generated/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Generate proposal' })).toBeInTheDocument();
  });

  it('generates a proposal, then offers to accept it', async () => {
    const user = userEvent.setup();
    const { api } = renderScreen(
      SME,
      fakeApi(undefined, undefined, undefined, undefined, undefined, undefined, withFamilies({ id: 'fam_one', state: 'PROPOSED' })),
    );

    await user.click(await screen.findByRole('button', { name: 'Generate proposal' }));

    expect(api.recorded).toContainEqual({ kind: 'PROPOSE_DESIGN', id: 'fam_one', reason: '' });
    expect(await screen.findByRole('button', { name: 'Accept' })).toBeInTheDocument();
  });

  it('accepts a family into DRAFT', async () => {
    const user = userEvent.setup();
    const { api } = renderScreen(
      SME,
      fakeApi(
        undefined, undefined, undefined, undefined, undefined, undefined,
        withFamilies({ id: 'fam_one', state: 'PROPOSED' }),
        { fam_one: designDocument({ family_id: 'fam_one' }) },
      ),
    );

    await user.click(await screen.findByRole('button', { name: 'Accept' }));

    expect(api.recorded).toContainEqual({ kind: 'ACCEPT_FAMILY', id: 'fam_one', reason: '' });
    expect(await screen.findByText('Family accepted into DRAFT.')).toBeInTheDocument();
  });

  it('is not offered to a non Semantic Model Engineer', async () => {
    renderScreen(
      PM,
      fakeApi(undefined, undefined, undefined, undefined, undefined, undefined, withFamilies({ id: 'fam_one', state: 'PROPOSED' })),
    );

    await screen.findByRole('region', { name: /Model Detail/ });
    expect(screen.queryByRole('button', { name: 'Generate proposal' })).not.toBeInTheDocument();
    expect(screen.getByText(/Editing a design is the Semantic Model Engineer/)).toBeInTheDocument();
  });
});

describe('the Design tab', () => {
  function draftFamily() {
    return fakeApi(
      undefined, undefined, undefined, undefined, undefined, undefined,
      withFamilies({ id: 'fam_one', state: 'DRAFT' }),
      { fam_one: designDocument({ family_id: 'fam_one' }) },
    );
  }

  it('shows tables, relationships and the grain statement', async () => {
    renderScreen(SME, draftFamily());

    // "positions"/"desk" each appear more than once (the tables table, the relationships
    // table, and the SVG diagram all name the same tables) — presence, not uniqueness, is
    // the point here.
    expect((await screen.findAllByText('positions')).length).toBeGreaterThan(0);
    expect(screen.getAllByText('desk').length).toBeGreaterThan(0);
    expect(screen.getByText('One row per Desk and Trade Date.')).toBeInTheDocument();
  });

  it('edits the grain statement while DRAFT', async () => {
    const user = userEvent.setup();
    const { api } = renderScreen(SME, draftFamily());

    const field = await screen.findByLabelText('Grain statement');
    await user.clear(field);
    await user.type(field, 'One row per Desk.');
    await user.click(screen.getByRole('button', { name: 'Save' }));

    expect(api.recorded).toContainEqual({ kind: 'EDIT_GRAIN_STATEMENT', id: 'fam_one', reason: '' });
  });

  it('overrides a table storage mode while DRAFT', async () => {
    const user = userEvent.setup();
    const { api } = renderScreen(SME, draftFamily());

    await screen.findByText('One row per Desk and Trade Date.');
    const select = screen.getByLabelText('Storage mode for positions');
    await user.selectOptions(select, 'directlake');

    expect(api.recorded).toContainEqual({ kind: 'SET_TABLE_MODE', id: 'mt_positions', reason: '' });
  });

  it('overrides a relationship cardinality while DRAFT', async () => {
    const user = userEvent.setup();
    const { api } = renderScreen(SME, draftFamily());

    const select = await screen.findByLabelText('Cardinality for desk to positions');
    await user.selectOptions(select, 'many_to_one');

    expect(api.recorded).toContainEqual({ kind: 'SET_RELATIONSHIP_CARDINALITY', id: 'fam_one', reason: '' });
  });

  it('shows editable controls only while DRAFT', async () => {
    renderScreen(
      SME,
      fakeApi(
        undefined, undefined, undefined, undefined, undefined, undefined,
        withFamilies({ id: 'fam_one', state: 'IN_REVIEW' }),
        { fam_one: designDocument({ family_id: 'fam_one', version: 'sha256:abc' }) },
      ),
    );

    await screen.findByText('One row per Desk and Trade Date.');
    expect(screen.queryByLabelText('Storage mode for positions')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Grain statement')).not.toBeInTheDocument();
  });
});

describe('submitting for G2', () => {
  it('submits a DRAFT design and shows the frozen version', async () => {
    const user = userEvent.setup();
    const { api } = renderScreen(
      SME,
      fakeApi(
        undefined, undefined, undefined, undefined, undefined, undefined,
        withFamilies({ id: 'fam_one', state: 'DRAFT' }),
        { fam_one: designDocument({ family_id: 'fam_one' }) },
      ),
    );

    await user.click(await screen.findByRole('button', { name: 'Submit for G2' }));

    expect(api.recorded).toContainEqual({ kind: 'SUBMIT_FOR_REVIEW', id: 'fam_one', reason: '' });
    expect(await screen.findByText(/Submitted for G2 — version/)).toBeInTheDocument();
  });
});

describe('the Measures tab', () => {
  it('shows class and pattern as pending — no Transpiler yet', async () => {
    const user = userEvent.setup();
    renderScreen(
      SME,
      fakeApi(
        undefined, undefined, undefined, undefined, undefined, undefined,
        withFamilies({ id: 'fam_one', state: 'DRAFT' }),
        { fam_one: designDocument({ family_id: 'fam_one' }) },
      ),
    );

    await screen.findByText('One row per Desk and Trade Date.');
    await user.click(screen.getByRole('button', { name: 'Measures' }));

    expect(await screen.findByText('Margin %')).toBeInTheDocument();
    expect(screen.getAllByText('pending')).toHaveLength(2);
  });
});

describe('the Open Questions tab', () => {
  it('shows an open question with its category', async () => {
    const user = userEvent.setup();
    renderScreen(
      SME,
      fakeApi(
        undefined, undefined, undefined, undefined, undefined, undefined,
        withFamilies({ id: 'fam_one', state: 'DRAFT' }),
        {
          fam_one: designDocument({
            family_id: 'fam_one',
            open_questions: [
              { category: 'ambiguous_key', question: 'table is sourced from custom SQL', evidence: {} },
            ],
          }),
        },
      ),
    );

    await screen.findByText('One row per Desk and Trade Date.');
    await user.click(screen.getByRole('button', { name: 'Open Questions' }));

    expect(await screen.findByText('ambiguous_key')).toBeInTheDocument();
    expect(screen.getByText(/custom SQL/)).toBeInTheDocument();
  });
});

describe('the Build tab', () => {
  it('shows the current state and the transition history', async () => {
    const user = userEvent.setup();
    const api = fakeApi(
      undefined, undefined, undefined, undefined, undefined, undefined,
      withFamilies({ id: 'fam_one', state: 'DRAFT' }),
      { fam_one: designDocument({ family_id: 'fam_one' }) },
    );
    const originalTransitions = api.familyTransitions.bind(api);
    api.familyTransitions = async (familyId, identity) => {
      const response = await originalTransitions(familyId, identity);
      return {
        family_id: familyId,
        transitions: [
          familyTransition({ from_state: null, to_state: 'PROPOSED', by: 'agent:cartographer' }),
          familyTransition({ from_state: 'PROPOSED', to_state: 'DRAFT', by: SME.principal }),
          ...response.transitions.filter((t) => t.to_state !== 'PROPOSED'),
        ],
      };
    };
    renderScreen(SME, api);

    await screen.findByText('One row per Desk and Trade Date.');
    await user.click(screen.getByRole('button', { name: 'Build' }));

    expect(await screen.findByText(/builds automatically the moment it is approved/)).toBeInTheDocument();
    expect(screen.getByText(/created → PROPOSED/)).toBeInTheDocument();
    expect(screen.getByText(/PROPOSED → DRAFT/)).toBeInTheDocument();
  });

  it('offers "Build now" once approved, and shows a successful build log', async () => {
    const user = userEvent.setup();
    const api = fakeApi(
      undefined, undefined, undefined, undefined, undefined, undefined,
      withFamilies({ id: 'fam_one', state: 'APPROVED' }),
      { fam_one: designDocument({ family_id: 'fam_one' }) },
    );
    renderScreen(SME, api);

    await screen.findByText('One row per Desk and Trade Date.');
    await user.click(screen.getByRole('button', { name: 'Build' }));
    expect(await screen.findByText(/No build has run yet/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Build now' }));

    expect(await screen.findByText('Build succeeded.')).toBeInTheDocument();
    expect(screen.getByText('SUCCEEDED')).toBeInTheDocument();
    expect(screen.getByText('emit')).toBeInTheDocument();
    expect(screen.getByText('commit')).toBeInTheDocument();
    expect(screen.getByText('deploy')).toBeInTheDocument();
  });

  it('renders a stored build log for an already-built family, with a Rebuild action', async () => {
    const api = fakeApi(
      undefined, undefined, undefined, undefined, undefined, undefined,
      withFamilies({ id: 'fam_one', state: 'BUILT' }),
      { fam_one: designDocument({ family_id: 'fam_one' }) },
      {},
      undefined,
      { fam_one: buildRecord({ family_id: 'fam_one' }) },
    );
    const user = userEvent.setup();
    renderScreen(SME, api);

    await screen.findByText('One row per Desk and Trade Date.');
    await user.click(screen.getByRole('button', { name: 'Build' }));

    expect(await screen.findByText('a1b2c3d')).toBeInTheDocument();
    expect(screen.getByText('dev')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Rebuild' })).toBeInTheDocument();
  });

  it('shows a failed step with its own detail, and does not silently move the family on', async () => {
    const api = fakeApi(
      undefined, undefined, undefined, undefined, undefined, undefined,
      withFamilies({ id: 'fam_one', state: 'APPROVED' }),
      { fam_one: designDocument({ family_id: 'fam_one' }) },
      {},
      undefined,
      {
        fam_one: buildRecord({
          family_id: 'fam_one',
          state: 'FAILED',
          steps: [
            { name: 'emit', ok: true, detail: '3 file(s) emitted' },
            { name: 'commit', ok: false, detail: "git commit failed: could not lock repository" },
          ],
          git_commit_sha: null,
        }),
      },
    );
    const user = userEvent.setup();
    renderScreen(SME, api);

    await screen.findByText('One row per Desk and Trade Date.');
    await user.click(screen.getByRole('button', { name: 'Build' }));

    expect(await screen.findByText('FAILED')).toBeInTheDocument();
    expect(screen.getByText(/could not lock repository/)).toBeInTheDocument();
  });

  it('lists a conformance violation with its offending object, and shows the checked ruleset version', async () => {
    const api = fakeApi(
      undefined, undefined, undefined, undefined, undefined, undefined,
      withFamilies({ id: 'fam_one', state: 'APPROVED', conformance_ruleset_version: 3 }),
      { fam_one: designDocument({ family_id: 'fam_one' }) },
      {},
      undefined,
      {
        fam_one: buildRecord({
          family_id: 'fam_one',
          state: 'FAILED',
          steps: [
            { name: 'emit', ok: true, detail: '3 file(s) emitted' },
            {
              name: 'conformance', ok: false,
              detail: "positions: table name starts with a digit; Margin %: duplicates the name of another measure",
            },
          ],
          git_commit_sha: null,
        }),
      },
    );
    renderScreen(SME, api);

    await screen.findByText('One row per Desk and Trade Date.');
    await userEvent.setup().click(screen.getByRole('button', { name: 'Build' }));

    expect(await screen.findByText(/checked against conformance ruleset version 3/)).toBeInTheDocument();
    expect(screen.getByText(/positions: table name starts with a digit/)).toBeInTheDocument();
  });

  it('hides "Build now" from anyone but the Semantic Model Engineer', async () => {
    const user = userEvent.setup();
    const api = fakeApi(
      undefined, undefined, undefined, undefined, undefined, undefined,
      withFamilies({ id: 'fam_one', state: 'APPROVED' }),
      { fam_one: designDocument({ family_id: 'fam_one' }) },
    );
    renderScreen(PM, api);

    await screen.findByText('One row per Desk and Trade Date.');
    await user.click(screen.getByRole('button', { name: 'Build' }));

    expect(await screen.findByText(/No build has run yet/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Build now' })).not.toBeInTheDocument();
  });
});

describe('the Versions tab', () => {
  it('shows version history, newest first', async () => {
    const user = userEvent.setup();
    const api = fakeApi(
      undefined, undefined, undefined, undefined, undefined, undefined,
      withFamilies({ id: 'fam_one', state: 'PUBLISHED' }),
      { fam_one: designDocument({ family_id: 'fam_one', version_number: 2, state: 'PUBLISHED' }) },
      {}, undefined, {}, undefined,
      {
        fam_one: [
          modelVersion({
            semantic_model_id: 'sem_v1', version_number: 1, state: 'DEPRECATED',
            published_at: '2027-04-01T00:00:00.000Z', deprecated_at: '2027-05-01T00:00:00.000Z',
          }),
          modelVersion({
            semantic_model_id: 'sem_v2', version_number: 2, state: 'PUBLISHED',
            published_at: '2027-05-01T00:00:00.000Z',
          }),
        ],
      },
    );
    renderScreen(SME, api);

    await screen.findByText('One row per Desk and Trade Date.');
    await user.click(screen.getByRole('button', { name: 'Versions' }));

    expect(await screen.findByText('v2')).toBeInTheDocument();
    expect(screen.getByText('v1')).toBeInTheDocument();
  });

  it('offers "Request new version" only while PUBLISHED, and opens a DRAFT v(n+1)', async () => {
    const user = userEvent.setup();
    const { api } = renderScreen(
      SME,
      fakeApi(
        undefined, undefined, undefined, undefined, undefined, undefined,
        withFamilies({ id: 'fam_one', state: 'PUBLISHED' }),
        { fam_one: designDocument({ family_id: 'fam_one', version_number: 1, state: 'PUBLISHED' }) },
      ),
    );

    await screen.findByText('One row per Desk and Trade Date.');
    await user.click(screen.getByRole('button', { name: 'Versions' }));

    const reasonField = await screen.findByLabelText('Change request reason');
    await user.type(reasonField, 'Mender repair for a broken relationship.');
    await user.click(screen.getByRole('button', { name: 'Request new version' }));

    expect(api.recorded).toContainEqual({
      kind: 'REQUEST_NEW_VERSION', id: 'fam_one', reason: 'Mender repair for a broken relationship.',
    });
    expect(await screen.findByText(/v2 is now DRAFT/)).toBeInTheDocument();
  });

  it('disables "Request new version" until the reason is long enough', async () => {
    const user = userEvent.setup();
    renderScreen(
      SME,
      fakeApi(
        undefined, undefined, undefined, undefined, undefined, undefined,
        withFamilies({ id: 'fam_one', state: 'PUBLISHED' }),
        { fam_one: designDocument({ family_id: 'fam_one', version_number: 1, state: 'PUBLISHED' }) },
      ),
    );

    await screen.findByText('One row per Desk and Trade Date.');
    await user.click(screen.getByRole('button', { name: 'Versions' }));

    const reasonField = await screen.findByLabelText('Change request reason');
    await user.type(reasonField, 'too short');

    expect(screen.getByRole('button', { name: 'Request new version' })).toBeDisabled();
  });

  it('does not offer "Request new version" unless the family is PUBLISHED', async () => {
    const user = userEvent.setup();
    renderScreen(
      SME,
      fakeApi(
        undefined, undefined, undefined, undefined, undefined, undefined,
        withFamilies({ id: 'fam_one', state: 'DRAFT' }),
        { fam_one: designDocument({ family_id: 'fam_one' }) },
      ),
    );

    await screen.findByText('One row per Desk and Trade Date.');
    await user.click(screen.getByRole('button', { name: 'Versions' }));

    expect(screen.queryByLabelText('Change request reason')).not.toBeInTheDocument();
  });

  it('offers "Promote" only while BUILT, and deprecates the predecessor with the date', async () => {
    const user = userEvent.setup();
    const { api } = renderScreen(
      SME,
      fakeApi(
        undefined, undefined, undefined, undefined, undefined, undefined,
        withFamilies({ id: 'fam_one', state: 'BUILT' }),
        { fam_one: designDocument({ family_id: 'fam_one', version_number: 2, state: 'DRAFT' }) },
        {}, undefined, {}, undefined,
        {
          fam_one: [
            modelVersion({
              semantic_model_id: 'sem_v1', version_number: 1, state: 'PUBLISHED',
              published_at: '2027-04-01T00:00:00.000Z',
            }),
            modelVersion({ semantic_model_id: 'sem_v2', version_number: 2, state: 'BUILT', published_at: null }),
          ],
        },
      ),
    );

    await screen.findByText('One row per Desk and Trade Date.');
    await user.click(screen.getByRole('button', { name: 'Versions' }));
    await user.click(screen.getByRole('button', { name: 'Promote to PUBLISHED' }));

    expect(api.recorded).toContainEqual({ kind: 'PROMOTE', id: 'fam_one', reason: '' });
    expect(await screen.findByText(/v1 is now DEPRECATED/)).toBeInTheDocument();
  });

  it('does not offer "Promote" unless the family is BUILT', async () => {
    const user = userEvent.setup();
    renderScreen(
      SME,
      fakeApi(
        undefined, undefined, undefined, undefined, undefined, undefined,
        withFamilies({ id: 'fam_one', state: 'DRAFT' }),
        { fam_one: designDocument({ family_id: 'fam_one' }) },
      ),
    );

    await screen.findByText('One row per Desk and Trade Date.');
    await user.click(screen.getByRole('button', { name: 'Versions' }));

    expect(screen.queryByRole('button', { name: 'Promote to PUBLISHED' })).not.toBeInTheDocument();
  });

  it('hides both version actions from anyone but the Semantic Model Engineer', async () => {
    const user = userEvent.setup();
    renderScreen(
      PM,
      fakeApi(
        undefined, undefined, undefined, undefined, undefined, undefined,
        withFamilies({ id: 'fam_one', state: 'PUBLISHED' }),
        { fam_one: designDocument({ family_id: 'fam_one', version_number: 1, state: 'PUBLISHED' }) },
      ),
    );

    await screen.findByText('One row per Desk and Trade Date.');
    await user.click(screen.getByRole('button', { name: 'Versions' }));

    expect(screen.queryByLabelText('Change request reason')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Promote to PUBLISHED' })).not.toBeInTheDocument();
  });
});

describe('the shell', () => {
  it('offers Model Detail as a surface', async () => {
    const user = userEvent.setup();
    render(<App api={fakeApi()} environment="local" />);

    await user.click(screen.getByRole('button', { name: 'Model Detail' }));

    expect(await screen.findByText('Families')).toBeInTheDocument();
  });

  it('maps /models to Model Detail', () => {
    expect(surfaceFromPath('/models')).toBe('models');
  });
});
