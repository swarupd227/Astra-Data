/**
 * The Exception Desk, S8.3.1, opening F8.3.
 *
 * Tested through what each role sees and can do: the queue's own columns and filters,
 * bulk assign and every decision gated to the Migration Engineer, the case page's own
 * evidence/artefact/pass-history panes, and each of the four real decisions.
 */

import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { ApiError, type Identity } from '../lib/api';
import { ExceptionDesk } from '../exceptions/ExceptionDesk';
import { exceptionCaseDetail, exceptionQueueEntry, exceptionQueueResponse, fakeApi } from './fixtures';

const ENGINEER: Identity = {
  principal: 'user:engineer@artizent.example',
  roles: ['migration_engineer'],
};
const REPORT_OWNER: Identity = {
  principal: 'user:owner@client.example',
  roles: ['client_report_owner'],
};

describe('a deep link to a case (story S10.1.1)', () => {
  it('auto-loads a case passed in as an initial id', async () => {
    const api = fakeApi();
    api.exceptionQueue = async () => exceptionQueueResponse({ entries: [exceptionQueueEntry({ id: 'exc_1' })] });
    let requested: string | null = null;
    api.exceptionCase = async (id) => {
      requested = id;
      return exceptionCaseDetail({ id });
    };
    render(<ExceptionDesk api={api} identity={ENGINEER} initialCaseId="exc_1" />);

    expect(await screen.findByText('Margin')).toBeInTheDocument();
    expect(requested).toBe('exc_1');
  });

  it('writes the loaded case back into the URL', async () => {
    window.history.replaceState(null, '', '/exceptions');
    const api = fakeApi();
    api.exceptionQueue = async () => exceptionQueueResponse({ entries: [exceptionQueueEntry({ id: 'exc_1' })] });
    api.exceptionCase = async (id) => exceptionCaseDetail({ id });
    const user = userEvent.setup();
    render(<ExceptionDesk api={api} identity={ENGINEER} />);

    await user.click(await screen.findByText('01ARZ3NDEKTSV4RRFFQ69G5FAV'));

    await screen.findByText('Margin');
    expect(window.location.search).toContain('case=exc_1');
  });
});

describe('the queue', () => {
  it('shows an entry with its own AC columns', async () => {
    const api = fakeApi();
    api.exceptionQueue = async () =>
      exceptionQueueResponse({
        entries: [
          exceptionQueueEntry({
            id: 'exc_1', mu_ref: 'wb_1', class: 'AGGREGATION', passes_consumed: 2,
            train_sequence: 3, age_seconds: 7200, assignee: 'a.mehta@artizent.example',
          }),
        ],
      });
    render(<ExceptionDesk api={api} identity={ENGINEER} />);

    const row = (await screen.findByText('wb_1')).closest('tr')!;
    expect(within(row).getByText('AGGREGATION')).toBeInTheDocument();
    expect(within(row).getByText('2')).toBeInTheDocument();
    expect(within(row).getByText('3')).toBeInTheDocument();
    expect(within(row).getByText('2h')).toBeInTheDocument();
    expect(within(row).getByText('a.mehta@artizent.example')).toBeInTheDocument();
  });

  it('shows "unsequenced" for a case whose workbook has never been trained', async () => {
    const api = fakeApi();
    api.exceptionQueue = async () =>
      exceptionQueueResponse({ entries: [exceptionQueueEntry({ train_sequence: null })] });
    render(<ExceptionDesk api={api} identity={ENGINEER} />);

    expect(await screen.findByText('unsequenced')).toBeInTheDocument();
  });

  it('filters by train, class, site and assignee', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    let sentFilters: unknown = null;
    api.exceptionQueue = async (filters) => {
      sentFilters = filters;
      return exceptionQueueResponse({ entries: [] });
    };
    render(<ExceptionDesk api={api} identity={ENGINEER} />);
    await screen.findByText(/No open or blocked exception/);

    await user.type(screen.getByLabelText('Train'), 'trn_one');
    await user.type(screen.getByLabelText('Class'), 'AGGREGATION');
    await user.type(screen.getByLabelText('Site'), 'RQA');
    await user.type(screen.getByLabelText('Assignee'), 'a.mehta@artizent.example');
    await user.click(screen.getByRole('button', { name: 'Filter' }));

    expect(sentFilters).toEqual({
      train: 'trn_one', failureClass: 'AGGREGATION', site: 'RQA', assignee: 'a.mehta@artizent.example',
    });
  });

  it('says nothing matches when the queue is empty', async () => {
    const api = fakeApi();
    api.exceptionQueue = async () => exceptionQueueResponse({ entries: [] });
    render(<ExceptionDesk api={api} identity={ENGINEER} />);

    expect(await screen.findByText(/No open or blocked exception/)).toBeInTheDocument();
  });

  it('surfaces a read failure', async () => {
    const api = fakeApi();
    api.exceptionQueue = async () => {
      throw new ApiError(403, 'forbidden', 'the Exception Desk is open to Artizent or the report owner');
    };
    render(<ExceptionDesk api={api} identity={REPORT_OWNER} />);

    expect(await screen.findByText(/the Exception Desk is open to/)).toBeInTheDocument();
  });
});

