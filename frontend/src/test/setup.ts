// Registers the jest-dom matchers on vitest's `expect`. Imported for its side
// effect, and for the type augmentation that makes the matchers visible to
// TypeScript.
import '@testing-library/jest-dom/vitest';

import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

// Explicit, because this project runs vitest without injected globals.
// Testing Library registers its own `afterEach` cleanup only when it can find
// a global one; without this, every rendered tree would stay mounted and the
// next test would query a document containing all of its predecessors.
afterEach(cleanup);
