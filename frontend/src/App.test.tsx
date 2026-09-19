import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import App from './App';

describe('App', () => {
  it('renders the application shell', () => {
    render(<App />);

    expect(screen.getByRole('heading', { level: 1, name: 'Reviewer UI' })).toBeInTheDocument();
  });

  it('states that the tool is local and unauthenticated', () => {
    // Not Phase A scaffolding. The notice describes a property that holds for
    // the whole of Sprint 12, and Sprint 13 owns the boundary that removes it.
    render(<App />);

    // Asserted through the note landmark rather than by text, so the test
    // also pins that the disclosure is exposed as a region a screen reader
    // announces -- not merely present somewhere on the page.
    const notice = screen.getByRole('note');
    expect(notice).toHaveTextContent(/not authenticated/i);
    expect(notice).toHaveTextContent(/not internet-ready/i);
  });

  it('displays no review data', () => {
    // A mocked-up queue would be indistinguishable from working software in a
    // screenshot, over customer-derived review evidence.
    render(<App />);

    expect(screen.queryByRole('table')).toBeNull();
    expect(screen.queryByRole('button')).toBeNull();
  });
});
