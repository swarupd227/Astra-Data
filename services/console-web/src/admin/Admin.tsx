/**
 * Admin — the conformance ruleset. Story S4.3.2.
 *
 * "As an architect, I want conformance rules enforced at emission, so that no model
 * reaches the client repository that breaks the target architecture... Rules are data,
 * editable by the architect in Admin, versioned, and recorded on the ModelFamily at
 * build."
 *
 * §15.3.7 names five Admin screens (Platform Health, Pattern Library, Model Gateway &
 * TokenOps, Data Handling, Tenant & Access) — none of them this one, and no other backlog
 * story claims a rules-editing screen, so this is a sixth, single-purpose Admin surface
 * this story adds on its own rather than waiting on a fuller Admin shell nobody has
 * specified yet.
 *
 * Six rules, each a plain enable/disable plus whatever parameters it has (most have none;
 * naming convention has a max length, the RLS check has a fixture username) — a save is a
 * new version, never an overwrite, so a build recorded against version 3 stays checkable
 * against exactly what version 3 said. Editing is the Migration Architect's; everyone else
 * reads the same screen with no Save button, the same hide-not-disable convention every
 * other role-gated action in this console already follows.
 */

import { useCallback, useEffect, useState } from 'react';

import type { Api, ConformanceRuleset, Identity, RuleConfig, RuleMetadataEntry } from '../lib/api';
import { ApiError } from '../lib/api';

interface Props {
  api: Api;
  identity: Identity;
}

export function Admin({ api, identity }: Props): JSX.Element {
  const [ruleset, setRuleset] = useState<ConformanceRuleset | null>(null);
  const [metadata, setMetadata] = useState<Record<string, RuleMetadataEntry>>({});
  const [draft, setDraft] = useState<RuleConfig[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const canEdit = identity.roles.includes('migration_architect');

  useEffect(() => {
    let live = true;
    api
      .conformanceRules(identity)
      .then((response) => {
        if (!live) return;
        setRuleset(response.ruleset);
        setMetadata(response.rule_metadata);
        setDraft(response.ruleset.rules.map((r) => ({ ...r, params: { ...r.params } })));
        setError(null);
      })
      .catch((caught: unknown) => {
        if (live) setError(caught instanceof ApiError ? caught.message : 'The conformance ruleset could not be read.');
      });
    return () => {
      live = false;
    };
  }, [api, identity]);

  const setEnabled = (ruleId: string, enabled: boolean): void => {
    setDraft((current) => current.map((r) => (r.rule_id === ruleId ? { ...r, enabled } : r)));
  };

  const setParam = (ruleId: string, key: string, value: string): void => {
    setDraft((current) =>
      current.map((r) => (r.rule_id === ruleId ? { ...r, params: { ...r.params, [key]: value } } : r)),
    );
  };

  const dirty =
    ruleset !== null &&
    JSON.stringify(draft) !== JSON.stringify(ruleset.rules.map((r) => ({ ...r, params: { ...r.params } })));

  const save = useCallback(async () => {
    setBusy(true);
    setNotice(null);
    try {
      const response = await api.saveConformanceRules(draft, identity);
      setRuleset(response.ruleset);
      setDraft(response.ruleset.rules.map((r) => ({ ...r, params: { ...r.params } })));
      setNotice(`Saved version ${response.ruleset.version}.`);
    } catch (caught: unknown) {
      setNotice(caught instanceof ApiError ? caught.message : 'The ruleset could not be saved.');
    } finally {
      setBusy(false);
    }
  }, [api, identity, draft]);

  return (
    <div className="workspace admin-workspace">
      <section className="pane" aria-label="Conformance rules">
        <header className="pane-header">
          <h2>Conformance rules</h2>
          {ruleset && <span className="pill idle mono">version {ruleset.version}</span>}
        </header>
        <div className="pane-body">
          {error && <div className="banner">{error}</div>}
          {!error && ruleset === null && <p className="empty">Reading the conformance ruleset…</p>}
          {ruleset && (
            <>
              <p className="faint">
                Enforced at build (§12.3) — a rule failure blocks BUILT and lists the violation
                with the offending object on the Build tab.
              </p>
              <table className="estate">
                <thead>
                  <tr>
                    <th>Enabled</th>
                    <th>Rule</th>
                    <th>Parameters</th>
                  </tr>
                </thead>
                <tbody>
                  {draft.map((rule) => {
                    const meta = metadata[rule.rule_id];
                    return (
                      <tr key={rule.rule_id}>
                        <td>
                          <input
                            type="checkbox"
                            aria-label={`Enable ${meta?.label ?? rule.rule_id}`}
                            checked={rule.enabled}
                            disabled={!canEdit}
                            onChange={(e) => setEnabled(rule.rule_id, e.target.checked)}
                          />
                        </td>
                        <td>
                          <strong>{meta?.label ?? rule.rule_id}</strong>
                          <br />
                          <span className="muted">{meta?.description ?? ''}</span>
                        </td>
                        <td>
                          {meta && Object.keys(meta.params).length > 0 ? (
                            <div className="admin-params">
                              {Object.entries(meta.params).map(([key, hint]) => (
                                <label key={key} className="admin-param-field">
                                  <span className="faint">{hint}</span>
                                  <input
                                    type="text"
                                    aria-label={`${meta.label} — ${hint}`}
                                    value={String(rule.params[key] ?? '')}
                                    disabled={!canEdit}
                                    onChange={(e) => setParam(rule.rule_id, key, e.target.value)}
                                  />
                                </label>
                              ))}
                            </div>
                          ) : (
                            <span className="faint">—</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <dl>
                <dt>Last saved by</dt>
                <dd>{ruleset.updated_by}</dd>
                <dt>Last saved at</dt>
                <dd>{ruleset.updated_at ?? 'never — built-in default'}</dd>
              </dl>
            </>
          )}
        </div>
        <footer className="statusbar">
          {notice && <span>{notice}</span>}
          <span className="spacer" />
          {canEdit ? (
            <button type="button" className="btn primary" disabled={!dirty || busy} onClick={() => void save()}>
              {busy ? 'Saving…' : 'Save new version'}
            </button>
          ) : (
            <span className="faint">Editing the conformance ruleset is the Migration Architect&rsquo;s.</span>
          )}
        </footer>
      </section>
    </div>
  );
}
