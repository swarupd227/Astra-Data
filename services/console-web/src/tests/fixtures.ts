/**
 * A fake API and the estate it serves.
 *
 * The console's tests drive the real components against a fake of the API contract, not
 * against a mock of fetch. That way a change to the response shape breaks the tests here
 * as well as the types, and a test cannot pass because it asserted on a mock it wrote.
 */

import type {
  AcceptanceSummary,
  Api,
  AppliedRule,
  ApplyRulesResult,
  AwaitingG2Response,
  AwaitingG2Review,
  BuildRecord,
  ClassMix,
  ConformanceRuleset,
  ConstructIssue,
  ConstructsResponse,
  DesignDocument,
  EstateQuery,
  EstateResponse,
  ExceptionAgeingResponse,
  ExceptionCaseDetail,
  ExceptionQueueEntry,
  ExceptionQueueResponse,
  FailingCellRow,
  FamiliesResponse,
  FamilyRecord,
  FamilyTransition,
  G2Question,
  G3Card,
  G3Question,
  Identity,
  LineageQuery,
  LineageResponse,
  ModelProposal,
  ModelVersion,
  MovedClassification,
  ParityDashboardResponse,
  ParityRunResponse,
  ParityRunTrendEntry,
  PatternPromotionStatus,
  PatternProvenance,
  PatternRecord,
  PatternsResponse,
  ProgrammeRecord,
  ProgrammesResponse,
  PromoteResult,
  PromotionRecord,
  QueueResponse,
  ReclassifyResult,
  RegressionExportRecord,
  RegressionMonitorResponse,
  RegressionMonitorRow,
  RegressionScheduleRecord,
  ReleaseBoard as ReleaseBoardData,
  ReleaseBoardMu,
  RequestNewVersionResult,
  RuleCatalog,
  RuleCatalogEntry,
  RuleCoverage,
  RunParityResult,
  RunVisualParityResult,
  SheetParityStats,
  SimulateResult,
  ToleranceCharter,
  ToleranceCharterFieldMetadata,
  ToleranceCharterVersion,
  Train,
  TrainEvent,
  TrainMember,
  TrainProjection,
  TrainProjectionsResponse,
  TrainsResponse,
  VerdictRow,
  VersionsResponse,
  VisualCapturePair,
  Workbook,
  WorkbookDetail,
} from '../lib/api';
import { ApiError } from '../lib/api';

export function workbook(overrides: Partial<Workbook> = {}): Workbook {
  return {
    id: '01ARZ3NDEKTSV4RRFFQ69G5FAV',
    luid: 'wb-daily-var',
    name: 'Daily VaR',
    site: 'RQA',
    site_id: 'site-rqa',
    project: 'Risk Core',
    project_id: 'prj-risk',
    parse_quality: 1,
    parse_quality_band: 'clean',
    views_90d: 412,
    usage_band: 'medium',
    distinct_viewers_90d: 31,
    owner: 'A Mehta',
    owner_id: 'user-mehta',
    calculated_fields: 27,
    held: false,
    tier: null,
    withdrawn: false,
    withdrawn_reason: null,
    ...overrides,
  };
}

export const HELD = workbook({
  id: '01ARZ3NDEKTSV4RRFFQ69G5FA2',
  luid: 'wb-liquidity',
  name: 'Liquidity Ladder',
  project: 'Treasury',
  project_id: 'prj-treasury',
  parse_quality: 0.83,
  parse_quality_band: 'poor',
  held: true,
  views_90d: 4,
  usage_band: 'low',
  owner: null,
  owner_id: null,
  calculated_fields: 3,
});

export function estateResponse(overrides: Partial<EstateResponse> = {}): EstateResponse {
  const workbooks = overrides.workbooks ?? [workbook(), HELD];
  return {
    tree: [
      {
        id: 'site-rqa',
        name: 'RQA',
        kind: 'site',
        workbooks: 2,
        held: 1,
        unparsed: 0,
        views_90d: 416,
        children: [
          {
            id: 'prj-risk',
            name: 'Risk Core',
            kind: 'project',
            workbooks: 1,
            held: 0,
            unparsed: 0,
            views_90d: 412,
            children: [],
          },
          {
            id: 'prj-treasury',
            name: 'Treasury',
            kind: 'project',
            workbooks: 1,
            held: 1,
            unparsed: 0,
            views_90d: 4,
            children: [],
          },
        ],
      },
    ],
    workbooks,
    total: workbooks.length,
    offset: 0,
    limit: 100,
    estate_total: 2,
    facets: {
      parse_quality_band: [
        { key: 'clean', label: '100%', count: 1 },
        { key: 'good', label: '98–99%', count: 0 },
        { key: 'held', label: '90–97%', count: 0 },
        { key: 'poor', label: 'under 90%', count: 1 },
        { key: 'unknown', label: 'not parsed', count: 0 },
      ],
      usage_band: [
        { key: 'unused', label: 'no views', count: 0 },
        { key: 'low', label: '1–49 views', count: 1 },
        { key: 'medium', label: '50–499 views', count: 1 },
        { key: 'high', label: '500+ views', count: 0 },
        { key: 'unknown', label: 'no usage data', count: 0 },
      ],
      owner: [
        { key: 'A Mehta', label: 'A Mehta', count: 1 },
        { key: '__none__', label: 'Unassigned', count: 1 },
      ],
      project: [
        { key: 'Risk Core', label: 'Risk Core', count: 1 },
        { key: 'Treasury', label: 'Treasury', count: 1 },
      ],
      site: [{ key: 'RQA', label: 'RQA', count: 2 }],
      tier: [],
      pending: [
        { facet: 'family', reason: 'Model family is assigned by clustering (E3/F3.2).' },
        { facet: 'state', reason: 'The Migration Unit state machine begins in E3/F3.2.' },
        { facet: 'train', reason: 'Release train membership is proposed in E3/F3.3.' },
      ],
      withdrawn: 0,
    },
    tiers: ['SIMPLE', 'MODERATE', 'COMPLEX', 'REDESIGN'],
    pending_columns: [
      { column: 'state', reason: 'The Migration Unit state machine begins in E3/F3.2.' },
    ],
    timing: { total_ms: 84, estate_read_ms: 61 },
    ...overrides,
  };
}

export function workbookDetail(overrides: Partial<WorkbookDetail> = {}): WorkbookDetail {
  return {
    workbook: {
      id: '01ARZ3NDEKTSV4RRFFQ69G5FAV',
      type: 'Workbook',
      properties: { name: 'Daily VaR', luid: 'wb-daily-var' },
    },
    scope: { decisions: [], current: { tier: null, tier_reason: null, withdrawn: false, withdrawn_reason: null } },
    lineage: {
      depth: 2,
      nodes: [
        { id: 'ws-1', type: 'Worksheet', name: 'VaR by Desk', depth: 1 },
        { id: 'ds-1', type: 'Datasource', name: 'Positions', depth: 1 },
        { id: 'f-1', type: 'Field', name: 'Notional', depth: 2 },
      ],
      edges: [
        { type: 'CONTAINS', from: '01ARZ3NDEKTSV4RRFFQ69G5FAV', to: 'ws-1' },
        { type: 'USES_DATASOURCE', from: 'ws-1', to: 'ds-1' },
        { type: 'HAS_FIELD', from: 'ds-1', to: 'f-1' },
      ],
      truncated: false,
    },
    migration_unit: null,
    migration_unit_reason:
      'No Migration Unit exists for this workbook. The Cartographer creates MUs in E3/F3.2.',
    ...overrides,
  };
}

export function lineageResponse(overrides: Partial<LineageResponse> = {}): LineageResponse {
  return {
    scope: {
      site: null,
      project: null,
      family: null,
      workbooks: null,
      min_strength: 0,
      limit: 250,
    },
    nodes: [
      { id: 'wb1', type: 'Workbook', name: 'Daily VaR', site: 'RQA', project: 'Risk Core',
        parse_quality: 1, views_90d: 412 },
      { id: 'wb2', type: 'Workbook', name: 'Weekly VaR', site: 'RQA', project: 'Risk Core',
        parse_quality: 0.83, views_90d: 40 },
      { id: 'wb3', type: 'Workbook', name: 'Liquidity Ladder', site: 'RQA',
        project: 'Treasury', parse_quality: 1, views_90d: 9 },
      { id: 't1', type: 'Table', name: 'positions' },
      { id: 't2', type: 'Table', name: 'prices' },
      { id: 'f1', type: 'Field', name: 'Notional' },
      { id: 'ds1', type: 'Datasource', name: 'Risk mart' },
    ],
    edges: [
      { source: 'wb1', target: 't1', type: 'REACHES' },
      { source: 'wb2', target: 't1', type: 'REACHES' },
      { source: 'wb1', target: 'f1', type: 'REACHES' },
      { source: 'wb3', target: 't2', type: 'REACHES' },
      { source: 'wb1', target: 'ds1', type: 'REACHES' },
    ],
    shared_lineage: [
      { source: 'wb1', target: 'wb2', strength: 0.71, jaccard_tables: 0.9,
        jaccard_fields: 0.5, shared_calc_shapes: 2, origin: 'computed' },
      { source: 'wb1', target: 'wb3', strength: 0.08, jaccard_tables: 0.1,
        jaccard_fields: 0.05, shared_calc_shapes: 0, origin: 'computed' },
    ],
    families: [],
    shared_lineage_origin: 'computed',
    colour_modes: [
      { key: 'type', label: 'Node type', available: true },
      { key: 'parse_status', label: 'Parse status', available: true },
      { key: 'family', label: 'Model family', available: true },
      { key: 'mu_state', label: 'Migration Unit state', available: false,
        reason: 'The §3.2 state machine begins when the Cartographer creates the MU (E3/F3.2).' },
    ],
    node_types: ['Workbook', 'Datasource', 'Table', 'Field', 'CalculatedField'],
    truncated: false,
    auto_scoped_to: null,
    workbook_count: 3,
    weights: { tables: 0.5, fields: 0.3, calc_shapes: 0.2, spec_ref: '§12.1' },
    read_ms: 42,
    ...overrides,
  };
}

export const FAMILY = {
  id: 'mf_risk',
  name: 'mf_risk_positions',
  state: 'PROPOSED',
  members: ['wb1', 'wb2'],
  size: 2,
};

export const CONSTRUCT = 'RAWSQL_INT(<expr>)';

export function queueResponse(overrides: Partial<QueueResponse> = {}): QueueResponse {
  const held = overrides.held ?? [
    {
      site: 'rqa',
      workbook_luid: 'wb-1',
      workbook_name: 'Daily VaR',
      project: 'Risk Core',
      parse_quality: 0.86,
      recognised: 6,
      ignorable: 0,
      total: 7,
      unrecognised_constructs: 1,
      grammar_version: 'fixture-1',
      harvested_at: '2027-03-01T00:00:00.000Z',
    },
    {
      site: 'rqa',
      workbook_luid: 'wb-2',
      workbook_name: 'Weekly VaR',
      project: 'Risk Core',
      parse_quality: 0.62,
      recognised: 5,
      ignorable: 0,
      total: 8,
      unrecognised_constructs: 3,
      grammar_version: 'fixture-1',
      harvested_at: '2027-03-01T00:00:00.000Z',
    },
  ];
  return { threshold: 0.98, held, count: held.length, ...overrides };
}

export function constructsResponse(
  overrides: Partial<ConstructsResponse> = {},
): ConstructsResponse {
  const constructs = overrides.constructs ?? [
    {
      construct: CONSTRUCT,
      occurrences: 12,
      workbooks: 9,
      workbooks_released_if_resolved: 6,
      sites: ['rqa', 'gtaa'],
      example_location: { site: 'rqa', workbook: 'Daily VaR', sheet: 'VaR by Desk' },
      unrecognised: true,
      issue: null,
    },
    {
      construct: 'WINDOW_SUM(<expr>)',
      occurrences: 3,
      workbooks: 2,
      // Every workbook holding this has other gaps too, so fixing it alone releases none.
      workbooks_released_if_resolved: 0,
      sites: ['rqa'],
      example_location: { site: 'rqa', workbook: 'Weekly VaR', sheet: null },
      unrecognised: true,
      issue: null,
    },
  ];
  return { threshold: 0.98, constructs, count: constructs.length, ...overrides };
}

