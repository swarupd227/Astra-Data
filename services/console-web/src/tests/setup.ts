import '@testing-library/jest-dom/vitest';
import { configure } from '@testing-library/react';
import { beforeEach, expect } from 'vitest';
import { toHaveNoViolations } from 'jest-axe';

// S10.5.1: real, automated WCAG 2.2 AA checks in CI (`npm run test`, already wired into
// `.github/workflows/ci.yml`'s own `console` job) -- `jest-axe` wraps the real `axe-core`
// engine (the same one browser DevTools' own Lighthouse/axe extension runs), not a
// hand-rolled ruleset. `toHaveNoViolations()` is available to every test file from here;
// see `tests/a11y.test.tsx` for where it is actually asserted.
expect.extend(toHaveNoViolations);

// jsdom plus a real user-event delay makes the default one-second wait tight on a
// loaded CI worker. A suite that fails intermittently teaches people to re-run it.
configure({ asyncUtilTimeout: 4000 });

// The Explorer keeps its filters in the URL so a view can be shared, and jsdom keeps
// one URL for the whole file — so without this a test starts with whatever filters the
// previous one left behind. Found by a test that passed alone and failed in the suite.
beforeEach(() => {
  window.history.replaceState(null, '', '/');
});
