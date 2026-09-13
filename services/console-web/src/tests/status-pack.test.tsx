/**
 * The Status Pack -- story S10.2.1, opening F10.2.
 *
 * Tested through what a Programme Manager and a read-only Artizent role see and do: the
 * generate/edit/publish lifecycle, the narrative edit, and the PDF/PPTX exports -- see
 * `status-pack/StatusPack.tsx`'s own docstring for the disclosed readings.
 */

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, type Identity } from '../lib/api';
import { StatusPack } from '../status-pack/StatusPack';
import { fakeApi, statusPackData } from './fixtures';

const PM: Identity = { principal: 'user:pm@artizent.example', roles: ['programme_manager'] };
const PLATFORM_ENGINEER: Identity = {
  principal: 'user:p.eng@artizent.example',
  roles: ['platform_engineer'],
};

describe('reading the pack', () => {
  it('says no Status Pack has been generated yet when none exists', async () => {
    render(<StatusPack api={fakeApi()} identity={PM} />);

    expect(await screen.findByText('No Status Pack has been generated yet.')).toBeInTheDocument();
  });

  it('shows the narrative and the frozen KPI snapshot once one exists', async () => {
    const api = fakeApi();
    const generated = statusPackData({ narrative: 'Five MUs in flight, one blocked.' });
    api.statusPack = async () => generated;
    render(<StatusPack api={api} identity={PLATFORM_ENGINEER} />);

    const pane = await screen.findByRole('region', { name: 'Status Pack' });
    expect(pane).toHaveTextContent('Five MUs in flight, one blocked.');
    expect(await screen.findByText(`v1 · week of ${generated.week_of}`)).toBeInTheDocument();
  });

  it('surfaces a read failure other than the honest empty state', async () => {
    const api = fakeApi();
    api.statusPack = async () => {
      throw new ApiError(503, 'unavailable', 'the Status Pack is not available');
    };
    render(<StatusPack api={api} identity={PM} />);

    expect(await screen.findByText(/the Status Pack is not available/)).toBeInTheDocument();
  });
});

describe('generate, edit and publish', () => {
  it('lets the Programme Manager generate this week’s pack', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<StatusPack api={api} identity={PM} />);
    await screen.findByText('No Status Pack has been generated yet.');

    await user.click(screen.getByRole('button', { name: 'Generate this week’s pack' }));

    expect(await screen.findByRole('region', { name: 'Status Pack' })).toBeInTheDocument();
    expect(api.recorded).toContainEqual(expect.objectContaining({ kind: 'GENERATE_STATUS_PACK' }));
  });

  it('saves an edited narrative as a new version', async () => {
    // The fake keeps the pack's own real, mutable state behind `generateStatusPack` /
    // `editStatusPack` / `publishStatusPack` -- seeded through the real generate action,
    // the same way a real Status Pack would exist before anyone could edit it, rather
    // than overriding `statusPack` alone (which only stubs the read, not the state the
    // other three actions actually mutate).
    const user = userEvent.setup();
    const api = fakeApi();
    render(<StatusPack api={api} identity={PM} />);
    await screen.findByText('No Status Pack has been generated yet.');
    await user.click(screen.getByRole('button', { name: 'Generate this week’s pack' }));
    const textarea = await screen.findByRole('textbox');

    await user.clear(textarea);
    await user.type(textarea, 'Updated narrative.');
    await user.click(screen.getByRole('button', { name: 'Save edit' }));

    expect(await screen.findByText('Saved as version 2.')).toBeInTheDocument();
    expect(api.recorded).toContainEqual(
      expect.objectContaining({ kind: 'EDIT_STATUS_PACK', reason: 'Updated narrative.' }),
    );
  });

  it('publishes to the client and then disables Publish', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    render(<StatusPack api={api} identity={PM} />);
    await screen.findByText('No Status Pack has been generated yet.');
    await user.click(screen.getByRole('button', { name: 'Generate this week’s pack' }));
    await screen.findByRole('region', { name: 'Status Pack' });

    await user.click(screen.getByRole('button', { name: 'Publish to client' }));

    expect(await screen.findByText('Published to the client.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Published' })).toBeDisabled();
  });

  it('hides generate/edit/publish from anyone but the Programme Manager', async () => {
    const api = fakeApi();
    api.statusPack = async () => statusPackData();
    render(<StatusPack api={api} identity={PLATFORM_ENGINEER} />);

    await screen.findByRole('region', { name: 'Status Pack' });
    expect(screen.queryByRole('button', { name: /Generate/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save edit' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Publish/ })).not.toBeInTheDocument();
    expect(
      screen.getByText(/Generating, editing and publishing are the Programme Manager/),
    ).toBeInTheDocument();
  });

  it('shows the API refusal rather than a generic failure', async () => {
    const user = userEvent.setup();
    const api = fakeApi();
    api.failNext(new ApiError(403, 'forbidden', 'programme manager only'));
    render(<StatusPack api={api} identity={PM} />);
    await screen.findByText('No Status Pack has been generated yet.');

    await user.click(screen.getByRole('button', { name: 'Generate this week’s pack' }));

    expect(await screen.findByText('programme manager only')).toBeInTheDocument();
  });
});

describe('export', () => {
  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => 'blob:test');
    URL.revokeObjectURL = vi.fn();
  });

  it('exports the pack as PDF and PPTX', async () => {
    const user = userEvent.setup();
    const clicked = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    const api = fakeApi();
    api.statusPack = async () => statusPackData();
    render(<StatusPack api={api} identity={PLATFORM_ENGINEER} />);
    await screen.findByRole('region', { name: 'Status Pack' });

    await user.click(screen.getByRole('button', { name: 'Export as PDF' }));
    await user.click(screen.getByRole('button', { name: 'Export as PPTX' }));

    expect(clicked).toHaveBeenCalledTimes(2);
    clicked.mockRestore();
  });
});
