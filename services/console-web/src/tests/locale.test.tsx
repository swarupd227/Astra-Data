/**
 * Locale -- story S10.5.1. `formatDateTime`/`formatDateTimeWithCharterNote` are the
 * real deliverable (see `lib/locale.ts`'s own module docstring for why date/time
 * formatting, not a strings dictionary, is where the two locales genuinely differ);
 * tested against a fixed, known viewer timezone so day-first vs month-first ordering
 * is asserted on real, deterministic output, not "whatever machine happens to run this."
 */

import { describe, expect, it } from 'vitest';

import { formatDateTime, formatDateTimeWithCharterNote, t, viewerTimezone } from '../lib/locale';

const ISO = '2027-03-04T15:30:00.000Z'; // an unambiguous day/month so ordering is provable

describe('formatDateTime', () => {
  it('renders day-first for en-GB', () => {
    expect(formatDateTime(ISO, 'en-GB')).toMatch(/^04\/03\/2027, \d{2}:\d{2}$/);
  });

  it('renders month-first, 12-hour, for en-US', () => {
    expect(formatDateTime(ISO, 'en-US')).toMatch(/^03\/04\/2027, \d{1,2}:\d{2}\s?(AM|PM|am|pm)$/i);
  });

  it('renders an em dash for a missing value', () => {
    expect(formatDateTime(null, 'en-GB')).toBe('—');
    expect(formatDateTime(undefined, 'en-GB')).toBe('—');
  });

  it('falls back to the raw string for an unparsable value, rather than "Invalid Date"', () => {
    expect(formatDateTime('not-a-date', 'en-GB')).toBe('not-a-date');
  });
});

describe('formatDateTimeWithCharterNote', () => {
  // Computed against the real host's own resolved zone rather than a mocked one, so
  // this is deterministic on every machine without needing to intercept `Intl` itself.
  const viewer = viewerTimezone();
  const otherZone = viewer === 'America/New_York' ? 'Europe/London' : 'America/New_York';

  it('notes the charter timezone when it differs from the viewer\'s own', () => {
    const rendered = formatDateTimeWithCharterNote(ISO, 'en-GB', otherZone);
    expect(rendered).toContain(`(charter timezone: ${otherZone})`);
  });

  it('says nothing extra when the charter timezone matches the viewer\'s own', () => {
    const rendered = formatDateTimeWithCharterNote(ISO, 'en-GB', viewer);
    expect(rendered).not.toContain('charter timezone');
  });

  it('says nothing extra when no charter timezone is given', () => {
    const rendered = formatDateTimeWithCharterNote(ISO, 'en-GB', null);
    expect(rendered).not.toContain('charter timezone');
  });
});

describe('t', () => {
  it('translates the one real divergent word this story\'s scope found', () => {
    expect(t('licence', 'en-GB')).toBe('licence');
    expect(t('licence', 'en-US')).toBe('license');
  });
});