export function programmeRecord(overrides: Partial<ProgrammeRecord> = {}): ProgrammeRecord {
  return {
    id: 'prg_01M1RQA',
    name: 'RQA migration',
    started_at: '2027-01-01T00:00:00.000Z',
    closed_at: null,
    open: true,
    retain_until: null,
    family_count: null,
    family_count_confirmed_at: null,
    family_count_confirmed_by: null,
    planned_family_count: 150,
    family_count_delta: null,
    ...overrides,
  };
}

export function programmesResponse(
  overrides: Partial<ProgrammesResponse> = {},
): ProgrammesResponse {
  return { programmes: overrides.programmes ?? [programmeRecord()] };
}

export function trainMember(overrides: Partial<TrainMember> = {}): TrainMember {
  return { id: 'wb1', name: 'Daily VaR', sequence: 1, state: 'CLUSTERED', ...overrides };
}

export function trainRecord(overrides: Partial<Train> = {}): Train {
  return {
    id: 'trn_one',
    name: 'Train 1',
    size: 0,
    members: [],
    planned_start: '2027-01-01',
    planned_end: '2027-01-31',
    actual_start: null,
    actual_end: null,
    gate_schedule: {
      G2: { planned_date: '2027-01-01', note: "family confirmation, clustered near the train's start" },
      G3: { planned_date: '2027-01-31', note: "MU acceptance, clustered near the train's end" },
    },
    wip_limits: null,
    overridden: false,
    override_action: null,
    override_reason: null,
    ...overrides,
  };
}

export function trainsResponse(overrides: Partial<TrainsResponse> = {}): TrainsResponse {
  const trains =
    overrides.trains ??
    [
      trainRecord({
        id: 'trn_one',
        name: 'Train 1',
        size: 2,
        members: [
          trainMember({ id: 'wb1', name: 'Daily VaR', sequence: 1 }),
          trainMember({ id: 'wb2', name: 'Weekly VaR', sequence: 2 }),
        ],
      }),
      trainRecord({
        id: 'trn_two',
        name: 'Train 2',
        size: 1,
        members: [trainMember({ id: 'wb3', name: 'Liquidity Ladder', sequence: 1 })],
      }),
    ];
  return { trains, count: trains.length };
}

export function trainProjection(overrides: Partial<TrainProjection> = {}): TrainProjection {
  return {
    train_id: 'trn_one',
    train_name: 'Train 1',
    planned_end: '2027-01-31',
    bottleneck_state: null,
    remaining_in_bottleneck: 0,
    projected_end: null,
    projected_end_early: null,
    projected_end_late: null,
    days_late: null,
    flagged: false,
    reason:
      "no measured throughput for any state this train's MUs currently occupy — nothing in "
      + 'this estate has yet transitioned an MU\'s state, so there is nothing to project from',
    ...overrides,
  };
}

export function trainProjectionsResponse(
  overrides: Partial<TrainProjectionsResponse> = {},
): TrainProjectionsResponse {
  const projections = overrides.projections ?? [
    trainProjection({ train_id: 'trn_one', train_name: 'Train 1' }),
    trainProjection({ train_id: 'trn_two', train_name: 'Train 2', planned_end: '2027-02-15' }),
  ];
  return {
    trailing_days: 14,
    late_threshold_working_days: 5,
    projections,
    flagged_count: projections.filter((p) => p.flagged).length,
    ...overrides,
  };
}

export function awaitingG2Review(overrides: Partial<AwaitingG2Review> = {}): AwaitingG2Review {
  return {
    family_id: 'fam_one',
    name: 'Risk Positions',
    domain: 'Risk',
    approver: 'owner@client.example',
    entered_review_at: '2027-03-01T09:00:00.000Z',
    days_waiting: 2,
    breached: false,
    open_questions: 1,
    ...overrides,
  };
}

export function awaitingG2Response(overrides: Partial<AwaitingG2Response> = {}): AwaitingG2Response {
  const reviews = overrides.reviews ?? [awaitingG2Review()];
  return {
    sla_working_days: 5,
    reviews,
    breached_count: reviews.filter((r) => r.breached).length,
    ...overrides,
  };
}

export function familyRecord(overrides: Partial<FamilyRecord> = {}): FamilyRecord {
  return {
    id: 'fam_one',
    name: 'Risk Positions',
    state: 'PROPOSED',
    domain: null,
    owner: null,
    grain: ['Desk', 'Trade Date'],
    conformed_dims: [],
    reason: null,
    members: ['wb1', 'wb2', 'wb3'],
    size: 3,
    evidence: { shared_tables: ['t1'], shared_fields: ['Desk'], shared_calc_shapes: 1 },
    overridden: false,
    override_action: null,
    override_reason: null,
    conformance_ruleset_version: null,
    ...overrides,
  };
}

export function familiesResponse(overrides: Partial<FamiliesResponse> = {}): FamiliesResponse {
  const families = overrides.families ?? [
    familyRecord({ id: 'fam_one', name: 'Risk Positions' }),
    familyRecord({ id: 'fam_two', name: 'Liquidity Ladder', size: 1, members: ['wb4'] }),
  ];
  return { families, count: families.length, ...overrides };
}

export function designDocument(overrides: Partial<DesignDocument> = {}): DesignDocument {
  return {
    family_id: 'fam_one',
    semantic_model_id: 'sem_one',
    grain_statement: 'One row per Desk and Trade Date.',
    design_generated_at: '2027-03-01T09:00:00.000Z',
    design_provenance_ref: 'prov_one',
    version: null,
    version_number: 1,
    state: null,
    published_at: null,
    deprecated_at: null,
    rls_roles: [],
    tables: [
      {
        id: 'mt_positions',
        name: 'positions',
        schema: 'risk',
        source_table_refs: ['t_positions'],
        mode: 'import',
        mode_reason: 'an extract already exists for this table',
        row_estimate: 5_000_000,
        custom_sql: false,
        family_ref: 'fam_one',
      },
      {
        id: 'mt_desk',
        name: 'desk',
        schema: 'risk',
        source_table_refs: ['t_desk'],
        mode: 'directquery',
        mode_reason: 'no extract exists for this connection',
        row_estimate: 40,
        custom_sql: false,
        family_ref: 'fam_one',
      },
    ],
    relationships: [
      {
        from_table: 'mt_desk',
        to_table: 'mt_positions',
        cardinality: 'one_to_many',
        confidence: 'row_estimate',
        reason: 'the from-table has 40 rows against 5,000,000 — small enough to call it the one side',
        join_clause: 'positions.desk_id = desk.id',
      },
    ],
    candidate_measures: [
      { name: 'Margin %', source_calc_refs: ['calc1', 'calc2'], dedup_decision: 'merged 2 calculations with an identical definition' },
    ],
    conformed_dimensions: [{ dimension: 'Desk', shared_with_family_ids: ['fam_two'] }],
    refresh_policy: {
      mode: 'scheduled',
      schedule: 'daily',
      extracted_source_count: 2,
      live_source_count: 0,
      distinct_schedules: ['daily'],
    },
    open_questions: [],
    rls_role_detail: [],
    ...overrides,
  };
}

export function familyTransition(overrides: Partial<FamilyTransition> = {}): FamilyTransition {
  return {
    from_state: null,
    to_state: 'PROPOSED',
    at: '2027-03-01T09:00:00.000Z',
    by: 'agent:cartographer',
    ...overrides,
  };
}

export function buildRecord(overrides: Partial<BuildRecord> = {}): BuildRecord {
  return {
    id: 'build_one',
    family_id: 'fam_one',
    version: 'sha256:abcdef0123456789',
    gate_decision_id: 'gd_one',
    state: 'SUCCEEDED',
    steps: [
      { name: 'emit', ok: true, detail: '3 file(s) emitted' },
      { name: 'commit', ok: true, detail: 'a1b2c3d on refs/heads/main' },
      { name: 'deploy', ok: true, detail: '' },
      { name: 'smoke:positions', ok: true, detail: 'structural check only' },
    ],
    git_commit_sha: 'a1b2c3d',
    git_ref: 'refs/heads/main',
    workspace: 'dev',
    triggered_by: 'agent:steward',
    started_at: '2027-04-01T09:00:00.000Z',
    finished_at: '2027-04-01T09:00:02.000Z',
    ...overrides,
  };
}

export function modelVersion(overrides: Partial<ModelVersion> = {}): ModelVersion {
  return {
    semantic_model_id: 'sem_one',
    version_number: 1,
    state: 'PUBLISHED',
    version: 'sha256:abcdef0123456789',
    design_generated_at: '2027-03-01T09:00:00.000Z',
    published_at: '2027-04-01T09:00:02.000Z',
    deprecated_at: null,
    ...overrides,
  };
}

export function versionsResponse(overrides: Partial<VersionsResponse> = {}): VersionsResponse {
  const versions = overrides.versions ?? [modelVersion()];
  return { family_id: 'fam_one', versions, ...overrides };
}

export function classMix(overrides: Partial<ClassMix> = {}): ClassMix {
  return {
    total: 20,
    unclassified: 20,
    counts: { C1: 0, C2: 0, C3: 0, C4: 0 },
    percentages: { C1: 0, C2: 0, C3: 0, C4: 0 },
    targets: { C1: 45, C2: 30, C3: 18, C4: 7 },
    classifier_version: null,
    ...overrides,
  };
}

export function movedClassification(overrides: Partial<MovedClassification> = {}): MovedClassification {
  return {
    calculated_field_id: 'calc_one',
    name: 'Margin %',
    from_class: null,
    to_class: 'C1',
    ...overrides,
  };
}

export function reclassifyResult(overrides: Partial<ReclassifyResult> = {}): ReclassifyResult {
  return {
    classifier_version: 1,
    total: 20,
    class_mix: { C1: 9, C2: 6, C3: 4, C4: 1 },
    moved: [movedClassification()],
    ...overrides,
  };
}

export function ruleCatalogEntry(overrides: Partial<RuleCatalogEntry> = {}): RuleCatalogEntry {
  return {
    id: 'c1_aggregate',
    version: 1,
    class: 'C1',
    family: 'aggregate',
    description: 'Aggregate functions map to their direct DAX equivalent.',
    guards: ['exactly one argument'],
    golden_case_count: 3,
    ...overrides,
  };
}

export function ruleCatalog(overrides: Partial<RuleCatalog> = {}): RuleCatalog {
  return { rules: [ruleCatalogEntry()], ...overrides };
}

export function ruleCoverage(overrides: Partial<RuleCoverage> = {}): RuleCoverage {
  return {
    total: 20,
    matched: 0,
    percentage: 0,
    by_family: {},
    rules_version: 1,
    ...overrides,
  };
}

export function appliedRule(overrides: Partial<AppliedRule> = {}): AppliedRule {
  return {
    calculated_field_id: 'calc_one',
    name: 'Total Notional',
    rule_id: 'c1_aggregate',
    family: 'aggregate',
    measure_id: 'msr_one',
    ...overrides,
  };
}

export function applyRulesResult(overrides: Partial<ApplyRulesResult> = {}): ApplyRulesResult {
  return {
    rules_version: 1,
    total: 20,
    matched: 1,
    by_family: { aggregate: 1 },
    applied: [appliedRule()],
    ...overrides,
  };
}

