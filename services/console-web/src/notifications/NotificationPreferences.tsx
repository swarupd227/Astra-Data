/**
 * Notification Preferences -- story S10.5.2, opening F10.5.
 *
 * "As a report owner, I want notifications I can tune, so that I hear about my
 * reports, not everyone's." Self-service, per-principal: this screen only ever reads
 * and writes the calling identity's own row (`GET`/`PUT /v1/notification-preferences`),
 * never anyone else's -- there is no admin list of other people's preferences to manage.
 *
 * **Visible to every role**, unlike most screens in this console -- notification
 * preferences are not one persona's concern the way the Decision Register or the Gate
 * Inbox are. `App.tsx` adds this surface to every role's own `CLIENT_VISIBLE_SURFACES`
 * entry (and it is already open to every Artizent role).
 *
 * **Content never includes data values; every record links to the console** -- an AC
 * clause this screen cannot violate structurally, since it never renders a notification's
 * own content at all, only the preferences that gate whether one is ever recorded. See
 * `notification_preferences.py`'s own module docstring for where that discipline
 * actually lives (each of the four real write-site integrations).
 *
 * "Send digests now" is an Artizent-only action (`ArtizentDep`, mirroring `g2_reminders.
 * send_due_reminders`'s own gate) -- hidden, not disabled, for a client role, the
 * identical hide-not-disable convention every other role-gated action in this console
 * already uses.
 */

import { useCallback, useEffect, useState } from 'react';

import type {
  Api,
  DigestMode,
  Identity,
  NotificationChannel,
  NotificationEvent,
  NotificationPreferenceOptions,
  NotificationPreferences as NotificationPreferencesData,
} from '../lib/api';
import { ApiError } from '../lib/api';
import { isArtizentRole } from '../lib/roles';

interface Props {
  api: Api;
  identity: Identity;
}

const EVENT_LABELS: Record<NotificationEvent, string> = {
  gate_request: 'Gate request',
  exception_assigned: 'Exception assigned',
  regression_fail: 'Regression fail',
  train_replan: 'Train re-plan',
};

const CHANNEL_LABELS: Record<NotificationChannel, string> = {
  email: 'Email',
  teams: 'Teams',
};

function toggle<T>(set: T[], value: T): T[] {
  return set.includes(value) ? set.filter((v) => v !== value) : [...set, value];
}

export function NotificationPreferences({ api, identity }: Props): JSX.Element {
  const [options, setOptions] = useState<NotificationPreferenceOptions | null>(null);
  const [saved, setSaved] = useState<NotificationPreferencesData | null>(null);
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [events, setEvents] = useState<NotificationEvent[]>([]);
  const [digestMode, setDigestMode] = useState<DigestMode>('immediate');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [digestBusy, setDigestBusy] = useState(false);
  const [digestNotice, setDigestNotice] = useState<string | null>(null);

  const isArtizent = isArtizentRole(identity.roles);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [preferences, opts] = await Promise.all([
        api.notificationPreferences(identity),
        api.notificationPreferenceOptions(identity),
      ]);
      setSaved(preferences);
      setChannels(preferences.channels);
      setEvents(preferences.events);
      setDigestMode(preferences.digest_mode);
      setOptions(opts);
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : 'Notification preferences could not be read.');
    } finally {
      setLoading(false);
    }
  }, [api, identity]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = useCallback(async () => {
    setSaving(true);
    setNotice(null);
    try {
      const result = await api.saveNotificationPreferences({ channels, events, digest_mode: digestMode }, identity);
      setSaved(result);
      setNotice('Saved.');
    } catch (caught: unknown) {
      setNotice(caught instanceof ApiError ? caught.message : 'Preferences could not be saved.');
    } finally {
      setSaving(false);
    }
  }, [api, identity, channels, events, digestMode]);

  const sendDigests = useCallback(async () => {
    setDigestBusy(true);
    setDigestNotice(null);
    try {
      const result = await api.sendNotificationDigests(identity);
      setDigestNotice(`Sent ${result.count} digest(s).`);
    } catch (caught: unknown) {
      setDigestNotice(caught instanceof ApiError ? caught.message : 'Digests could not be sent.');
    } finally {
      setDigestBusy(false);
    }
  }, [api, identity]);

  return (
    <div className="workspace notification-preferences-workspace">
      <section className="pane" aria-label="Notification Preferences">
        <header className="pane-header">
          <h2>Notification Preferences</h2>
          {saved?.updated_at && <span className="faint">saved {saved.updated_at}</span>}
          {saved && !saved.updated_at && <span className="pill idle">using the defaults</span>}
        </header>
        <div className="pane-body detail">
          {error && <div className="banner">{error}</div>}
          {!error && loading && <p className="empty">Reading your notification preferences…</p>}
          {!error && !loading && options && (
            <>
              <p className="faint">
                Notifications never include data values -- every one links back to this console.
              </p>

              <h3>Channels</h3>
              <fieldset>
                <legend className="visually-hidden">Channels</legend>
                {options.channels.map((channel) => (
                  <label key={channel} className="checkbox-row">
                    <input
                      type="checkbox"
                      checked={channels.includes(channel)}
                      onChange={() => setChannels((prev) => toggle(prev, channel))}
                    />
                    {CHANNEL_LABELS[channel]}
                  </label>
                ))}
              </fieldset>

              <h3>Events</h3>
              <fieldset>
                <legend className="visually-hidden">Events</legend>
                {options.events.map((event) => (
                  <label key={event} className="checkbox-row">
                    <input
                      type="checkbox"
                      checked={events.includes(event)}
                      onChange={() => setEvents((prev) => toggle(prev, event))}
                    />
                    {EVENT_LABELS[event]}
                  </label>
                ))}
              </fieldset>

              <h3>Digest mode</h3>
              <fieldset>
                <legend className="visually-hidden">Digest mode</legend>
                {options.digest_modes.map((mode) => (
                  <label key={mode} className="checkbox-row">
                    <input
                      type="radio"
                      name="digest_mode"
                      checked={digestMode === mode}
                      onChange={() => setDigestMode(mode)}
                    />
                    {mode === 'immediate' ? 'Immediate' : 'Daily digest'}
                  </label>
                ))}
              </fieldset>

              <footer className="statusbar">
                {notice && <span>{notice}</span>}
                <span className="spacer" />
                <button type="button" className="btn primary" disabled={saving} onClick={() => void save()}>
                  {saving ? 'Saving…' : 'Save'}
                </button>
              </footer>

              {isArtizent && (
                <>
                  <h3>Digest delivery</h3>
                  <p className="faint">
                    Batches and records everything queued for a &ldquo;daily&rdquo; preference, one digest per
                    recipient and channel.
                  </p>
                  <div className="statusbar">
                    {digestNotice && <span>{digestNotice}</span>}
                    <span className="spacer" />
                    <button type="button" className="btn" disabled={digestBusy} onClick={() => void sendDigests()}>
                      {digestBusy ? 'Sending…' : 'Send digests now'}
                    </button>
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </section>
    </div>
  );
}