describe('bulk assign', () => {
  it('hides selection and the assign bar for a non-migration-engineer', async () => {
    const api = fakeApi();
    api.exceptionQueue = async () => exceptionQueueResponse({ entries: [exceptionQueueEntry()] });
    render(<ExceptionDesk api={api} identity={REPORT_OWNER} />);

    await screen.findByText('01ARZ3NDEKTSV4RRFFQ69G5FAV');
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Bulk assign' })).not.toBeInTheDocument();
  });

  it('lets a migration engineer select cases and bulk assign them', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.exceptionQueue = async () =>
      exceptionQueueResponse({
        entries: [exceptionQueueEntry({ id: 'exc_1' }), exceptionQueueEntry({ id: 'exc_2', mu_ref: 'wb_2' })],
      });
    let assignedIds: string[] = [];
    api.bulkAssignExceptions = async (ids, assignee) => {
      assignedIds = ids;
      return { assignee, updated: ids, count: ids.length };
    };
    render(<ExceptionDesk api={api} identity={ENGINEER} />);

    const checkboxes = await screen.findAllByRole('checkbox');
    await user.click(checkboxes[0]!);
    await user.click(checkboxes[1]!);
    await user.type(screen.getByPlaceholderText('engineer'), 'a.mehta@artizent.example');
    await user.click(screen.getByRole('button', { name: 'Bulk assign' }));

    expect(await screen.findByText(/Assigned 2 case\(s\)/)).toBeInTheDocument();
    expect(assignedIds).toEqual(['exc_1', 'exc_2']);
  });

  it('shows a forbidden message if the API refuses the bulk assign call', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.exceptionQueue = async () => exceptionQueueResponse({ entries: [exceptionQueueEntry()] });
    api.bulkAssignExceptions = async () => {
      throw new ApiError(403, 'forbidden', 'bulk assign is the Migration Engineer\'s action');
    };
    render(<ExceptionDesk api={api} identity={ENGINEER} />);

    await user.click((await screen.findAllByRole('checkbox'))[0]!);
    await user.type(screen.getByPlaceholderText('engineer'), 'someone');
    await user.click(screen.getByRole('button', { name: 'Bulk assign' }));

    expect(await screen.findByText(/Bulk assign is the Migration Engineer/)).toBeInTheDocument();
  });
});

