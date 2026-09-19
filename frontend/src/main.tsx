import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import App from './App';
import './styles/tokens.css';

const container = document.getElementById('root');
if (container === null) {
  // A missing root means index.html and this entry point disagree. Failing
  // loudly beats rendering nothing and leaving a blank page to diagnose.
  throw new Error('Root container #root is missing from the document.');
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
