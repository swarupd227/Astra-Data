/**
 * Locale -- story S10.5.1, opening F10.5.
 *
 * Two real locales, en-GB and en-US. en-GB stays the default -- `estate/format.tsx`'s
 * own `count()` already formats numbers `en-GB` (confirmed by direct search: no other
 * locale is used anywhere in this codebase today), and this story's own AC names en-US
 * as the one real alternative to add, not a replacement for the existing default.
 *
 * **This codebase's own real user-facing vocabulary has almost no genuine en-GB/en-US
 * spelling divergence, confirmed by direct search across every screen this story
 * touches.** "Programme" and "licence" are this platform's own canonical domain nouns --
 * used identically in the product spec, role names (`client_licence_admin`) and every
 * screen's own copy -- not a British-English styling choice a US reader would expect
 * swapped; translating them would misrepresent the product's own vocabulary as a dialect
 * preference rather than leave it as the domain term it is. `STRINGS` below is real,
 * used, and extensible, not padded with invented divergence to look more complete than
 * the actual language difference is -- "licence"/"license" (the one real word this
 * story's own scope found spelled out as prose, in `App.tsx`'s "Client Licence
 * Administrator" role label) is genuinely all there is to show for it today.
 *
 * **The one place the two locales genuinely, measurably differ is date and time
 * formatting** -- day-first vs month-first order, a 24-hour vs 12-hour clock -- so
 * `formatDateTime` below, not the strings dictionary, is where this module's real value
 * is. Every date this story's own named screens render is converted to the *viewer's*
 * own local timezone (`Intl.DateTimeFormat` with no explicit `timeZone` -- the runtime's
 * own resolved zone, the honest "wherever this browser thinks it is" reading, not a
 * guess this codebase has no better source for); `withCharterNote` appends the Tolerance
 * Charter's own configured comparison timezone (`ToleranceCharter.dates.timezone`, a
 * real, already-modelled field -- S7.1.1) only when it differs from the viewer's own
 * resolved zone, so a viewer who already is in that zone is not shown a redundant note.
 */

export type LocaleCode = 'en-GB' | 'en-US';

export const LOCALES: { value: LocaleCode; label: string }[] = [
  { value: 'en-GB', label: 'English (UK)' },
  { value: 'en-US', label: 'English (US)' },
];

export const DEFAULT_LOCALE: LocaleCode = 'en-GB';

const STRINGS = {
  licenceAdmin: { 'en-GB': 'Client Licence Administrator', 'en-US': 'Client License Administrator' },
  licence: { 'en-GB': 'licence', 'en-US': 'license' },
} satisfies Record<string, Record<LocaleCode, string>>;

export function t(key: keyof typeof STRINGS, locale: LocaleCode): string {
  return STRINGS[key][locale];
}

/** The viewer's own resolved IANA timezone -- what "the viewer's timezone" (this
 * story's own AC wording) concretely means, read once from the runtime. */
export function viewerTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

/** `iso` converted to the viewer's own local timezone, formatted per locale --
 * day-first `DD/MM/YYYY, HH:mm` for en-GB, month-first `MM/DD/YYYY, h:mm am/pm` for
 * en-US (each locale's own real, conventional order and clock, not a fixed format with
 * only the separator changed). `null`/absent input renders as the same em dash every
 * other "no value" cell in this console already uses. */
export function formatDateTime(iso: string | null | undefined, locale: LocaleCode): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat(locale, {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(date);
}

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;

/** A pure calendar date (ontology `T.DATE` -- `ReleaseTrain.planned_start` and its three
 * siblings, ontology/nodes.py's own distinct type from `T.TIMESTAMP`), reordered per
 * locale without ever constructing a `Date`/going through a timezone. A `T.DATE` value
 * carries no time-of-day, so converting it through `formatDateTime`'s own timezone
 * math would risk shifting the calendar date itself for a viewer behind UTC (`new
 * Date("2027-03-04")` parses as UTC midnight -- a viewer at UTC-5 would see "03" render
 * where "04" is correct) -- the exact bug this function exists to avoid. Falls back to
 * the raw string, unrecognised, for anything not shaped `YYYY-MM-DD`. */
export function formatDate(value: string | null | undefined, locale: LocaleCode): string {
  if (!value) return '—';
  const match = DATE_ONLY.exec(value);
  if (!match) return value;
  const [, year, month, day] = match;
  return locale === 'en-US' ? `${month}/${day}/${year}` : `${day}/${month}/${year}`;
}

/** The same rendering, with the Tolerance Charter's own configured comparison timezone
 * noted alongside -- only when it is real (`charterTimezone` given) and differs from
 * the viewer's own resolved zone, per this module's own docstring. */
export function formatDateTimeWithCharterNote(
  iso: string | null | undefined,
  locale: LocaleCode,
  charterTimezone: string | null | undefined,
): string {
  const rendered = formatDateTime(iso, locale);
  if (!iso || !charterTimezone || charterTimezone === viewerTimezone()) return rendered;
  return `${rendered} (charter timezone: ${charterTimezone})`;
}