describe('the case page', () => {
  it('shows evidence, artefact and Mender pass history when a row is selected', async () => {
    const api = fakeApi();
    api.exceptionQueue = async () => exceptionQueueResponse({ entries: [exceptionQueueEntry({ id: 'exc_1' })] });
    api.exceptionCase = async (id) => exceptionCaseDetail({ id });
    const user = userEvent.setup();
    render(<ExceptionDesk api={api} identity={ENGINEER} />);

    await user.click(await screen.findByText('01ARZ3NDEKTSV4RRFFQ69G5FAV'));

    expect(await screen.findByText('Margin')).toBeInTheDocument();
    expect(screen.getByText('Margin Calc')).toBeInTheDocument();
    expect(screen.getByText('SUM([WrongField])')).toBeInTheDocument();
    expect(screen.getByText('PATTERN')).toBeInTheDocument();
    expect(screen.getByText('STILL_FAILING')).toBeInTheDocument();
  });

  it('hides the decision form for a non-migration-engineer', async () => {
    const api = fakeApi();
    api.exceptionQueue = async () => exceptionQueueResponse({ entries: [exceptionQueueEntry({ id: 'exc_1' })] });
    api.exceptionCase = async (id) => exceptionCaseDetail({ id });
    const user = userEvent.setup();
    render(<ExceptionDesk api={api} identity={REPORT_OWNER} />);

    await user.click(await screen.findByText('01ARZ3NDEKTSV4RRFFQ69G5FAV'));

    expect(await screen.findByText(/Recording a decision is the Migration Engineer/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Record decision' })).not.toBeInTheDocument();
  });

  it('disables Record decision until the rationale is at least 20 characters', async () => {
    const api = fakeApi();
    api.exceptionQueue = async () => exceptionQueueResponse({ entries: [exceptionQueueEntry({ id: 'exc_1' })] });
    api.exceptionCase = async (id) => exceptionCaseDetail({ id });
    const user = userEvent.setup();
    render(<ExceptionDesk api={api} identity={ENGINEER} />);

    await user.click(await screen.findByText('01ARZ3NDEKTSV4RRFFQ69G5FAV'));
    const button = await screen.findByRole('button', { name: 'Record decision' });
    expect(button).toBeDisabled();

    await user.type(screen.getByLabelText(/Rationale/), 'fixed it');
    expect(button).toBeDisabled();

    await user.clear(screen.getByLabelText(/Rationale/));
    await user.type(screen.getByLabelText(/Rationale/), 'This measure double-counts returns after a schema change.');
    expect(button).toBeEnabled();
  });

  it('records a patch decision and shows the outcome', async () => {
    const api = fakeApi();
    api.exceptionQueue = async () => exceptionQueueResponse({ entries: [exceptionQueueEntry({ id: 'exc_1' })] });
    api.exceptionCase = async (id) => exceptionCaseDetail({ id });
    let patched: [string, string, string, string] | null = null;
    api.patchException = async (id, dax, rationale, workspace) => {
      patched = [id, dax, rationale, workspace];
      return {
        exception_case_id: id, gate_decision_id: 'gd_1', measure_id: 'msr_new',
        outcome: 'closed', cases_reproved: ['case_1'], cases_still_failing: [],
      };
    };
    const user = userEvent.setup();
    render(<ExceptionDesk api={api} identity={ENGINEER} />);

    await user.click(await screen.findByText('01ARZ3NDEKTSV4RRFFQ69G5FAV'));
    await user.type(screen.getByLabelText('New DAX'), 'SUM([[Margin])');
    await user.type(
      screen.getByLabelText(/Rationale/), 'This measure double-counts returns after a schema change.',
    );
    await user.click(await screen.findByRole('button', { name: 'Record decision' }));

    expect(await screen.findByText(/Patched and closed/)).toBeInTheDocument();
    expect(patched).toEqual([
      'exc_1', 'SUM([Margin])', 'This measure double-counts returns after a schema change.', 'dev',
    ]);
  });

  it('records a redesign-to-Desktop decision with its own commit hash', async () => {
    const api = fakeApi();
    api.exceptionQueue = async () => exceptionQueueResponse({ entries: [exceptionQueueEntry({ id: 'exc_1' })] });
    api.exceptionCase = async (id) => exceptionCaseDetail({ id });
    let redesigned: [string, string, string, string | undefined] | null = null;
    api.redesignException = async (id, route, rationale, _identity, desktopCommitHash) => {
      redesigned = [id, route, rationale, desktopCommitHash];
      return { exception_case_id: id, gate_decision_id: 'gd_2', route, detail: { desktop_commit_hash: desktopCommitHash } };
    };
    const user = userEvent.setup();
    render(<ExceptionDesk api={api} identity={ENGINEER} />);

    await user.click(await screen.findByText('01ARZ3NDEKTSV4RRFFQ69G5FAV'));
    await user.selectOptions(screen.getByLabelText('Kind'), 'redesign');
    await user.type(screen.getByLabelText('Desktop commit hash'), 'a1b2c3d');
    await user.type(
      screen.getByLabelText(/Rationale/), 'Class 4 visual; agreed with the report owner to finish in Desktop.',
    );
    await user.click(await screen.findByRole('button', { name: 'Record decision' }));

    expect(await screen.findByText(/closed with Desktop commit a1b2c3d/)).toBeInTheDocument();
    expect(redesigned).toEqual([
      'exc_1', 'desktop', 'Class 4 visual; agreed with the report owner to finish in Desktop.', 'a1b2c3d',
    ]);
  });

  it('records a model-defect decision', async () => {
    const api = fakeApi();
    api.exceptionQueue = async () => exceptionQueueResponse({ entries: [exceptionQueueEntry({ id: 'exc_1' })] });
    api.exceptionCase = async (id) => exceptionCaseDetail({ id });
    api.decideModelDefect = async (id, rationale) => ({
      exception_case_id: id, gate_decision_id: 'gd_3', route_result: { rationale },
    });
    const user = userEvent.setup();
    render(<ExceptionDesk api={api} identity={ENGINEER} />);

    await user.click(await screen.findByText('01ARZ3NDEKTSV4RRFFQ69G5FAV'));
    await user.selectOptions(screen.getByLabelText('Kind'), 'model_defect');
    await user.type(
      screen.getByLabelText(/Rationale/), 'The grain field has no real binding to a dimension member.',
    );
    await user.click(await screen.findByRole('button', { name: 'Record decision' }));

    expect(await screen.findByText(/Model defect recorded/)).toBeInTheDocument();
  });

  it('requires owner sign-off before a fix-with-sign-off source defect can be recorded', async () => {
    const api = fakeApi();
    api.exceptionQueue = async () => exceptionQueueResponse({ entries: [exceptionQueueEntry({ id: 'exc_1' })] });
    api.exceptionCase = async (id) => exceptionCaseDetail({ id });
    let sourceDefect: [string, string, string, string | undefined] | null = null;
    api.decideSourceDefect = async (id, rationale, resolution, _identity, ownerSignOff) => {
      sourceDefect = [id, rationale, resolution, ownerSignOff];
      return {
        exception_case_id: id, gate_decision_id: 'gd_4', resolution, owner_sign_off: ownerSignOff ?? null, notified: true,
      };
    };
    const user = userEvent.setup();
    render(<ExceptionDesk api={api} identity={ENGINEER} />);

    await user.click(await screen.findByText('01ARZ3NDEKTSV4RRFFQ69G5FAV'));
    await user.selectOptions(screen.getByLabelText('Kind'), 'source_defect');
    await user.selectOptions(screen.getByLabelText('Resolution'), 'FIX_WITH_SIGN_OFF');
    await user.type(screen.getByLabelText('Owner sign-off'), 'Approved by owner@client.example on 2027-06-01.');
    await user.type(
      screen.getByLabelText(/Rationale/), 'The Tableau report itself was wrong; fixing with the owner\'s agreement.',
    );
    await user.click(await screen.findByRole('button', { name: 'Record decision' }));

    expect(await screen.findByText(/Source defect recorded and closed/)).toBeInTheDocument();
    expect(sourceDefect).toEqual([
      'exc_1',
      'The Tableau report itself was wrong; fixing with the owner\'s agreement.',
      'FIX_WITH_SIGN_OFF',
      'Approved by owner@client.example on 2027-06-01.',
    ]);
  });

  it('surfaces a decision refusal from the API', async () => {
    const api = fakeApi();
    api.exceptionQueue = async () => exceptionQueueResponse({ entries: [exceptionQueueEntry({ id: 'exc_1' })] });
    api.exceptionCase = async (id) => exceptionCaseDetail({ id });
    api.patchException = async () => {
      throw new ApiError(400, 'invalid_request', 'this DAX does not validate: unexpected token');
    };
    const user = userEvent.setup();
    render(<ExceptionDesk api={api} identity={ENGINEER} />);

    await user.click(await screen.findByText('01ARZ3NDEKTSV4RRFFQ69G5FAV'));
    await user.type(screen.getByLabelText('New DAX'), 'SUM(');
    await user.type(
      screen.getByLabelText(/Rationale/), 'This measure double-counts returns after a schema change.',
    );
    await user.click(await screen.findByRole('button', { name: 'Record decision' }));

    expect(await screen.findByText(/this DAX does not validate/)).toBeInTheDocument();
  });
});
