/**
 * The left pane: site → project, with counts and parse status.
 *
 * Spec §15.3.2. Workbooks are not nodes of this tree — a thousand leaves is not something
 * anybody navigates, and the centre pane is the list. The tree's job is counts and where
 * the trouble is, which is why the only decoration on a row is the held/unparsed flags.
 */

import type { TreeNode } from '../lib/api';

export interface TreeSelection {
  site: string | null;
  project: string | null;
}

interface Props {
  tree: TreeNode[];
  selection: TreeSelection;
  onSelect: (selection: TreeSelection) => void;
}

function Flags({ node }: { node: TreeNode }): JSX.Element | null {
  if (!node.held && !node.unparsed) return null;
  return (
    <span className="tree-flags">
      {node.held > 0 && (
        <span className="dot warn" title={`${node.held} held below the parse-quality threshold`} />
      )}
      {node.unparsed > 0 && (
        <span className="dot bad" title={`${node.unparsed} never parsed`} />
      )}
    </span>
  );
}

export function Tree({ tree, selection, onSelect }: Props): JSX.Element {
  const total = tree.reduce((sum, site) => sum + site.workbooks, 0);
  const everything = selection.site === null && selection.project === null;

  return (
    <nav className="tree" aria-label="Estate">
      <button
        type="button"
        className="tree-row"
        aria-current={everything}
        onClick={() => onSelect({ site: null, project: null })}
      >
        <span className="twisty" aria-hidden="true" />
        <span className="name">All sites</span>
        <span className="count">{total.toLocaleString('en-GB')}</span>
      </button>

      {tree.map((site) => {
        const siteSelected = selection.site === site.name && selection.project === null;
        return (
          <div key={site.id}>
            <button
              type="button"
              className="tree-row"
              aria-current={siteSelected}
              onClick={() => onSelect({ site: site.name, project: null })}
            >
              <span className="twisty" aria-hidden="true">
                ▾
              </span>
              <span className="name" title={site.name}>
                {site.name}
              </span>
              <span className="count">
                <Flags node={site} /> {site.workbooks.toLocaleString('en-GB')}
              </span>
            </button>

            {site.children.map((project) => (
              <button
                type="button"
                key={project.id}
                className="tree-row child"
                aria-current={selection.site === site.name && selection.project === project.name}
                onClick={() => onSelect({ site: site.name, project: project.name })}
              >
                <span />
                <span className="name" title={project.name}>
                  {project.name}
                </span>
                <span className="count">
                  <Flags node={project} /> {project.workbooks.toLocaleString('en-GB')}
                </span>
              </button>
            ))}
          </div>
        );
      })}

      {tree.length === 0 && (
        <p className="faint" style={{ padding: '12px 6px' }}>
          Nothing harvested yet. Start a harvest from the toolbar.
        </p>
      )}
    </nav>
  );
}
