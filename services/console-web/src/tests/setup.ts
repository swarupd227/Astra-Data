import '@testing-library/jest-dom/vitest';
import { configure } from '@testing-library/react';
import { beforeEach } from 'vitest';

// jsdom plus a real user-event delay makes the default one-second wait tight on a
// loaded CI worker. A suite that fails intermittently teaches people to re-run it.
configure({ asyncUtilTimeout: 4000 });

// The Explorer keeps its filters in the URL so a view can be shared, and jsdom keeps
// one URL for the whole file — so without this a test starts with whatever filters the
// previous one left behind. Found by a test that passed alone and failed in the suite.
beforeEach(() => {
  window.history.replaceState(null, '', '/');
});
