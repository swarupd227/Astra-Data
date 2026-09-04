/**
 * What happens to the selection when the estate under it changes.
 *
 * Both of these were found by using the screen rather than by reading the code. Withdrawing
 * a workbook takes it out of the default filter, and clearing the selection on every reload
 * took away the decision the user had just made — along with the Reinstate button that
 * undoes it. The fix ties clearing to the *filters* changing, and both halves of that are
 * asserted here so the next person to touch it knows which is which.
 */

import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { EstateExplorer } from '../estate/EstateExplorer';
import type { Identity } from '../lib/api';
import { fakeApi } from './fixtures';

const PM: Identity = { principal: 'user:pm@artizent.example', roles: ['programme_manager'] };

describe('the selection', () => {
  it('survives the decision that removes its row from the list', async () => {
    const user = userEvent.setup();
    render(<EstateExplorer api={fakeApi()} identity={PM} />);

    await user.click(await screen.findByRole('row', { name: /Daily VaR/ }));
    await user.click(await screen.findByRole('button', { name: /Withdraw…/ }));
    const dialog = await screen.findByRole('dialog');
    await user.type(within(dialog).getByLabelText('Reason'), 'Superseded by the Treasury pack');
    await user.click(within(dialog).getByRole('button', { name: 'Withdraw' }));

    const panel = await screen.findByRole('region', { name: 'Selected workbook' });
    await waitFor(() => expect(within(panel).getByText('Daily VaR')).toBeInTheDocument());
    expect(within(panel).queryByText(/Select a workbook/)).not.toBeInTheDocument();
  });

  it('clears when the filters change, because the user has moved on', async () => {
    const user = userEvent.setup();
    render(<EstateExplorer api={fakeApi()} identity={PM} />);

    await user.click(await screen.findByRole('row', { name: /Daily VaR/ }));
    await screen.findByText('wb-daily-var');

    await user.click(screen.getByRole('button', { name: /Treasury/ }));

    const panel = await screen.findByRole('region', { name: 'Selected workbook' });
    await waitFor(() =>
      expect(within(panel).getByText(/Select a workbook/)).toBeInTheDocument(),
    );
  });
});
