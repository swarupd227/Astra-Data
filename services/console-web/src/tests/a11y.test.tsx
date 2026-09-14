/**
 * WCAG 2.2 AA — story S10.5.1, opening F10.5.
 *
 * The AC's own three named surfaces: gate cards, the Exception Desk, and evidence views.
 * `axe()` (real `axe-core`, via `jest-axe` — see `tests/setup.ts`) is the AC's own
 * "automated axe checks in CI" clause, run here as an ordinary `npm run test` assertion
 * so it rides the existing `console` CI job (`.github/workflows/ci.yml`) rather than
 * needing a second pipeline. "Evidence views" is read as the two real evidence-shaped
 * surfaces this codebase has today: the Exception Desk's own case-detail evidence pane
 * (its own docstring already calls it that), and the Decision Register's evidence
 * bundle panel (S10.4.2) — both checked with their own richer state rendered, not just
 * an empty shell, since a violation hiding behind a conditional render is a real one.
 *
 * **Automated axe checks are not the same claim as a manual screen-reader pass, and this
 * file does not pretend otherwise.** A CLI test environment (jsdom, no OS-level screen
 * reader) cannot drive VoiceOver/NVDA/JAWS. The manual pass this story's own AC also
 * asks for was done against the running console's real accessibility tree (the same
 * tree a screen reader itself consumes — role, accessible name, and reading order — read
 * with the browser's own devtools accessibility inspector), disclosed as that specific,
 * real methodology in ADR 0077 rather than an unverifiable claim of running an actual
 * screen reader inside this environment.
 */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { axe } from 'jest-axe';
import { describe, expect, it } from 'vitest';

import { DecisionRegister } from '../register/DecisionRegister';
import { ExceptionDesk } from '../exceptions/ExceptionDesk';
import { G3Card } from '../g3/G3Card';
import { NotificationPreferences } from '../notifications/NotificationPreferences';
import type { Identity } from '../lib/api';
import {
  decisionEvidenceBundle,
  decisionRegisterItem,
  decisionRegisterResponse,
  exceptionCaseDetail,
  fakeApi,
  g3Card,
} from './fixtures';

const REPORT_OWNER: Identity = { principal: 'user:owner@client.example', roles: ['client_report_owner'] };
const ENGINEER: Identity = { principal: 'user:engineer@artizent.example', roles: ['migration_engineer'] };
const INFOSEC: Identity = { principal: 'user:infosec@client.example', roles: ['client_infosec_reviewer'] };
const PROGRAMME_MANAGER: Identity = { principal: 'user:pm@artizent.example', roles: ['programme_manager'] };

describe('gate cards', () => {
  it('the G3 gate card has no axe violations, loaded and decidable', async () => {
    const api = fakeApi();
    api.g3Card = async (workbookId) => g3Card({ workbook_id: workbookId });
    const { container } = render(
      <G3Card api={api} identity={REPORT_OWNER} initialWorkbookId="01ARZ3NDEKTSV4RRFFQ69G5FAV" />,
    );
    await screen.findByRole('region', { name: 'G3 Gate Card' });

    expect(await axe(container)).toHaveNoViolations();
  });

  it('the G3 gate card has no axe violations for a reader who cannot decide it', async () => {
    const api = fakeApi();
    api.g3Card = async (workbookId) => g3Card({ workbook_id: workbookId });
    const { container } = render(
      <G3Card api={api} identity={ENGINEER} initialWorkbookId="01ARZ3NDEKTSV4RRFFQ69G5FAV" />,
    );
    await screen.findByRole('region', { name: 'G3 Gate Card' });

    expect(await axe(container)).toHaveNoViolations();
  });
});

describe('the Exception Desk', () => {
  it('the queue view has no axe violations', async () => {
    const api = fakeApi();
    const { container } = render(<ExceptionDesk api={api} identity={ENGINEER} />);
    await screen.findByRole('region', { name: 'Exception Desk' });

    expect(await axe(container)).toHaveNoViolations();
  });

  it("a case's own evidence pane has no axe violations (an evidence view)", async () => {
    const api = fakeApi();
    api.exceptionCase = async (caseId) => exceptionCaseDetail({ id: caseId });
    const { container } = render(
      <ExceptionDesk api={api} identity={ENGINEER} initialCaseId="exc_1" />,
    );
    await screen.findByRole('region', { name: 'Case page' });

    expect(await axe(container)).toHaveNoViolations();
  });
});

describe('evidence views', () => {
  it('the Decision Register has no axe violations', async () => {
    const api = fakeApi();
    const { container } = render(<DecisionRegister api={api} identity={INFOSEC} />);
    await screen.findByText('Risk Positions');

    expect(await axe(container)).toHaveNoViolations();
  });

  it("a decision's own evidence bundle panel has no axe violations, with a resolved artefact", async () => {
    const api = fakeApi();
    api.decisionRegister = async () => decisionRegisterResponse({ items: [decisionRegisterItem()] });
    api.decisionEvidence = async () => decisionEvidenceBundle({
      artefact: {
        id: 'art_1', kind: 'g3_card_snapshot', mu_ref: 'wb_1', case_id: '',
        content_hash: 'sha256:abc', media_type: 'application/json', size_bytes: 42,
        width: null, height: null,
        produced_by: { adapter: null, adapter_version: null, interface_version: null },
        recorded_by: 'user:owner@client.example', recorded_at: '2027-06-01T09:00:00.000Z',
      },
    });
    const user = userEvent.setup();
    const { container } = render(<DecisionRegister api={api} identity={INFOSEC} />);
    await screen.findByText('Risk Positions');
    await user.click(screen.getByRole('button', { name: 'Open evidence' }));
    await screen.findByRole('complementary', { name: 'Evidence bundle' });

    expect(await axe(container)).toHaveNoViolations();
  });
});

describe('Notification Preferences (S10.5.2)', () => {
  it('has no axe violations for a client role', async () => {
    const api = fakeApi();
    const { container } = render(<NotificationPreferences api={api} identity={REPORT_OWNER} />);
    await screen.findByText('using the defaults');

    expect(await axe(container)).toHaveNoViolations();
  });

  it('has no axe violations for an Artizent role, with the digest-delivery section shown', async () => {
    const api = fakeApi();
    const { container } = render(<NotificationPreferences api={api} identity={PROGRAMME_MANAGER} />);
    await screen.findByRole('button', { name: 'Send digests now' });

    expect(await axe(container)).toHaveNoViolations();
  });
});