export const RULE_METADATA_FIXTURE: Record<string, { label: string; description: string; params: Record<string, string> }> = {
  star_schema: { label: 'Star schema only', description: 'No many-to-many without a bridge table.', params: {} },
  single_active_path: { label: 'Single active relationship path', description: 'No two tables reachable by more than one path.', params: {} },
  conformed_dimensions_by_reference: { label: 'Conformed dimensions shared by reference', description: 'Shared dimensions must not be imported (copied).', params: {} },
  measures_display_folder: { label: 'Measures in display folders by source family', description: 'Every measure name must be unique within its family.', params: {} },
  naming_convention: { label: 'Naming convention', description: 'Names must be non-blank and TMDL-safe.', params: { max_length: 'Maximum name length' } },
  rls_fixture_user: { label: 'RLS roles tested with a fixture user', description: 'Every role must name a field and a recognised function.', params: { fixture_username: 'Fixture identity' } },
};

export function conformanceRuleset(overrides: Partial<ConformanceRuleset> = {}): ConformanceRuleset {
  return {
    version: 1,
    rules: [
      { rule_id: 'star_schema', enabled: true, params: {} },
      { rule_id: 'single_active_path', enabled: true, params: {} },
      { rule_id: 'conformed_dimensions_by_reference', enabled: true, params: {} },
      { rule_id: 'measures_display_folder', enabled: true, params: {} },
      { rule_id: 'naming_convention', enabled: true, params: { max_length: 100 } },
      { rule_id: 'rls_fixture_user', enabled: true, params: { fixture_username: 'fixture.user@astra.local' } },
    ],
    updated_by: 'user:architect@artizent.example',
    updated_at: '2027-05-01T09:00:00.000Z',
    ...overrides,
  };
}

export const DEFAULT_CHARTER: ToleranceCharter = {
  numeric: { abs_epsilon: 0.005, rel_epsilon: 1e-6, rounding: 'HALF_EVEN', currency_scale: 2 },
  nulls: { source_null_vs_target_zero: 'FAIL', source_null_vs_target_blank: 'PASS', empty_string_is_null: true },
  dates: { grain_alignment: 'TRUNCATE_TO_SOURCE_GRAIN', timezone: 'UTC', fiscal_year_start: 1 },
  strings: { trim: true, case_sensitive: false, collation: 'en-US' },
  ordering: { sort_sensitive: false, top_n_tie_break: 'SOURCE_ORDER' },
  rows: { missing_key: 'FAIL', extra_key: 'FAIL', row_count_tolerance: 0, max_failing_cells: 50 },
  sampling: { full_compare_max_rows: 200_000, sample_rows: 50_000, stratify_by: 'grain' },
  params: { enumerate_max_values: 12, enumerate_strategy: 'DEFAULT_PLUS_OBSERVED' },
  waiver: { allowed_classes: ['C4'], requires: ['engineer', 'client_owner'], justification_min_chars: 120 },
};

export const CHARTER_FIELD_METADATA_FIXTURE: ToleranceCharterFieldMetadata = {
  numeric: {
    abs_epsilon: 'Two numbers pass if they differ by no more than this absolute amount.',
    rel_epsilon: 'Two numbers also pass within this fraction of the larger magnitude.',
    rounding: 'How a value is rounded before comparison.',
    currency_scale: 'Decimal places a currency value is rounded to before comparison.',
  },
  nulls: {
    source_null_vs_target_zero: 'Verdict when the source is null and the target is zero.',
    source_null_vs_target_blank: 'Verdict when the source is null and the target is blank.',
    empty_string_is_null: 'Treat an empty string as null before every other null rule.',
  },
  dates: {
    grain_alignment: "How a date is truncated to the case's own grain before comparison.",
    timezone: 'The timezone both sides are normalised to before comparing.',
    fiscal_year_start: 'The calendar month a fiscal year begins in.',
  },
  strings: {
    trim: 'Strip leading/trailing whitespace before comparing.',
    case_sensitive: 'Whether case differences fail the comparison.',
    collation: 'The collation used to compare and sort strings.',
  },
  ordering: {
    sort_sensitive: 'Whether row order itself is part of what is compared.',
    top_n_tie_break: 'How a tie at the boundary of a top-N result is resolved.',
  },
  rows: {
    missing_key: 'Verdict when a source key is absent on the target.',
    extra_key: 'Verdict when a target key is absent on the source.',
    row_count_tolerance: 'How many rows the two sides may differ by and still pass.',
    max_failing_cells: "How many failing cells a FAIL verdict's evidence bundle keeps, first N.",
  },
  sampling: {
    full_compare_max_rows: 'Below this row count, every row is compared.',
    sample_rows: 'Above the threshold, this many rows are sampled instead.',
    stratify_by: 'The field a sample is stratified by.',
  },
  params: {
    enumerate_max_values: 'The most parameter-value combinations a derivation will enumerate.',
    enumerate_strategy: 'Which combinations are chosen when there are more than the maximum.',
  },
  waiver: {
    allowed_classes: 'Which failure classes may ever be waived rather than fixed.',
    requires: 'Which parties must all sign a waiver before it is accepted.',
    justification_min_chars: "The minimum length a waiver's own justification must have.",
  },
};

export function toleranceCharterVersion(overrides: Partial<ToleranceCharterVersion> = {}): ToleranceCharterVersion {
  return {
    version: 0,
    charter: DEFAULT_CHARTER,
    updated_by: 'system',
    updated_at: null,
    ...overrides,
  };
}

export function patternRecord(overrides: Partial<PatternRecord> = {}): PatternRecord {
  const provenance: PatternProvenance = {
    origin: 'PROMOTED_FROM_LLM',
    first_seen: 'calc_running_total',
    ...overrides.provenance,
  };
  return {
    id: 'pat_running_sum',
    name: 'pattern_RUNNING_SUM(SUM(a))',
    class: 'C3',
    promotion_state: 'CANDIDATE',
    target_template: 'CALCULATE(SUM({a}))',
    guards: ['a is real'],
    applications: 2,
    pass_total: 2,
    distinct_passing_calcs: 2,
    failure_count: 0,
    version: 1,
    supersedes_id: null,
    ...overrides,
    provenance,
  };
}

export function patternsResponse(overrides: Partial<PatternsResponse> = {}): PatternsResponse {
  const patterns = overrides.patterns ?? [patternRecord()];
  return { patterns, count: patterns.length, ...overrides };
}

export function g2Question(overrides: Partial<G2Question> = {}): G2Question {
  return {
    id: 'q_one',
    family_id: 'fam_one',
    category: 'ambiguous_key',
    question: "table 'positions' is sourced from custom SQL; confirm its grain and keys",
    state: 'OPEN',
    evidence: {},
    thread: [],
    asked_by: 'agent:modeller',
    asked_at: '2027-03-01T09:00:00.000Z',
    answered_by: null,
    answered_at: null,
    ...overrides,
  };
}

export function modelProposal(overrides: Partial<ModelProposal> = {}): ModelProposal {
  const open_questions = overrides.open_questions ?? [g2Question()];
  return {
    family_id: 'fam_one',
    name: 'Risk Positions',
    domain: null,
    state: 'IN_REVIEW',
    grain_statement: 'One row per Desk and Trade Date.',
    plain_summary:
      'This model brings together 2 tables into 1 measure. No row-level security is applied — '
      + 'everyone with access sees every row. Data refreshes on a daily schedule.',
    reports: ['Daily VaR', 'Weekly VaR'],
    version: 'sha256:abcdef0123456789',
    open_questions,
    unanswered_count: open_questions.filter((q) => q.state === 'OPEN').length,
    ...overrides,
  };
}

export function failingCellRow(overrides: Partial<FailingCellRow> = {}): FailingCellRow {
  return {
    case_id: 'case_1',
    grain_key: ['EMEA'],
    measure: 'Margin',
    kind: 'numeric',
    expected: 100.0,
    candidate: 101.2,
    delta: 1.2,
    reason: 'numeric epsilon exceeded',
    filter_ctx: { kind: 'default', filters: [] },
    ...overrides,
  };
}

export function sheetParityStats(overrides: Partial<SheetParityStats> = {}): SheetParityStats {
  return {
    sheet_ref: 'sheet_1',
    sheet_name: 'Bar sheet',
    visual_id: 'vis_1',
    cases_run: 4,
    pass: 3,
    fail: 1,
    inconclusive: 0,
    waived_count: 0,
    first_pass_rate: 0.75,
    failing_cells: [failingCellRow()],
    structural_score: 0.86,
    structural_score_breakdown: {
      score: 0.86, mark_type: 1.0, encodings: 1.0, axes: 1.0, sort: 1.0, reference_lines: 0.0,
    },
    image_score: 0.93,
    visual_score_computed_at: '2027-06-01T09:00:00.000Z',
    source_screenshot_ref: 'af_screenshot',
    target_render_ref: 'af_render',
    ...overrides,
  };
}

export function parityRunTrendEntry(overrides: Partial<ParityRunTrendEntry> = {}): ParityRunTrendEntry {
  return {
    run_id: 'run_1',
    started: '2027-06-01T09:00:00.000Z',
    finished: '2027-06-01T09:00:02.000Z',
    charter_version: '1',
    cases: 4,
    pass: 3,
    fail: 1,
    inconclusive: 0,
    pass_rate: 0.75,
    ...overrides,
  };
}

export function parityDashboardResponse(
  overrides: Partial<ParityDashboardResponse> = {},
): ParityDashboardResponse {
  return {
    workbook_id: '01ARZ3NDEKTSV4RRFFQ69G5FAV',
    charter_version: '1',
    passes_the_charter: false,
    latest_run_id: 'run_1',
    sheets: overrides.sheets ?? [sheetParityStats()],
    trend: overrides.trend ?? {
      runs: [parityRunTrendEntry()],
      mender_passes: {
        available: false,
        detail: 'no ExceptionCase for this workbook has closed through the Mender yet',
      },
    },
    ...overrides,
  };
}

export function verdictRow(overrides: Partial<VerdictRow> = {}): VerdictRow {
  return {
    id: 'v_1',
    case_ref: 'case_1',
    result: 'FAIL',
    failing_cells: [
      { grain_key: ['EMEA'], measure: 'Margin', kind: 'numeric', expected: 100.0, candidate: 101.2, delta: 1.2, reason: 'numeric epsilon exceeded' },
    ],
    evidence_ref: 'af_1',
    sampled: false,
    ...overrides,
  };
}

export function parityRunResponse(overrides: Partial<ParityRunResponse> = {}): ParityRunResponse {
  const verdicts = overrides.verdicts ?? [
    verdictRow(),
    verdictRow({ id: 'v_2', case_ref: 'case_2', result: 'PASS', failing_cells: [] }),
  ];
  return {
    run_id: 'run_1',
    workbook_id: '01ARZ3NDEKTSV4RRFFQ69G5FAV',
    suite_ref: '01ARZ3NDEKTSV4RRFFQ69G5FAV',
    charter_version: '1',
    started: '2027-06-01T09:00:00.000Z',
    finished: '2027-06-01T09:00:02.000Z',
    verdicts,
    ...overrides,
  };
}

export function runParityResult(overrides: Partial<RunParityResult> = {}): RunParityResult {
  return {
    workbook_id: '01ARZ3NDEKTSV4RRFFQ69G5FAV',
    run_id: 'run_2',
    charter_version: '1',
    cases_diffed: 4,
    pass: 4,
    fail: 0,
    inconclusive: 0,
    proved_by_report: null,
    results: [],
    ...overrides,
  };
}

export function runVisualParityResult(
  overrides: Partial<RunVisualParityResult> = {},
): RunVisualParityResult {
  return {
    workbook_id: '01ARZ3NDEKTSV4RRFFQ69G5FAV',
    visuals_scored: 1,
    results: [
      { visual_id: 'vis_1', sheet_ref: 'sheet_1', structural_score: 0.86, image_score: 0.93 },
    ],
    ...overrides,
  };
}

// A one-pixel PNG, the same disclosed placeholder the fixture adapters return.
const ONE_PIXEL_PNG_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAACklEQVR4nGMAAQAABQABDQottAAAAABJRU5ErkJggg==';

