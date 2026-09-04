import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './App';
import './styles.css';

const container = document.getElementById('root');
if (!container) throw new Error('the console has no mount point');

// The environment chrome has to be visibly distinct between prod, test and dev (§15.6),
// so it comes from build configuration rather than from a guess about the hostname.
const environment = import.meta.env.VITE_ASTRA_ENV ?? 'local';

createRoot(container).render(
  <StrictMode>
    <App environment={environment} />
  </StrictMode>,
);