export function visualCapturePair(overrides: Partial<VisualCapturePair> = {}): VisualCapturePair {
  return {
    visual_id: 'vis_1',
    source: { media_type: 'image/png', content_base64: ONE_PIXEL_PNG_BASE64 },
    target: { media_type: 'image/png', content_base64: ONE_PIXEL_PNG_BASE64 },
    ...overrides,
  };
}

export function regressionScheduleRecord(
  overrides: Partial<RegressionScheduleRecord> = {},
): RegressionScheduleRecord {
  return {
    id: 'rs_1',
    workbook_id: '01ARZ3NDEKTSV4RRFFQ69G5FAV',
    workspace: 'dev',
    cadence: { every_minutes: 10080 },
    cadence_description: 'every 10080 minutes',
    enabled: true,
    paused_reason: null,
    next_run_at: '2027-06-08T09:00:00.000Z',
    last_run: { id: 'run_9', at: '2027-06-01T09:00:00.000Z', result: 'PASS', error: null },
    consecutive_failures: 0,
    created_by: 'user:pm@artizent.example',
    created_at: '2027-06-01T08:00:00.000Z',
    ...overrides,
  };
}

export function regressionMonitorRow(
  overrides: Partial<RegressionMonitorRow> = {},
): RegressionMonitorRow {
  return {
    workbook_id: '01ARZ3NDEKTSV4RRFFQ69G5FAV',
    workbook_name: 'Daily VaR',
    schedule: overrides.schedule === undefined ? regressionScheduleRecord() : overrides.schedule,
    last_result: 'PASS',
    drift_alert: overrides.drift_alert ?? { unaddressed: false, last_drift_at: null },
    ...overrides,
  };
}

export function regressionMonitorResponse(
  overrides: Partial<RegressionMonitorResponse> = {},
): RegressionMonitorResponse {
  const workbooks = overrides.workbooks ?? [regressionMonitorRow()];
  return { workbooks, count: workbooks.length, ...overrides };
}

export function exceptionQueueEntry(overrides: Partial<ExceptionQueueEntry> = {}): ExceptionQueueEntry {
  return {
    id: 'exc_1',
    mu_ref: '01ARZ3NDEKTSV4RRFFQ69G5FAV',
    class: 'AGGREGATION',
    passes_consumed: 1,
    assignee: null,
    state: 'OPEN',
    train_id: 'trn_one',
    train_sequence: 1,
    site: 'RQA',
    created_at: '2027-06-01T09:00:00.000Z',
    age_seconds: 3600,
    ...overrides,
  };
}

export function exceptionQueueResponse(
  overrides: Partial<ExceptionQueueResponse> = {},
): ExceptionQueueResponse {
  const entries = overrides.entries ?? [exceptionQueueEntry()];
  return { entries, count: entries.length, ...overrides };
}

export function exceptionCaseDetail(overrides: Partial<ExceptionCaseDetail> = {}): ExceptionCaseDetail {
  return {
    id: 'exc_1',
    mu_ref: '01ARZ3NDEKTSV4RRFFQ69G5FAV',
    class: 'AGGREGATION',
    state: 'OPEN',
    passes_consumed: 1,
    assignee: null,
    decision: null,
    train_id: 'trn_one',
    train_sequence: 1,
    site: 'RQA',
    created_at: '2027-06-01T09:00:00.000Z',
    evidence: {
      failing_cells: [
        {
          case_ref: 'case_1', grain_key: ['EMEA'], measure: 'Margin',
          expected: 100.0, candidate: 101.2, delta: 1.2,
        },
      ],
      missing_keys: [['Desk-9', 'case_1']],
      extra_keys: [['Desk-8', 'case_1']],
      filter_ctx: { kind: 'default' },
      param_values: { case_1: { AsOf: '2027-06-01' } },
    },
    artefact: {
      calc_id: 'calc_1',
      calc_name: 'Margin Calc',
      source_formula: 'SUM([Margin])',
      measure_id: 'msr_1',
      current_dax: 'SUM([WrongField])',
      current_m_query: null,
    },
    mender_passes: [
      {
        id: 'mp_1', pass_number: 1, strategy: 'PATTERN', result: 'STILL_FAILING',
        measure_ref: 'msr_0', cases_reproved: [], cases_still_failing: ['case_1'],
        evidence_ref: 'af_1', started_at: '2027-06-01T09:00:00.000Z', finished_at: '2027-06-01T09:00:01.000Z',
      },
    ],
    ...overrides,
  };
}

export function exceptionAgeingResponse(
  overrides: Partial<ExceptionAgeingResponse> = {},
): ExceptionAgeingResponse {
  return {
    open_by_class_and_age_band: [
      { class: 'AGGREGATION', age_band: 'under_1d', age_band_label: 'under 1 day', count: 2 },
      { class: 'KEY_MISSING', age_band: '7d_plus', age_band_label: '7+ days', count: 1 },
    ],
    age_bands: [
      { key: 'under_1d', label: 'under 1 day' },
      { key: '1_3d', label: '1-3 days' },
      { key: '3_7d', label: '3-7 days' },
      { key: '7d_plus', label: '7+ days' },
    ],
    total_open: 3,
    mender_close_rate: {
      mender_closed: 7, total_failures: 10, rate: 0.7, target: 0.7, meets_target: true,
    },
    ...overrides,
  };
}

export function acceptanceSummary(overrides: Partial<AcceptanceSummary> = {}): AcceptanceSummary {
  const by_tier = overrides.by_tier ?? [
    { tier: 'SIMPLE', accepted: 3, planned: 70, delta: -67, unit_price: 8_000, accepted_value: 24_000 },
    { tier: 'MODERATE', accepted: 1, planned: 50, delta: -49, unit_price: 15_000, accepted_value: 15_000 },
    { tier: 'COMPLEX', accepted: 0, planned: 20, delta: -20, unit_price: 28_000, accepted_value: 0 },
    { tier: 'REDESIGN', accepted: 0, planned: 10, delta: -10, unit_price: 40_000, accepted_value: 0 },
  ];
  return {
    by_tier,
    total_accepted: by_tier.reduce((sum, row) => sum + row.accepted, 0),
    total_planned: by_tier.reduce((sum, row) => sum + row.planned, 0),
    total_accepted_value: by_tier.reduce((sum, row) => sum + row.accepted_value, 0),
    ...overrides,
  };
}

export function promotionRecord(overrides: Partial<PromotionRecord> = {}): PromotionRecord {
  return {
    id: 'promotion_1',
    workbook_id: '01ARZ3NDEKTSV4RRFFQ69G5FAV',
    to_stage: 'test',
    workspace: 'test',
    state: 'SUCCEEDED',
    steps: [
      { name: 'report deploy', ok: true, detail: 'ok' },
      { name: 'model deploy', ok: true, detail: 'deploy_1' },
    ],
    model_git_ref: 'refs/heads/main',
    report_deploy_id: 'deploy_1',
    approved_by: 'user:p.eng@artizent.example',
    approver_role: 'platform_engineer',
    rationale: null,
    triggered_by: 'user:p.eng@artizent.example',
    started_at: '2027-06-01T09:00:00.000Z',
    finished_at: '2027-06-01T09:00:02.000Z',
    ...overrides,
  };
}

export function releaseBoardMu(overrides: Partial<ReleaseBoardMu> = {}): ReleaseBoardMu {
  return {
    workbook_id: '01ARZ3NDEKTSV4RRFFQ69G5FAV',
    name: 'Daily VaR',
    sequence: 1,
    stage: 'ACCEPTED',
    next_stage: 'test',
    blockers: [],
    evidence: [],
    ...overrides,
  };
}

export function releaseBoard(overrides: Partial<ReleaseBoardData> = {}): ReleaseBoardData {
  return {
    trains: overrides.trains ?? [
      { id: 'trn_one', name: 'Train 1', mus: [releaseBoardMu()] },
    ],
    sites: overrides.sites ?? [
      {
        site_id: 'site-rqa', name: 'RQA', released_mu_count: 0, total_mu_count: 1,
        parallel_run_start: null, parallel_run_end: null,
      },
    ],
    ...overrides,
  };
}

export function g3Card(overrides: Partial<G3Card> = {}): G3Card {
  return {
    workbook_id: '01ARZ3NDEKTSV4RRFFQ69G5FAV',
    what: { name: 'Daily VaR', site: 'RQA', pages: 2, visuals: 6 },
    proof: {
      cases_run: 41, cases_pass: 41, charter_version: '3', sampled: false,
      passes_the_charter: true, waivers: [],
    },
    visual: { structural_score: 0.96, image_score: 0.91, reviewed: [], human_review_status: 'not yet reviewed' },
    changes: {
      c4_decisions: [], redesigns: [],
      model: { family_id: 'fam_risk', name: 'mf_risk_positions', state: 'APPROVED', approved_at: '2027-01-09T00:00:00.000Z' },
    },
    next: {
      on_approval: 'promote to test, then a parallel run before your sign-off',
      parallel_window_weeks: 4,
    },
    latest_decision: null,
    ...overrides,
  };
}

export function g3Question(overrides: Partial<G3Question> = {}): G3Question {
  return {
    id: 'g3q_1',
    workbook_id: '01ARZ3NDEKTSV4RRFFQ69G5FAV',
    question: 'Why was the table-calc visual redesigned?',
    asked_by: 'user:owner@client.example',
    asked_at: '2027-01-14T00:00:00.000Z',
    ...overrides,
  };
}

export const RAISED_ISSUE: ConstructIssue = {
  id: 'gi_01M1',
  state: 'OPEN',
  opened_by: 'user:p.eng@artizent.example',
  opened_at: '2027-03-02T09:00:00.000Z',
  external: { ref: null, url: null },
};

export interface FakeApi extends Api {
  readonly calls: {
    estate: EstateQuery[];
    workbook: string[];
    lineage: LineageQuery[];
    quality: number;
  };
  readonly recorded: { kind: string; id: string; reason: string; tier?: string }[];
  failNext(error: ApiError): void;
}

export function fakeApi(
  response: EstateResponse = estateResponse(),
  lineage: LineageResponse = lineageResponse(),
  quality: { queue?: QueueResponse; constructs?: ConstructsResponse } = {},
  programmes: ProgrammesResponse = programmesResponse(),
  trains: TrainsResponse = trainsResponse(),
  projections: TrainProjectionsResponse = trainProjectionsResponse(),
  families: FamiliesResponse = familiesResponse(),
  designs: Record<string, DesignDocument> = {},
  questions: Record<string, G2Question[]> = {},
  awaitingG2: AwaitingG2Response = awaitingG2Response({ reviews: [] }),
  builds: Record<string, BuildRecord> = {},
  initialRuleset: ConformanceRuleset = conformanceRuleset(),
  versions: Record<string, ModelVersion[]> = {},
  initialClassMix: ClassMix = classMix(),
  initialRuleCoverage: RuleCoverage = ruleCoverage(),
  initialPatterns: PatternsResponse = patternsResponse(),
  initialCharter: ToleranceCharterVersion = toleranceCharterVersion(),
  initialParityDashboard: ParityDashboardResponse | null = parityDashboardResponse(),
  initialParityRun: ParityRunResponse | null = parityRunResponse(),
  initialRegressionMonitor: RegressionMonitorResponse = regressionMonitorResponse({ workbooks: [] }),
  initialExceptionCases: ExceptionCaseDetail[] = [],
  initialAcceptanceSummary: AcceptanceSummary = acceptanceSummary(),
  initialReleaseBoard: ReleaseBoardData = releaseBoard(),
): FakeApi {
  const calls: FakeApi['calls'] = { estate: [], workbook: [], lineage: [], quality: 0 };
  const recorded: FakeApi['recorded'] = [];
  let queued: ApiError | null = null;
  let charterState: ToleranceCharterVersion = { ...initialCharter, charter: { ...initialCharter.charter } };
  const parityDashboardState: ParityDashboardResponse | null = initialParityDashboard;
  const parityRunState: ParityRunResponse | null = initialParityRun;
  const regressionMonitorRows = initialRegressionMonitor.workbooks.map((row) => ({ ...row }));
  let regressionScheduleSeq = regressionMonitorRows.length;
  let releaseBoardState: ReleaseBoardData = {
    trains: initialReleaseBoard.trains.map((t) => ({
      ...t, mus: t.mus.map((mu) => ({ ...mu, blockers: [...mu.blockers], evidence: [...mu.evidence] })),
    })),
    sites: initialReleaseBoard.sites.map((s) => ({ ...s })),
  };
  const exceptionCaseRows = initialExceptionCases.map((c) => ({ ...c }));
  const findExceptionCase = (exceptionCaseId: string): ExceptionCaseDetail => {
    const found = exceptionCaseRows.find((c) => c.id === exceptionCaseId);
    if (!found) throw new ApiError(404, 'not_found', `no ExceptionCase '${exceptionCaseId}'`);
    return found;
  };
  const g3CardState = new Map<string, G3Card>();
  const g3QuestionRows: G3Question[] = [];
  let g1Approved = false;
  const programmeRows = programmes.programmes.map((row) => ({ ...row }));
  const trainRows = trains.trains.map((train) => ({
    ...train,
    members: train.members.map((member) => ({ ...member })),
  }));
  const familyRows = families.families.map((family) => ({ ...family }));
  const designRows = new Map<string, DesignDocument>(
    Object.entries(designs).map(([id, doc]) => [id, { ...doc }]),
  );
  const transitionRows = new Map<string, FamilyTransition[]>();
  for (const family of familyRows) {
    transitionRows.set(family.id, [familyTransition({ to_state: family.state ?? 'PROPOSED' })]);
  }

  const findFamily = (familyId: string) => {
    const family = familyRows.find((f) => f.id === familyId);
    if (!family) throw new ApiError(404, 'not_found', `no model family '${familyId}'`);
    return family;
  };
  const findDesign = (familyId: string) => {
    const design = designRows.get(familyId);
    if (!design) {
      throw new ApiError(
        404, 'not_found', `no design proposal has been generated for family '${familyId}' yet`,
      );
    }
    return design;
  };
  const requireDraft = (familyId: string) => {
    const family = findFamily(familyId);
    if (family.state !== 'DRAFT') {
      throw new ApiError(
        400, 'invalid_request',
        `family '${familyId}' is '${family.state}'; editing a design proposal is only available while a family is DRAFT`,
      );
    }
  };
  const recordTransition = (familyId: string, from: string | null, to: string, by: string) => {
    const list = transitionRows.get(familyId) ?? [];
    list.push(familyTransition({ from_state: from, to_state: to, by, at: new Date().toISOString() }));
    transitionRows.set(familyId, list);
  };
  const questionRows = new Map<string, G2Question[]>(
    Object.entries(questions).map(([id, list]) => [id, list.map((q) => ({ ...q }))]),
  );
  const cycleCounts = new Map<string, number>();
  const reviewRows = awaitingG2.reviews.map((review) => ({ ...review }));
  const sentReminderDays = new Map<string, Set<number>>();
  const buildRows = new Map<string, BuildRecord>(
    Object.entries(builds).map(([id, build]) => [id, { ...build }]),
  );
  const versionRows = new Map<string, ModelVersion[]>(
    Object.entries(versions).map(([id, list]) => [id, list.map((v) => ({ ...v }))]),
  );
  let rulesetState: ConformanceRuleset = { ...initialRuleset, rules: initialRuleset.rules.map((r) => ({ ...r })) };
  let classMixState: ClassMix = { ...initialClassMix, counts: { ...initialClassMix.counts }, percentages: { ...initialClassMix.percentages }, targets: { ...initialClassMix.targets } };
  let ruleCoverageState: RuleCoverage = { ...initialRuleCoverage, by_family: { ...initialRuleCoverage.by_family } };
  const patternRows: PatternRecord[] = initialPatterns.patterns.map((p) => ({
    ...p,
    guards: [...p.guards],
    provenance: { ...p.provenance },
  }));
  let patternVersionCounter = patternRows.length;
  const findPattern = (patternId: string): PatternRecord => {
    const pattern = patternRows.find((p) => p.id === patternId);
    if (!pattern) throw new ApiError(404, 'not_found', `no Pattern '${patternId}'`);
    return pattern;
  };
  const findQuestion = (questionId: string): G2Question => {
    for (const list of questionRows.values()) {
      const found = list.find((q) => q.id === questionId);
      if (found) return found;
    }
    throw new ApiError(404, 'not_found', `no G2 question '${questionId}'`);
  };

  const maybeFail = (): void => {
    if (queued) {
      const error = queued;
      queued = null;
      throw error;
    }
  };

  return {
    calls,
    recorded,
    failNext(error) {
      queued = error;
    },
    async estate(query: EstateQuery, _identity: Identity) {
      calls.estate.push(query);
      return response;
    },
    async workbook(id: string, _identity: Identity) {
      calls.workbook.push(id);
      // The detail has to agree with the row: the panel prefers the detail's scope state
      // because it is fresher, so a fixture that disagreed would decide the test.
      const row = response.workbooks.find((candidate) => candidate.id === id);
      return workbookDetail({
        workbook: {
          id,
          type: 'Workbook',
          properties: { name: row?.name ?? 'Daily VaR', luid: row?.luid ?? 'wb-daily-var' },
        },
        scope: {
          decisions: [],
          current: {
            tier: row?.tier ?? null,
            tier_reason: null,
            withdrawn: row?.withdrawn ?? false,
            withdrawn_reason: row?.withdrawn_reason ?? null,
          },
        },
      });
    },
    async reTier(id, tier, reason) {
      maybeFail();
      recorded.push({ kind: 'RE_TIER', id, reason, tier });
      return {};
    },
    async withdraw(id, reason) {
      maybeFail();
      recorded.push({ kind: 'WITHDRAW', id, reason });
      return {};
    },
    async reinstate(id, reason) {
      maybeFail();
      recorded.push({ kind: 'REINSTATE', id, reason });
      return {};
    },
    async reharvest(site: string) {
      maybeFail();
      recorded.push({ kind: 'HARVEST', id: site, reason: '' });
      return { id: '01M1HARVEST' };
    },
    async lineage(query) {
      calls.lineage.push(query);
      return lineage;
    },
    async parseQualityQueue() {
      calls.quality += 1;
      return quality.queue ?? queueResponse();
    },
    async constructs() {
      return quality.constructs ?? constructsResponse();
    },
    async markIgnorable(construct, reason) {
      maybeFail();
      recorded.push({ kind: 'IGNORABLE', id: construct, reason });
      return { workbooks_released: 6, workbooks_rescored: 8 };
    },
    async openGrammarIssue(construct, summary, detail) {
      maybeFail();
      recorded.push({ kind: 'ISSUE', id: construct, reason: detail, tier: summary });
      return {
        id: 'gi_new',
        construct,
        summary: summary || `Grammar cannot read ${construct}`,
        detail,
        state: 'OPEN',
        active: true,
        locations: [{ site: 'rqa', workbook: 'Daily VaR', sheet: 'VaR by Desk' }],
        occurrences_when_raised: 12,
        workbooks_held_when_raised: 8,
        external: { ref: null, url: null },
        opened_by: 'user:p.eng@artizent.example',
        opened_at: '2027-03-02T09:00:00.000Z',
      };
    },
    async programmes() {
      return { programmes: programmeRows };
    },
    async confirmFamilyCount(programmeId, identity) {
      maybeFail();
      const index = programmeRows.findIndex((row) => row.id === programmeId);
      if (index === -1) {
        throw new ApiError(404, 'not_found', `no programme '${programmeId}'`);
      }
      const measured = 142; // a fixed, deliberately-not-150 stand-in for "read live"
      const updated: ProgrammeRecord = {
        ...programmeRows[index]!,
        family_count: measured,
        family_count_confirmed_at: '2027-04-01T09:00:00.000Z',
        family_count_confirmed_by: identity.principal,
        family_count_delta: measured - programmeRows[index]!.planned_family_count,
      };
      programmeRows[index] = updated;
      recorded.push({ kind: 'CONFIRM_FAMILY_COUNT', id: programmeId, reason: '' });
      return updated;
    },
    async trains() {
      // A fresh array (and fresh member arrays) every call, the same as a real HTTP
      // response would be — a component that stores this in state and calls setState
      // with the exact same reference back (as the real fake used to, mutating
      // trainRows in place) never re-renders: React bails out on an Object.is-equal
      // value. Found by a test that moved a card and never saw it land.
      return {
        trains: trainRows.map((train) => ({ ...train, members: train.members.map((m) => ({ ...m })) })),
        count: trainRows.length,
      };
    },
    async moveMember(trainId, workbookId, _identity, reason) {
      maybeFail();
      const target = trainRows.find((t) => t.id === trainId);
      if (!target) throw new ApiError(404, 'not_found', `no release train '${trainId}'`);
      const source = trainRows.find((t) => t.members.some((m) => m.id === workbookId));
      if (!source) {
        throw new ApiError(
          400,
          'invalid_request',
          `workbook '${workbookId}' is not currently IN_TRAIN any train`,
        );
      }
      if (source.id === trainId) {
        throw new ApiError(400, 'invalid_request', `workbook '${workbookId}' is already in '${trainId}'`);
      }

      const trainLimit = target.wip_limits?.train ?? null;
      const nextCount = target.members.length + 1;
      const exceeded = trainLimit != null && nextCount > trainLimit;
      if (exceeded && !reason) {
        throw new ApiError(
          400,
          'invalid_request',
          `moving '${workbookId}' into '${trainId}' would exceed its configured WIP limit — resubmit with a reason to proceed anyway`,
        );
      }

      const memberIndex = source.members.findIndex((m) => m.id === workbookId);
      const [member] = source.members.splice(memberIndex, 1);
      const moved = { ...member!, sequence: target.members.length + 1 };
      target.members.push(moved);
      source.size = source.members.length;
      target.size = target.members.length;
      recorded.push({ kind: 'MOVE_MEMBER', id: workbookId, reason: reason ?? '' });
      return {
        workbook_id: workbookId,
        from_train_id: source.id,
        to_train_id: trainId,
        state: moved.state,
        sequence: moved.sequence,
        wip:
          trainLimit != null
            ? { train_limit: trainLimit, train_count: nextCount, state_limit: null, state_count: 0, exceeded }
            : null,
      };
    },
    async resequenceMember(_trainId, workbookId, position) {
      maybeFail();
      const train = trainRows.find((t) => t.members.some((m) => m.id === workbookId));
      if (!train) {
        throw new ApiError(
          400,
          'invalid_request',
          `workbook '${workbookId}' is not currently IN_TRAIN any train`,
        );
      }
      const byId = new Map(train.members.map((m) => [m.id, m]));
      const order = train.members.map((m) => m.id).filter((id) => id !== workbookId);
      const index = Math.min(position - 1, order.length);
      order.splice(index, 0, workbookId);
      train.members = order.map((id, i) => ({ ...byId.get(id)!, sequence: i + 1 }));
      recorded.push({ kind: 'RESEQUENCE_MEMBER', id: workbookId, reason: '' });
      return { train_id: train.id, workbook_id: workbookId, position: index + 1 };
    },
    async setWipLimits(trainId, trainLimit, stateLimits, reason) {
      maybeFail();
      const train = trainRows.find((t) => t.id === trainId);
      if (!train) throw new ApiError(404, 'not_found', `no release train '${trainId}'`);
      train.wip_limits = { train: trainLimit, states: stateLimits };
      train.overridden = true;
      train.override_action = 'WIP_LIMITS';
      train.override_reason = reason;
      recorded.push({ kind: 'SET_WIP_LIMITS', id: trainId, reason });
      return { train_id: trainId, wip_limits: train.wip_limits };
    },
    async trainEvents(trainId) {
      const events: TrainEvent[] = [
        {
          sequence: 1,
          event: { subject: trainId, type: 'astra.data.node.upserted', time: '2027-01-01T00:00:00.000Z' },
        },
      ];
      return { train_id: trainId, events, window: 2000 };
    },
    async trainProjections() {
      return {
        ...projections,
        projections: projections.projections.map((p) => ({ ...p })),
      };
    },
    async families() {
      return { families: familyRows.map((f) => ({ ...f })), count: familyRows.length };
    },
    async family(familyId: string) {
      return { ...findFamily(familyId) };
    },
    async proposeDesign(familyId: string) {
      maybeFail();
      const family = findFamily(familyId);
      if (family.state !== 'PROPOSED' && family.state !== 'SINGLETON') {
        throw new ApiError(
          400, 'invalid_request',
          `family '${familyId}' has already been accepted (state: '${family.state}')`,
        );
      }
      const doc = designDocument({ family_id: familyId });
      designRows.set(familyId, doc);
      recorded.push({ kind: 'PROPOSE_DESIGN', id: familyId, reason: '' });
      return { ...doc };
    },
    async getDesign(familyId: string) {
      return { ...findDesign(familyId) };
    },
    async acceptFamily(familyId: string) {
      maybeFail();
      const family = findFamily(familyId);
      if (family.state !== 'PROPOSED' && family.state !== 'SINGLETON') {
        throw new ApiError(400, 'invalid_request', `cannot move a family from '${family.state}' to 'DRAFT'`);
      }
      const from = family.state;
      family.state = 'DRAFT';
      recordTransition(familyId, from, 'DRAFT', 'user:sme@artizent.example');
      recorded.push({ kind: 'ACCEPT_FAMILY', id: familyId, reason: '' });
      return { ...family };
    },
    async submitForReview(familyId: string) {
      maybeFail();
      const family = findFamily(familyId);
      if (family.state !== 'DRAFT') {
        throw new ApiError(400, 'invalid_request', `cannot move a family from '${family.state}' to 'IN_REVIEW'`);
      }
      const design = findDesign(familyId);
      const version = `sha256:${familyId}-${Date.now().toString(16)}`;
      design.version = version;
      family.state = 'IN_REVIEW';
      recordTransition(familyId, 'DRAFT', 'IN_REVIEW', 'user:sme@artizent.example');
      recorded.push({ kind: 'SUBMIT_FOR_REVIEW', id: familyId, reason: '' });
      return { ...family, semantic_model_id: design.semantic_model_id, version };
    },
    async familyTransitions(familyId: string) {
      return { family_id: familyId, transitions: (transitionRows.get(familyId) ?? []).map((t) => ({ ...t })) };
    },
    async editGrainStatement(familyId: string, grainStatement: string) {
      maybeFail();
      requireDraft(familyId);
      const design = findDesign(familyId);
      design.grain_statement = grainStatement;
      recorded.push({ kind: 'EDIT_GRAIN_STATEMENT', id: familyId, reason: '' });
      return { family_id: familyId, semantic_model_id: design.semantic_model_id, grain_statement: grainStatement };
    },
    async setTableMode(familyId: string, tableId: string, mode: string) {
      maybeFail();
      requireDraft(familyId);
      const design = findDesign(familyId);
      const table = design.tables.find((t) => t.id === tableId);
      if (!table) throw new ApiError(404, 'not_found', `no ModelTable '${tableId}' in family '${familyId}'`);
      table.mode = mode;
      recorded.push({ kind: 'SET_TABLE_MODE', id: tableId, reason: '' });
      return { family_id: familyId, table_id: tableId, mode };
    },
    async setRelationshipCardinality(familyId: string, fromTable: string, toTable: string, cardinality: string) {
      maybeFail();
      requireDraft(familyId);
      const design = findDesign(familyId);
      const relationship = design.relationships.find(
        (r) => r.from_table === fromTable && r.to_table === toTable,
      );
      if (!relationship) {
        throw new ApiError(404, 'not_found', `no relationship from '${fromTable}' to '${toTable}'`);
      }
      relationship.cardinality = cardinality;
      relationship.confidence = 'engineer_confirmed';
      recorded.push({ kind: 'SET_RELATIONSHIP_CARDINALITY', id: familyId, reason: '' });
      return { family_id: familyId, semantic_model_id: design.semantic_model_id, relationship: { ...relationship } };
    },
    async editDomain(familyId: string, domain: string) {
      maybeFail();
      requireDraft(familyId);
      const family = findFamily(familyId);
      family.domain = domain;
      recorded.push({ kind: 'EDIT_DOMAIN', id: familyId, reason: '' });
      return { family_id: familyId, domain };
    },
    async editOwner(familyId: string, owner: string) {
      maybeFail();
      requireDraft(familyId);
      const family = findFamily(familyId);
      family.owner = owner;
      recorded.push({ kind: 'EDIT_OWNER', id: familyId, reason: '' });
      return { family_id: familyId, owner };
    },
    async awaitingG2() {
      return {
        sla_working_days: awaitingG2.sla_working_days,
        reviews: reviewRows.map((review) => ({ ...review })),
        breached_count: reviewRows.filter((review) => review.breached).length,
      };
    },
    async sendG2Reminders() {
      maybeFail();
      const sent: { id: string; family_id: string; day: number; sent_at: string }[] = [];
      for (const review of reviewRows) {
        if (review.days_waiting === null) continue;
        const already = sentReminderDays.get(review.family_id) ?? new Set<number>();
        for (const day of [3, 5]) {
          if (review.days_waiting < day || already.has(day)) continue;
          already.add(day);
          sent.push({
            id: `rem_${review.family_id}_${day}`,
            family_id: review.family_id,
            day,
            sent_at: new Date().toISOString(),
          });
        }
        sentReminderDays.set(review.family_id, already);
      }
      recorded.push({ kind: 'SEND_G2_REMINDERS', id: '', reason: '' });
      return { sent, count: sent.length };
    },
    async familiesForReview() {
      const reviewStates = new Set(['DRAFT', 'IN_REVIEW', 'APPROVED']);
      const inReview = familyRows.filter((f) => f.state !== null && reviewStates.has(f.state));
      return { families: inReview.map((f) => ({ ...f })), count: inReview.length };
    },
    async proposal(familyId: string) {
      const family = findFamily(familyId);
      const design = designRows.get(familyId);
      const familyQuestions = questionRows.get(familyId) ?? [];
      return {
        family_id: familyId,
        name: family.name,
        domain: family.domain,
        state: family.state,
        grain_statement: design?.grain_statement ?? null,
        plain_summary: design
          ? `This model brings together ${design.tables.length} table(s) into ${design.candidate_measures.length} measure(s).`
          : 'No design has been generated yet.',
        reports: family.members,
        version: design?.version ?? null,
        open_questions: familyQuestions.map((q) => ({ ...q, owner: q.asked_by, status: q.state })),
        unanswered_count: familyQuestions.filter((q) => q.state === 'OPEN').length,
      };
    },
    async questions(familyId: string) {
      return { family_id: familyId, questions: (questionRows.get(familyId) ?? []).map((q) => ({ ...q })) };
    },
    async askQuestion(familyId: string, question: string, category: string) {
      maybeFail();
      const asked = g2Question({
        id: `q_${Math.random().toString(36).slice(2, 8)}`,
        family_id: familyId,
        category,
        question,
        state: 'OPEN',
        asked_by: 'user:owner@client.example',
      });
      const list = questionRows.get(familyId) ?? [];
      list.push(asked);
      questionRows.set(familyId, list);
      recorded.push({ kind: 'ASK_QUESTION', id: familyId, reason: '' });
      return { ...asked };
    },
    async replyToQuestion(questionId: string, message: string) {
      maybeFail();
      const question = findQuestion(questionId);
      question.thread = [
        ...question.thread,
        { from: 'user:sme@artizent.example', text: message, at: new Date().toISOString() },
      ];
      recorded.push({ kind: 'REPLY_TO_QUESTION', id: questionId, reason: '' });
      return { ...question };
    },
    async answerQuestion(questionId: string) {
      maybeFail();
      const question = findQuestion(questionId);
      if (question.state === 'ANSWERED') {
        throw new ApiError(400, 'invalid_request', `question '${questionId}' is already ANSWERED`);
      }
      question.state = 'ANSWERED';
      question.answered_by = 'user:owner@client.example';
      question.answered_at = new Date().toISOString();
      recorded.push({ kind: 'ANSWER_QUESTION', id: questionId, reason: '' });
      return { ...question };
    },
    async approveG2(familyId: string, _countersignedBy: string, rationale: string) {
      maybeFail();
      const family = findFamily(familyId);
      if (family.state !== 'IN_REVIEW') {
        throw new ApiError(400, 'invalid_request', `cannot move a family from '${family.state}' to 'APPROVED'`);
      }
      const openCount = (questionRows.get(familyId) ?? []).filter((q) => q.state === 'OPEN').length;
      if (openCount > 0) {
        throw new ApiError(400, 'invalid_request', `${openCount} open question(s) must be answered before this design can be approved`);
      }
      const design = findDesign(familyId);
      family.state = 'APPROVED';
      recordTransition(familyId, 'IN_REVIEW', 'APPROVED', 'user:owner@client.example');
      recorded.push({ kind: 'APPROVE_G2', id: familyId, reason: rationale });
      return {
        gate_decision_id: `gd_${Math.random().toString(36).slice(2, 8)}`,
        family_id: familyId,
        state: 'APPROVED',
        version: design.version ?? '',
      };
    },
    async requestChangesG2(familyId: string, comment: string) {
      maybeFail();
      const family = findFamily(familyId);
      if (family.state !== 'IN_REVIEW') {
        throw new ApiError(400, 'invalid_request', `cannot move a family from '${family.state}' to 'DRAFT'`);
      }
      family.state = 'DRAFT';
      const cycle = (cycleCounts.get(familyId) ?? 0) + 1;
      cycleCounts.set(familyId, cycle);
      recordTransition(familyId, 'IN_REVIEW', 'DRAFT', 'user:owner@client.example');
      recorded.push({ kind: 'REQUEST_CHANGES_G2', id: familyId, reason: comment });
      return {
        gate_decision_id: `gd_${Math.random().toString(36).slice(2, 8)}`,
        family_id: familyId,
        state: 'DRAFT',
        g2_cycle_count: cycle,
      };
    },
    async getBuild(familyId: string) {
      const build = buildRows.get(familyId);
      return { family_id: familyId, build: build ? { ...build } : null };
    },
    async triggerBuild(familyId: string) {
      maybeFail();
      const family = findFamily(familyId);
      if (family.state !== 'APPROVED' && family.state !== 'BUILT') {
        throw new ApiError(
          400, 'invalid_request',
          `cannot move a family from '${family.state}' to 'BUILT'; legal next state(s) from '${family.state}': (none — this is a terminal state)`,
        );
      }
      const record = buildRecord({
        id: `build_${Math.random().toString(36).slice(2, 8)}`,
        family_id: familyId,
        triggered_by: 'user:sme@artizent.example',
        started_at: new Date().toISOString(),
        finished_at: new Date().toISOString(),
      });
      buildRows.set(familyId, record);
      family.state = 'BUILT';
      recordTransition(familyId, 'APPROVED', 'BUILT', 'user:sme@artizent.example');
      recorded.push({ kind: 'TRIGGER_BUILD', id: familyId, reason: '' });
      return { ...record };
    },
    async conformanceRules() {
      return {
        ruleset: { ...rulesetState, rules: rulesetState.rules.map((r) => ({ ...r })) },
        rule_metadata: RULE_METADATA_FIXTURE,
      };
    },
    async saveConformanceRules(rules) {
      maybeFail();
      rulesetState = {
        version: rulesetState.version + 1,
        rules: rules.map((r) => ({ ...r })),
        updated_by: 'user:architect@artizent.example',
        updated_at: new Date().toISOString(),
      };
      recorded.push({ kind: 'SAVE_CONFORMANCE_RULES', id: '', reason: '' });
      return {
        ruleset: { ...rulesetState, rules: rulesetState.rules.map((r) => ({ ...r })) },
        rule_metadata: RULE_METADATA_FIXTURE,
      };
    },
    async toleranceCharter() {
      return {
        charter: { ...charterState, charter: { ...charterState.charter } },
        field_metadata: CHARTER_FIELD_METADATA_FIXTURE,
      };
    },
    async saveToleranceCharter(charter, identity, clientAnalyticsLeadAck, reason) {
      maybeFail();
      if (g1Approved && !clientAnalyticsLeadAck) {
        throw new ApiError(
          400, 'invalid_request',
          'the charter has already been approved at G1; changing it needs the client analytics lead’s own named sign-off',
        );
      }
      charterState = {
        version: charterState.version + 1,
        charter: { ...charter },
        updated_by: identity.principal,
        updated_at: new Date().toISOString(),
      };
      recorded.push({ kind: 'SAVE_TOLERANCE_CHARTER', id: '', reason: reason ?? '' });
      const isRevision = g1Approved;
      if (isRevision) g1Approved = true; // a revision re-records G1, so it stays approved
      return {
        charter: { ...charterState, charter: { ...charterState.charter } },
        is_revision: isRevision,
        reproved_workbook_ids: [],
      };
    },
    async approveG1(version, countersignedBy, rationale, identity) {
      maybeFail();
      g1Approved = true;
      recorded.push({ kind: 'APPROVE_G1', id: identity.principal, reason: rationale });
      void countersignedBy;
      return { gate_decision_id: 'gd_1', version, decision: 'APPROVED' };
    },
    async simulateToleranceCharter(workbookId): Promise<SimulateResult> {
      recorded.push({ kind: 'SIMULATE_CHARTER', id: workbookId, reason: '' });
      return { workbook_id: workbookId, has_prior_run: false, message: 'no ParityRun exists yet for this workbook', verdicts: [] };
    },
    async classMix() {
      return {
        ...classMixState,
        counts: { ...classMixState.counts },
        percentages: { ...classMixState.percentages },
        targets: { ...classMixState.targets },
      };
    },
    async reclassify() {
      maybeFail();
      const result = reclassifyResult({
        total: classMixState.total,
        classifier_version: (classMixState.classifier_version ?? 0) + 1,
      });
      const total = result.total || 1;
      classMixState = {
        ...classMixState,
        unclassified: 0,
        counts: { ...result.class_mix },
        percentages: {
          C1: Math.round((result.class_mix.C1 / total) * 1000) / 10,
          C2: Math.round((result.class_mix.C2 / total) * 1000) / 10,
          C3: Math.round((result.class_mix.C3 / total) * 1000) / 10,
          C4: Math.round((result.class_mix.C4 / total) * 1000) / 10,
        },
        classifier_version: result.classifier_version,
      };
      recorded.push({ kind: 'RECLASSIFY', id: '', reason: '' });
      return result;
    },
    async ruleCatalog(): Promise<RuleCatalog> {
      return ruleCatalog();
    },
    async ruleCoverage(): Promise<RuleCoverage> {
      return { ...ruleCoverageState, by_family: { ...ruleCoverageState.by_family } };
    },
    async applyRules(): Promise<ApplyRulesResult> {
      maybeFail();
      const result = applyRulesResult({ total: ruleCoverageState.total });
      const nextMatched = ruleCoverageState.matched + result.matched;
      const nextByFamily = { ...ruleCoverageState.by_family };
      for (const [family, count] of Object.entries(result.by_family)) {
        nextByFamily[family] = (nextByFamily[family] ?? 0) + count;
      }
      ruleCoverageState = {
        ...ruleCoverageState,
        matched: nextMatched,
        percentage: Math.round((nextMatched / (ruleCoverageState.total || 1)) * 1000) / 10,
        by_family: nextByFamily,
      };
      recorded.push({ kind: 'APPLY_RULES', id: '', reason: '' });
      return result;
    },
    async patterns(): Promise<PatternsResponse> {
      const rows = patternRows.map((p) => ({ ...p, guards: [...p.guards], provenance: { ...p.provenance } }));
      return { patterns: rows, count: rows.length };
    },
    async patternPromotionStatus(patternId: string): Promise<PatternPromotionStatus> {
      const pattern = findPattern(patternId);
      const threshold = 5;
      const eligible = pattern.promotion_state === 'CANDIDATE'
        && pattern.distinct_passing_calcs >= threshold
        && pattern.failure_count === 0;
      return {
        pattern_id: patternId,
        promotion_state: pattern.promotion_state,
        distinct_passing_calcs: pattern.distinct_passing_calcs,
        has_failure: pattern.failure_count > 0,
        threshold,
        eligible,
        reason: eligible ? 'eligible' : 'not eligible in this fixture',
      };
    },
    async promotePattern(patternId: string): Promise<PatternRecord> {
      maybeFail();
      const pattern = findPattern(patternId);
      if (pattern.promotion_state !== 'CANDIDATE') {
        throw new ApiError(400, 'invalid_request', `Pattern '${patternId}' is not eligible for promotion: already ${pattern.promotion_state}`);
      }
      pattern.promotion_state = 'ACTIVE';
      pattern.provenance = { ...pattern.provenance, promoted_at: new Date().toISOString(), approved_by: 'user:p.eng@artizent.example' };
      recorded.push({ kind: 'PROMOTE_PATTERN', id: patternId, reason: '' });
      return { ...pattern, guards: [...pattern.guards], provenance: { ...pattern.provenance } };
    },
    async retirePattern(patternId: string, reason: string): Promise<PatternRecord> {
      maybeFail();
      const pattern = findPattern(patternId);
      const cleaned = reason.trim();
      if (cleaned.length < 8) {
        throw new ApiError(400, 'invalid_request', 'a retirement needs a reason of at least 8 characters');
      }
      if (pattern.promotion_state === 'RETIRED') {
        throw new ApiError(400, 'invalid_request', `Pattern '${patternId}' is already RETIRED`);
      }
      pattern.promotion_state = 'RETIRED';
      pattern.provenance = {
        ...pattern.provenance,
        retired_at: new Date().toISOString(),
        retirement_reason: cleaned,
        retired_by: 'user:p.eng@artizent.example',
      };
      recorded.push({ kind: 'RETIRE_PATTERN', id: patternId, reason: cleaned });
      return { ...pattern, guards: [...pattern.guards], provenance: { ...pattern.provenance } };
    },
    async editPatternGuards(patternId: string, guards: string[], reason: string): Promise<PatternRecord> {
      maybeFail();
      const old = findPattern(patternId);
      const cleaned = reason.trim();
      if (cleaned.length < 8) {
        throw new ApiError(400, 'invalid_request', "editing a pattern's guards needs a reason of at least 8 characters");
      }
      if (old.promotion_state === 'RETIRED') {
        throw new ApiError(400, 'invalid_request', `Pattern '${patternId}' is RETIRED; edit its replacement instead`);
      }
      patternVersionCounter += 1;
      const created: PatternRecord = {
        ...old,
        id: `pat_v${patternVersionCounter}`,
        guards: [...guards],
        applications: 0,
        pass_total: 0,
        distinct_passing_calcs: 0,
        failure_count: 0,
        version: old.version + 1,
        supersedes_id: patternId,
        provenance: {
          ...old.provenance,
          edited_from: patternId,
          edit_reason: cleaned,
          edited_by: 'user:p.eng@artizent.example',
          edited_at: new Date().toISOString(),
        },
      };
      const index = patternRows.findIndex((p) => p.id === patternId);
      patternRows.splice(index, 1); // the superseded version no longer lists as live
      patternRows.push(created);
      recorded.push({ kind: 'EDIT_PATTERN_GUARDS', id: patternId, reason: cleaned });
      return { ...created, guards: [...created.guards], provenance: { ...created.provenance } };
    },
    async getVersions(familyId: string): Promise<VersionsResponse> {
      const rows = versionRows.get(familyId) ?? [];
      return { family_id: familyId, versions: [...rows].reverse().map((v) => ({ ...v })) };
    },
    async requestNewVersion(familyId: string, reason: string): Promise<RequestNewVersionResult> {
      maybeFail();
      const family = findFamily(familyId);
      if (family.state !== 'PUBLISHED') {
        throw new ApiError(400, 'invalid_request', `cannot move a family from '${family.state}' to 'DRAFT'`);
      }
      const cleaned = reason.trim();
      if (cleaned.length < 10) {
        throw new ApiError(400, 'invalid_request', 'a change request needs a reason of at least 10 characters');
      }
      const rows = versionRows.get(familyId) ?? [];
      const design = findDesign(familyId);
      const current = rows[rows.length - 1] ?? modelVersion({
        semantic_model_id: design.semantic_model_id,
        version_number: design.version_number,
        state: 'PUBLISHED',
      });
      if (!rows.length) rows.push(current);
      const nextVersionNumber = current.version_number + 1;
      const created = modelVersion({
        semantic_model_id: `sem_${familyId}_v${nextVersionNumber}`,
        version_number: nextVersionNumber,
        state: 'DRAFT',
        version: null,
        design_generated_at: new Date().toISOString(),
        published_at: null,
        deprecated_at: null,
      });
      rows.push(created);
      versionRows.set(familyId, rows);
      designRows.set(familyId, {
        ...design,
        semantic_model_id: created.semantic_model_id,
        version_number: created.version_number,
        state: 'DRAFT',
        version: null,
      });
      family.state = 'DRAFT';
      recordTransition(familyId, 'PUBLISHED', 'DRAFT', 'user:sme@artizent.example');
      recorded.push({ kind: 'REQUEST_NEW_VERSION', id: familyId, reason: cleaned });
      return {
        family_id: familyId,
        semantic_model_id: created.semantic_model_id,
        version_number: created.version_number,
        previous_semantic_model_id: current.semantic_model_id,
        previous_version_number: current.version_number,
        reason: cleaned,
      };
    },
    async promote(familyId: string): Promise<PromoteResult> {
      maybeFail();
      const family = findFamily(familyId);
      if (family.state !== 'BUILT') {
        throw new ApiError(400, 'invalid_request', `cannot move a family from '${family.state}' to 'PUBLISHED'`);
      }
      const rows = versionRows.get(familyId) ?? [];
      const design = findDesign(familyId);
      let current = [...rows].reverse().find((v) => v.state === 'BUILT') ?? rows[rows.length - 1];
      if (!current) {
        current = modelVersion({
          semantic_model_id: design.semantic_model_id,
          version_number: design.version_number,
          state: 'BUILT',
        });
        rows.push(current);
      }
      const previous = rows.find((v) => v.state === 'PUBLISHED' && v.semantic_model_id !== current!.semantic_model_id);
      const now = new Date().toISOString();
      current.state = 'PUBLISHED';
      current.published_at = now;
      if (previous) {
        previous.state = 'DEPRECATED';
        previous.deprecated_at = now;
      }
      versionRows.set(familyId, rows);
      designRows.set(familyId, { ...design, state: 'PUBLISHED', published_at: now });
      family.state = 'PUBLISHED';
      recordTransition(familyId, 'BUILT', 'PUBLISHED', 'user:sme@artizent.example');
      recorded.push({ kind: 'PROMOTE', id: familyId, reason: '' });
      return {
        family_id: familyId,
        semantic_model_id: current.semantic_model_id,
        version_number: current.version_number,
        published_at: now,
        deprecated_semantic_model_id: previous?.semantic_model_id ?? null,
        deprecated_version_number: previous?.version_number ?? null,
        published_workspace: 'prod',
        deployment_id: `dep_${Math.random().toString(36).slice(2, 8)}`,
      };
    },
    async parityDashboard(workbookId: string, _identity: Identity): Promise<ParityDashboardResponse> {
      if (!parityDashboardState) {
        throw new ApiError(404, 'not_found', `no ParityRun exists yet for workbook '${workbookId}'`);
      }
      return {
        ...parityDashboardState,
        sheets: parityDashboardState.sheets.map((sheet) => ({ ...sheet, failing_cells: [...sheet.failing_cells] })),
        trend: {
          runs: parityDashboardState.trend.runs.map((run) => ({ ...run })),
          mender_passes: { ...parityDashboardState.trend.mender_passes },
        },
      };
    },
    async parityRun(workbookId: string, _identity: Identity): Promise<ParityRunResponse> {
      if (!parityRunState) {
        throw new ApiError(404, 'not_found', `no ParityRun exists yet for workbook '${workbookId}'`);
      }
      return {
        ...parityRunState,
        verdicts: parityRunState.verdicts.map((verdict) => ({ ...verdict, failing_cells: [...verdict.failing_cells] })),
      };
    },
    async runParity(workbookId: string, _identity: Identity): Promise<RunParityResult> {
      maybeFail();
      recorded.push({ kind: 'RUN_PARITY', id: workbookId, reason: '' });
      return runParityResult({ workbook_id: workbookId });
    },
    async runVisualParity(workbookId: string, _identity: Identity): Promise<RunVisualParityResult> {
      maybeFail();
      recorded.push({ kind: 'RUN_VISUAL_PARITY', id: workbookId, reason: '' });
      return runVisualParityResult({ workbook_id: workbookId });
    },
    async visualCaptures(_workbookId: string, visualId: string, _identity: Identity): Promise<VisualCapturePair> {
      return visualCapturePair({ visual_id: visualId });
    },
    async regressionMonitor(_identity: Identity): Promise<RegressionMonitorResponse> {
      return { workbooks: regressionMonitorRows.map((row) => ({ ...row })), count: regressionMonitorRows.length };
    },
    async scheduleRegression(
      workbookId: string,
      workspace: string,
      identity: Identity,
      cadence?: { every_minutes: number } | { daily_at: string },
    ): Promise<RegressionScheduleRecord> {
      maybeFail();
      if (!identity.roles.includes('programme_manager')) {
        throw new ApiError(403, 'forbidden', 'scheduling regression is the Programme Manager\'s action');
      }
      const row = regressionMonitorRows.find((candidate) => candidate.workbook_id === workbookId);
      if (!row) {
        throw new ApiError(404, 'not_found', `no released workbook '${workbookId}'`);
      }
      if (row.schedule) {
        throw new ApiError(
          400, 'invalid_request', `a regression schedule already exists for workbook '${workbookId}'`,
        );
      }
      regressionScheduleSeq += 1;
      const created = regressionScheduleRecord({
        id: `rs_${regressionScheduleSeq}`,
        workbook_id: workbookId,
        workspace,
        cadence: cadence ?? { every_minutes: 10080 },
        cadence_description: cadence && 'daily_at' in cadence ? `daily at ${cadence.daily_at} UTC` : 'every 10080 minutes',
        last_run: { id: null, at: null, result: null, error: null },
        created_by: identity.principal,
      });
      row.schedule = created;
      recorded.push({ kind: 'SCHEDULE_REGRESSION', id: workbookId, reason: '' });
      return created;
    },
    async exportRegressionSuite(workbookId: string, identity: Identity): Promise<RegressionExportRecord> {
      maybeFail();
      const artizent = [
        'programme_manager', 'migration_architect', 'semantic_model_engineer',
        'migration_engineer', 'parity_engineer', 'platform_engineer',
      ];
      if (!identity.roles.some((role) => artizent.includes(role))) {
        throw new ApiError(403, 'forbidden', 'exporting a handover suite is an Artizent action');
      }
      recorded.push({ kind: 'EXPORT_REGRESSION_SUITE', id: workbookId, reason: '' });
      return { id: `af_export_${workbookId}`, kind: 'regression_export', mu_ref: workbookId, size_bytes: 4096 };
    },
    async exceptionQueue(filters, _identity) {
      const entries = exceptionCaseRows
        .filter((c) => c.state === 'OPEN' || c.state === 'BLOCKED')
        .filter((c) => !filters.train || c.train_id === filters.train)
        .filter((c) => !filters.failureClass || c.class === filters.failureClass)
        .filter((c) => !filters.site || c.site === filters.site)
        .filter((c) => !filters.assignee || c.assignee === filters.assignee)
        .map((c) => exceptionQueueEntry({
          id: c.id, mu_ref: c.mu_ref, class: c.class, passes_consumed: c.passes_consumed,
          assignee: c.assignee, state: c.state, train_id: c.train_id, train_sequence: c.train_sequence,
          site: c.site, created_at: c.created_at, age_seconds: 3600,
        }));
      return { entries, count: entries.length };
    },
    async exceptionCase(exceptionCaseId, _identity) {
      return { ...findExceptionCase(exceptionCaseId) };
    },
    async bulkAssignExceptions(exceptionCaseIds, assignee, identity) {
      maybeFail();
      if (!identity.roles.includes('migration_engineer')) {
        throw new ApiError(403, 'forbidden', 'bulk assign is the Migration Engineer\'s action');
      }
      const updated: string[] = [];
      for (const id of exceptionCaseIds) {
        const row = exceptionCaseRows.find((c) => c.id === id);
        if (!row) continue;
        row.assignee = assignee;
        updated.push(id);
      }
      recorded.push({ kind: 'BULK_ASSIGN_EXCEPTIONS', id: updated.join(','), reason: assignee });
      return { assignee, updated, count: updated.length };
    },
    async patchException(exceptionCaseId, dax, rationale, _workspace, _identity) {
      maybeFail();
      const row = findExceptionCase(exceptionCaseId);
      row.decision = 'PATCHED';
      row.artefact = { ...row.artefact, current_dax: dax };
      row.state = 'CLOSED';
      recorded.push({ kind: 'PATCH_EXCEPTION', id: exceptionCaseId, reason: rationale });
      return {
        exception_case_id: exceptionCaseId, gate_decision_id: 'gd_patch_1', measure_id: 'msr_new',
        outcome: 'closed', cases_reproved: row.evidence.failing_cells.map((c) => c.case_ref), cases_still_failing: [],
      };
    },
    async redesignException(exceptionCaseId, route, rationale, _identity, desktopCommitHash) {
      maybeFail();
      const row = findExceptionCase(exceptionCaseId);
      row.decision = route === 'desktop' ? 'REDESIGN_DESKTOP' : 'REDESIGN_FOUNDRY';
      row.state = route === 'desktop' ? 'CLOSED' : row.state;
      recorded.push({ kind: 'REDESIGN_EXCEPTION', id: exceptionCaseId, reason: rationale });
      return {
        exception_case_id: exceptionCaseId, gate_decision_id: 'gd_redesign_1', route,
        detail: route === 'desktop' ? { desktop_commit_hash: desktopCommitHash ?? null } : { route_result: 'ROUTED_TO_FOUNDRY' },
      };
    },
    async decideModelDefect(exceptionCaseId, rationale, _identity) {
      maybeFail();
      const row = findExceptionCase(exceptionCaseId);
      row.decision = 'MODEL_DEFECT_FOUNDRY';
      row.state = 'BLOCKED';
      recorded.push({ kind: 'DECIDE_MODEL_DEFECT', id: exceptionCaseId, reason: rationale });
      return { exception_case_id: exceptionCaseId, gate_decision_id: 'gd_model_defect_1', route_result: { result: 'ROUTED_TO_FOUNDRY' } };
    },
    async decideSourceDefect(exceptionCaseId, rationale, resolution, _identity, ownerSignOff) {
      maybeFail();
      const row = findExceptionCase(exceptionCaseId);
      row.decision = `SOURCE_DEFECT_${resolution}`;
      row.state = 'CLOSED';
      recorded.push({ kind: 'DECIDE_SOURCE_DEFECT', id: exceptionCaseId, reason: rationale });
      return {
        exception_case_id: exceptionCaseId, gate_decision_id: 'gd_source_defect_1', resolution,
        owner_sign_off: ownerSignOff ?? null, notified: true,
      };
    },
    async exceptionAgeing(_identity) {
      return exceptionAgeingResponse();
    },
    async g3Card(workbookId, _identity, format) {
      const current = g3CardState.get(workbookId) ?? g3Card({ workbook_id: workbookId });
      if (format === 'adaptive_card') {
        return { type: 'AdaptiveCard', version: '1.5', body: [], actions: [] } as unknown as G3Card;
      }
      return current;
    },
    async approveG3(workbookId, rationale, countersignedBy, identity) {
      maybeFail();
      recorded.push({ kind: 'APPROVE_G3', id: workbookId, reason: rationale });
      const current = g3CardState.get(workbookId) ?? g3Card({ workbook_id: workbookId });
      g3CardState.set(workbookId, {
        ...current,
        latest_decision: {
          decision: 'APPROVED', approver: identity.principal, countersigner: countersignedBy,
          timestamp: '2027-06-01T09:00:00.000Z', rationale,
        },
      });
      return {
        workbook_id: workbookId, gate_decision_id: 'gd_g3_1', decision: 'APPROVED',
        invoiced: false, tier: null, unit_price: null,
      };
    },
    async requestChangesG3(workbookId, rationale, identity) {
      maybeFail();
      recorded.push({ kind: 'REQUEST_CHANGES_G3', id: workbookId, reason: rationale });
      const current = g3CardState.get(workbookId) ?? g3Card({ workbook_id: workbookId });
      g3CardState.set(workbookId, {
        ...current,
        latest_decision: {
          decision: 'CHANGES_REQUESTED', approver: identity.principal, countersigner: null,
          timestamp: '2027-06-01T09:00:00.000Z', rationale,
        },
      });
      return {
        workbook_id: workbookId, gate_decision_id: 'gd_g3_2', decision: 'CHANGES_REQUESTED',
        invoiced: false, tier: null, unit_price: null,
      };
    },
    async askG3Question(workbookId, question, identity) {
      maybeFail();
      recorded.push({ kind: 'ASK_G3_QUESTION', id: workbookId, reason: question });
      const created = g3Question({ id: `g3q_${g3QuestionRows.length + 1}`, workbook_id: workbookId, question, asked_by: identity.principal });
      g3QuestionRows.push(created);
      return created;
    },
    async g3Questions(workbookId, _identity) {
      const rows = g3QuestionRows.filter((q) => q.workbook_id === workbookId);
      return { questions: rows, count: rows.length };
    },
    async acceptanceSummary(_identity) {
      return initialAcceptanceSummary;
    },
    async releaseBoard(_identity) {
      return releaseBoardState;
    },
    async promoteToTest(workbookId, identity) {
      maybeFail();
      recorded.push({ kind: 'PROMOTE_TO_TEST', id: workbookId, reason: '' });
      const record = promotionRecord({
        workbook_id: workbookId, to_stage: 'test', workspace: 'test',
        approved_by: identity.principal, approver_role: 'platform_engineer', triggered_by: identity.principal,
      });
      releaseBoardState = {
        ...releaseBoardState,
        trains: releaseBoardState.trains.map((t) => ({
          ...t,
          mus: t.mus.map((mu) =>
            mu.workbook_id === workbookId
              ? { ...mu, stage: 'TEST', next_stage: 'prod', blockers: [], evidence: [...mu.evidence, record] }
              : mu,
          ),
        })),
      };
      return record;
    },
    async promoteToProd(workbookId, rationale, identity) {
      maybeFail();
      recorded.push({ kind: 'PROMOTE_TO_PROD', id: workbookId, reason: rationale });
      const record = promotionRecord({
        workbook_id: workbookId, to_stage: 'prod', workspace: 'prod', rationale,
        approved_by: identity.principal, approver_role: 'programme_manager', triggered_by: identity.principal,
      });
      releaseBoardState = {
        ...releaseBoardState,
        trains: releaseBoardState.trains.map((t) => ({
          ...t,
          mus: t.mus.map((mu) =>
            mu.workbook_id === workbookId
              ? { ...mu, stage: 'PROD', next_stage: null, blockers: [], evidence: [...mu.evidence, record] }
              : mu,
          ),
        })),
      };
      return record;
    },
  };
}
