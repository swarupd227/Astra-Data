/**
 * The API the console talks to.
 *
 * One module, so every call carries the identity headers and every failure is the same
 * shape. Spec §12.4.1: "the console has no privileged path" — this is the same public API
 * an integration would use, which is why there is no bespoke console endpoint here.
 *
 * Identity is a header until E11 brings Entra ID (`X-Astra-Principal` / `X-Astra-Roles`,
 * as the service reads them). That is a stated stub, not a security model, and the console
 * says so on screen rather than pretending it has signed anybody in.
 */

export interface Identity {
  principal: string;
  roles: string[];
  /** What a client data owner is asserting authority over (S4.2.1) — sent as
   * `X-Astra-Domain-Scope` on every call. Absent or empty means "no domain asserted", the
   * same "real until E11 maps it for real" posture `roles` already has. */
  domainScope?: string[];
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly body?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
  }

  /** Whether the caller lacks the role, as opposed to the request being wrong. */
  get forbidden(): boolean {
    return this.status === 403;
  }
}

export interface TreeNode {
  id: string;
  name: string;
  kind: 'site' | 'project';
  workbooks: number;
  held: number;
  unparsed: number;
  views_90d: number;
  children: TreeNode[];
}

export interface Workbook {
  id: string;
  luid: string;
  name: string;
  site: string | null;
  site_id: string | null;
  project: string | null;
  project_id: string | null;
  parse_quality: number | null;
  parse_quality_band: string;
  views_90d: number | null;
  usage_band: string;
  distinct_viewers_90d: number | null;
  owner: string | null;
  owner_id: string | null;
  calculated_fields: number;
  held: boolean;
  tier: string | null;
  withdrawn: boolean;
  withdrawn_reason: string | null;
}

export interface FacetOption {
  key: string;
  label: string;
  count: number;
}

export interface PendingFacet {
  facet: string;
  reason: string;
}

export interface EstateResponse {
  tree: TreeNode[];
  workbooks: Workbook[];
  total: number;
  offset: number;
  limit: number;
  estate_total: number;
  facets: {
    parse_quality_band: FacetOption[];
    usage_band: FacetOption[];
    owner: FacetOption[];
    project: FacetOption[];
    site: FacetOption[];
    tier: FacetOption[];
    pending: PendingFacet[];
    withdrawn: number;
  };
  tiers: string[];
  pending_columns: { column: string; reason: string }[];
  timing: { total_ms: number; estate_read_ms: number };
}

export interface LineageNode {
  id: string;
  type: string;
  name: string | null;
  depth: number;
}

export interface WorkbookDetail {
  workbook: { id: string; type: string; properties: Record<string, unknown> };
  scope: {
    decisions: {
      id: string;
      kind: string;
      from: string | null;
      to: string | null;
      reason: string;
      decided_by: string;
      decided_at: string;
    }[];
    current: {
      tier: string | null;
      tier_reason: string | null;
      withdrawn: boolean;
      withdrawn_reason: string | null;
    };
  };
  lineage: {
    depth: number;
    nodes: LineageNode[];
    edges: { type: string; from: string; to: string }[];
    truncated: boolean;
  };
  migration_unit: null | { id: string; state: string };
  migration_unit_reason: string;
}

export interface LineageGraphNode {
  id: string;
  type: string;
  name: string;
  site?: string | null;
  project?: string | null;
  parse_quality?: number | null;
  views_90d?: number | null;
}

export interface LineageStructuralEdge {
  source: string;
  target: string;
  type: string;
}

export interface SharedLineageLink {
  source: string;
  target: string;
  strength: number;
  jaccard_tables: number;
  jaccard_fields: number;
  shared_calc_shapes: number;
  origin: 'graph' | 'computed';
}

export interface ModelFamily {
  id: string;
  name: string;
  state: string | null;
  members: string[];
  size: number;
}

export interface ColourMode {
  key: string;
  label: string;
  available: boolean;
  note?: string;
  reason?: string;
}

export interface LineageResponse {
  scope: {
    site: string | null;
    project: string | null;
    family: string | null;
    workbooks: string[] | null;
    min_strength: number;
    limit: number;
  };
  nodes: LineageGraphNode[];
  edges: LineageStructuralEdge[];
  shared_lineage: SharedLineageLink[];
  families: ModelFamily[];
  shared_lineage_origin: 'graph' | 'computed';
  colour_modes: ColourMode[];
  node_types: string[];
  truncated: boolean;
  auto_scoped_to: string | null;
  workbook_count: number;
  weights: { tables: number; fields: number; calc_shapes: number; spec_ref: string };
  read_ms: number;
}

export interface HeldWorkbook {
  site: string;
  workbook_luid: string;
  workbook_name: string;
  project: string;
  parse_quality: number | null;
  recognised: number;
  ignorable: number;
  total: number;
  unrecognised_constructs: number;
  grammar_version: string | null;
  harvested_at: string | null;
}

export interface ConstructIssue {
  id: string;
  state: string;
  opened_by: string;
  opened_at: string | null;
  external: { ref: string | null; url: string | null };
}

export interface ConstructGroup {
  construct: string;
  occurrences: number;
  /** How many workbooks contain it at all, held or not. */
  workbooks: number;
  /**
   * Held workbooks for which this is the only remaining unrecognised construct — so
   * resolving it alone releases them. The service calls the same number `workbooks_held`
   * internally and serialises it under this name; there is one figure, not two.
   */
  workbooks_released_if_resolved: number;
  sites: string[];
  example_location: Record<string, string | null>;
  unrecognised: boolean;
  issue: ConstructIssue | null;
}

export interface QueueResponse {
  threshold: number;
  /** The service calls this `held`; it is the list of workbooks under the threshold. */
  held: HeldWorkbook[];
  count: number;
}

export interface ConstructsResponse {
  threshold: number;
  constructs: ConstructGroup[];
  count: number;
}

export interface GrammarIssue {
  id: string;
  construct: string;
  summary: string;
  detail: string;
  state: string;
  active: boolean;
  locations: Record<string, string | null>[];
  occurrences_when_raised: number;
  workbooks_held_when_raised: number;
  external: { ref: string | null; url: string | null };
  opened_by: string;
  opened_at: string | null;
}

export interface ProgrammeRecord {
  id: string;
  name: string;
  started_at: string;
  closed_at: string | null;
  open: boolean;
  retain_until: string | null;
  family_count: number | null;
  family_count_confirmed_at: string | null;
  family_count_confirmed_by: string | null;
  /** §14.3 / Appendix A's "~150 shared governed models" planning assumption. */
  planned_family_count: number;
  /** `family_count - planned_family_count`, or `null` until a count is confirmed. */
  family_count_delta: number | null;
}

export interface ProgrammesResponse {
  programmes: ProgrammeRecord[];
}

// -------------------------------------------------------------- the Modeller (S4.1.1/S4.1.2)

export interface FamilyRecord {
  id: string;
  name: string;
  state: string | null;
  domain: string | null;
  owner: string | null;
  grain: string[];
  conformed_dims: string[];
  reason: string | null;
  members: string[];
  size: number;
  evidence: {
    shared_tables: string[];
    shared_fields: string[];
    shared_calc_shapes: number;
  };
  overridden: boolean;
  override_action: string | null;
  override_reason: string | null;
  conformance_ruleset_version: number | null;
}

export interface FamiliesResponse {
  families: FamilyRecord[];
  count: number;
}

export interface DesignTable {
  id: string;
  name: string;
  schema: string | null;
  source_table_refs: string[];
  mode: string;
  mode_reason: string;
  row_estimate: number | null;
  custom_sql: boolean;
  family_ref: string;
}

export interface DesignRelationship {
  from_table: string;
  to_table: string;
  cardinality: string | null;
  confidence: string;
  reason: string;
  join_clause: string | null;
}

export interface DesignMeasure {
  name: string;
  source_calc_refs: string[];
  dedup_decision: string;
}

export interface DesignRlsRole {
  name: string;
  expression: string;
  source_workbook_ids: string[];
}

export interface DesignConformedDimension {
  dimension: string;
  shared_with_family_ids: string[];
}

export interface DesignOpenQuestion {
  category: string;
  question: string;
  evidence: Record<string, unknown>;
}

export interface RefreshPolicy {
  mode: string;
  schedule: string | null;
  extracted_source_count: number;
  live_source_count: number;
  distinct_schedules: string[];
}

export interface DesignDocument {
  family_id: string;
  semantic_model_id: string;
  grain_statement: string | null;
  design_generated_at: string | null;
  design_provenance_ref: string | null;
  version: string | null;
  /** Story S4.3.3: which version of the model this is — absent on a design generated
   * before that story means 1, the same "only version there ever was" every family had. */
  version_number: number;
  state: string | null;
  published_at: string | null;
  deprecated_at: string | null;
  rls_roles: string[];
  tables: DesignTable[];
  relationships: DesignRelationship[];
  candidate_measures: DesignMeasure[];
  conformed_dimensions: DesignConformedDimension[];
  refresh_policy: RefreshPolicy;
  open_questions: DesignOpenQuestion[];
  rls_role_detail: DesignRlsRole[];
  member_count?: number;
  elapsed_seconds?: number;
}

export interface FamilyTransition {
  from_state: string | null;
  to_state: string;
  at: string;
  by: string;
}

export interface FamilyTransitionsResponse {
  family_id: string;
  transitions: FamilyTransition[];
}

// ------------------------------------------------------------------- versioning (S4.3.3)

export interface ModelVersion {
  semantic_model_id: string;
  version_number: number;
  state: string | null;
  version: string | null;
  design_generated_at: string | null;
  published_at: string | null;
  deprecated_at: string | null;
}

export interface VersionsResponse {
  family_id: string;
  versions: ModelVersion[];
}

export interface RequestNewVersionResult {
  family_id: string;
  semantic_model_id: string;
  version_number: number;
  previous_semantic_model_id: string;
  previous_version_number: number;
  reason: string;
}

export interface PromoteResult {
  family_id: string;
  semantic_model_id: string;
  version_number: number;
  published_at: string;
  deprecated_semantic_model_id: string | null;
  deprecated_version_number: number | null;
  published_workspace: string;
  deployment_id: string;
}

// --------------------------------------------------------------------- G2 review (S4.2.1)

export interface ThreadMessage {
  from: string;
  text: string;
  at: string;
}

export interface G2Question {
  id: string;
  family_id: string;
  category: string;
  question: string;
  state: 'OPEN' | 'ANSWERED';
  evidence: Record<string, unknown>;
  thread: ThreadMessage[];
  asked_by: string;
  asked_at: string | null;
  answered_by: string | null;
  answered_at: string | null;
  /** Present only on the client proposal view's own copy — same as asked_by/state. */
  owner?: string;
  status?: string;
}

export interface QuestionsResponse {
  family_id: string;
  questions: G2Question[];
}

export interface ModelProposal {
  family_id: string;
  name: string | null;
  domain: string | null;
  state: string | null;
  grain_statement: string | null;
  plain_summary: string;
  reports: string[];
  version: string | null;
  open_questions: G2Question[];
  unanswered_count: number;
}

export interface ApproveResult {
  gate_decision_id: string;
  family_id: string;
  state: string;
  version: string;
}

export interface RequestChangesResult {
  gate_decision_id: string;
  family_id: string;
  state: string;
  g2_cycle_count: number;
}

// --------------------------------------------------------------------- build (S4.3.1)

export interface BuildStep {
  name: string;
  ok: boolean;
  detail: string;
}

export interface BuildRecord {
  id: string;
  family_id: string;
  version: string;
  gate_decision_id: string | null;
  state: 'SUCCEEDED' | 'FAILED';
  steps: BuildStep[];
  git_commit_sha: string | null;
  git_ref: string | null;
  workspace: string | null;
  triggered_by: string;
  started_at: string;
  finished_at: string;
}

export interface BuildResponse {
  family_id: string;
  build: BuildRecord | null;
}

// --------------------------------------------------------------- conformance rules (S4.3.2)

export interface RuleConfig {
  rule_id: string;
  enabled: boolean;
  params: Record<string, unknown>;
}

export interface ConformanceRuleset {
  version: number;
  rules: RuleConfig[];
  updated_by: string;
  updated_at: string | null;
}

export interface RuleMetadataEntry {
  label: string;
  description: string;
  params: Record<string, string>;
}

export interface ConformanceRulesResponse {
  ruleset: ConformanceRuleset;
  rule_metadata: Record<string, RuleMetadataEntry>;
}

// -------------------------------------------------------------- tolerance charter (S7.1.1)

export interface NumericRule {
  abs_epsilon: number;
  rel_epsilon: number;
  rounding: string;
  currency_scale: number;
}

export interface NullRule {
  source_null_vs_target_zero: 'PASS' | 'FAIL';
  source_null_vs_target_blank: 'PASS' | 'FAIL';
  empty_string_is_null: boolean;
}

export interface DateRule {
  grain_alignment: string;
  timezone: string;
  fiscal_year_start: number;
}

export interface StringRule {
  trim: boolean;
  case_sensitive: boolean;
  collation: string;
}

export interface OrderingRule {
  sort_sensitive: boolean;
  top_n_tie_break: string;
}

export interface RowRule {
  missing_key: 'PASS' | 'FAIL';
  extra_key: 'PASS' | 'FAIL';
  row_count_tolerance: number;
  max_failing_cells: number;
}

export interface SamplingRule {
  full_compare_max_rows: number;
  sample_rows: number;
  stratify_by: string;
}

export interface ParamRule {
  enumerate_max_values: number;
  enumerate_strategy: string;
}

export interface WaiverRule {
  allowed_classes: string[];
  requires: string[];
  justification_min_chars: number;
}

export interface ToleranceCharter {
  numeric: NumericRule;
  nulls: NullRule;
  dates: DateRule;
  strings: StringRule;
  ordering: OrderingRule;
  rows: RowRule;
  sampling: SamplingRule;
  params: ParamRule;
  waiver: WaiverRule;
}

export interface ToleranceCharterVersion {
  version: number;
  charter: ToleranceCharter;
  updated_by: string;
  updated_at: string | null;
}

export interface ToleranceCharterFieldMetadata {
  [block: string]: Record<string, string>;
}

export interface ToleranceCharterResponse {
  charter: ToleranceCharterVersion;
  field_metadata: ToleranceCharterFieldMetadata;
}

export interface SaveToleranceCharterResult {
  charter: ToleranceCharterVersion;
  is_revision: boolean;
  reproved_workbook_ids: string[];
}

export interface ApproveG1Result {
  gate_decision_id: string;
  version: number;
  decision: string;
}

export interface SimulatedCell {
  verdict_id: string;
  grain_key: unknown;
  measure: unknown;
  expected: unknown;
  candidate: unknown;
  result: 'PASS' | 'FAIL';
  reason: string;
}

export interface SimulateResult {
  workbook_id: string;
  has_prior_run: boolean;
  run_id?: string;
  message: string | null;
  verdicts: SimulatedCell[];
}

// -------------------------------------------------------------- classification (S5.1.1)

export interface ClassMix {
  total: number;
  unclassified: number;
  counts: { C1: number; C2: number; C3: number; C4: number };
  percentages: { C1: number; C2: number; C3: number; C4: number };
  targets: { C1: number; C2: number; C3: number; C4: number };
  /** Null when nothing has been classified yet, or when classified fields disagree on
   * which ruleset version produced their class — the console shows "mixed" rather than
   * picking one. */
  classifier_version: number | null;
}

export interface MovedClassification {
  calculated_field_id: string;
  name: string;
  from_class: string | null;
  to_class: string;
}

export interface ReclassifyResult {
  classifier_version: number;
  total: number;
  class_mix: { C1: number; C2: number; C3: number; C4: number };
  moved: MovedClassification[];
}

// ---------------------------------------------------- deterministic rules engine (S5.2.1)

export interface RuleCatalogEntry {
  id: string;
  version: number;
  class: string;
  family: string;
  description: string;
  guards: string[];
  golden_case_count: number;
}

export interface RuleCatalog {
  rules: RuleCatalogEntry[];
}

export interface RuleCoverage {
  total: number;
  matched: number;
  percentage: number;
  by_family: Record<string, number>;
  rules_version: number;
}

export interface AppliedRule {
  calculated_field_id: string;
  name: string;
  rule_id: string;
  family: string;
  measure_id: string;
}

export interface ApplyRulesResult {
  rules_version: number;
  total: number;
  matched: number;
  by_family: Record<string, number>;
  applied: AppliedRule[];
}

// -------------------------------------------------------------- Pattern Library (S5.5.1-3)

export interface PatternProvenance {
  origin?: string;
  first_seen?: string;
  promoted_at?: string;
  approved_by?: string;
  retired_at?: string;
  retirement_reason?: string;
  retired_by?: string;
  edited_from?: string;
  edit_reason?: string;
  edited_by?: string;
  edited_at?: string;
}

export type PromotionState = 'CANDIDATE' | 'ACTIVE' | 'RETIRED';

export interface PatternRecord {
  id: string;
  name: string;
  class: string;
  promotion_state: PromotionState;
  target_template: string;
  guards: string[];
  applications: number;
  pass_total: number;
  distinct_passing_calcs: number;
  failure_count: number;
  provenance: PatternProvenance;
  version: number;
  supersedes_id: string | null;
}

export interface PatternsResponse {
  patterns: PatternRecord[];
  count: number;
}

export interface PatternPromotionStatus {
  pattern_id: string;
  promotion_state: PromotionState;
  distinct_passing_calcs: number;
  has_failure: boolean;
  threshold: number;
  eligible: boolean;
  reason: string;
}

export interface TrainMember {
  id: string;
  name: string;
  sequence: number;
  /** The MU's §3.2 state — the Wave Board's kanban column for this card. */
  state: string;
}

export interface WipLimits {
  train: number | null;
  states: Record<string, number>;
}

export interface GateWindow {
  planned_date: string;
  note: string;
}

export interface Train {
  id: string;
  name: string;
  size: number;
  members: TrainMember[];
  planned_start: string | null;
  planned_end: string | null;
  actual_start: string | null;
  actual_end: string | null;
  gate_schedule: { G2: GateWindow; G3: GateWindow } | null;
  wip_limits: WipLimits | null;
  /** Set once a Programme Manager has edited this train on the Wave Board (S3.2.2) — a
   * re-propose (S3.2.1) leaves it exactly as it is. */
  overridden: boolean;
  override_action: string | null;
  override_reason: string | null;
}

export interface TrainsResponse {
  trains: Train[];
  count: number;
}

export interface WipStatus {
  train_limit: number | null;
  train_count: number;
  state_limit: number | null;
  state_count: number;
  exceeded: boolean;
}

export interface MoveMemberResult {
  workbook_id: string;
  from_train_id: string;
  to_train_id: string;
  state: string;
  sequence: number;
  wip: WipStatus | null;
}

export interface TrainEvent {
  sequence: number;
  event: {
    subject: string;
    type: string;
    time: string;
    [key: string]: unknown;
  };
}

export interface TrainEventsResponse {
  train_id: string;
  events: TrainEvent[];
  window: number;
}

export interface TrainProjection {
  train_id: string;
  train_name: string;
  planned_end: string | null;
  bottleneck_state: string | null;
  remaining_in_bottleneck: number;
  projected_end: string | null;
  projected_end_early: string | null;
  projected_end_late: string | null;
  days_late: number | null;
  flagged: boolean;
  reason: string;
}

export interface TrainProjectionsResponse {
  trailing_days: number;
  late_threshold_working_days: number;
  projections: TrainProjection[];
  flagged_count: number;
}

export interface AwaitingG2Review {
  family_id: string;
  name: string | null;
  domain: string | null;
  approver: string | null;
  entered_review_at: string | null;
  days_waiting: number | null;
  breached: boolean;
  open_questions: number;
}

export interface AwaitingG2Response {
  sla_working_days: number;
  reviews: AwaitingG2Review[];
  breached_count: number;
}

export interface G2ReminderRecord {
  id: string;
  family_id: string;
  day: number;
  sent_at: string;
}

export interface SendG2RemindersResponse {
  sent: G2ReminderRecord[];
  count: number;
}

export interface LineageQuery {
  site?: string | null;
  project?: string | null;
  family?: string | null;
  min_strength?: number;
  limit?: number;
}

export interface EstateQuery {
  site?: string | null;
  project?: string | null;
  owner?: string | null;
  tier?: string | null;
  parse_quality_band?: string | null;
  usage_band?: string | null;
  held_only?: boolean;
  unowned_only?: boolean;
  include_withdrawn?: boolean;
  search?: string | null;
  sort?: string;
  offset?: number;
  limit?: number;
}

function headers(identity: Identity): HeadersInit {
  const base: Record<string, string> = {
    'Content-Type': 'application/json',
    'X-Astra-Principal': identity.principal,
    'X-Astra-Roles': identity.roles.join(','),
  };
  if (identity.domainScope && identity.domainScope.length > 0) {
    base['X-Astra-Domain-Scope'] = identity.domainScope.join(',');
  }
  return base;
}

async function unwrap(response: Response): Promise<unknown> {
  const text = await response.text();
  const body: unknown = text ? JSON.parse(text) : null;
  if (response.ok) return body;

  // The service answers with {error, message} for its own refusals and {detail: […]} for
  // schema violations. Both are turned into one shape so callers never branch on which.
  const record = (body ?? {}) as Record<string, unknown>;
  const detail = record.detail;
  const message =
    typeof record.message === 'string'
      ? record.message
      : Array.isArray(detail)
        ? detail.map((d) => String((d as Record<string, unknown>).msg ?? d)).join('; ')
        : typeof detail === 'string'
          ? detail
          : response.statusText;
  throw new ApiError(response.status, String(record.error ?? 'error'), message, body);
}

export function estateQueryString(query: EstateQuery): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined || value === '' || value === false) continue;
    params.set(key, String(value));
  }
  const rendered = params.toString();
  return rendered ? `?${rendered}` : '';
}

// --------------------------------------------------------------------- Parity Dashboard

/** One failing cell (§10.3), enriched with the case it belongs to and that case's own
 * filter context (`FailingCell` itself carries no filter context — story S7.4.2). */
export interface FailingCellRow {
  case_id: string;
  grain_key: unknown[];
  measure: string;
  kind: string;
  expected: unknown;
  candidate: unknown;
  delta: number | null;
  reason: string;
  filter_ctx: Record<string, unknown>;
}

export interface StructuralScoreBreakdown {
  score: number;
  mark_type: number;
  encodings: number;
  axes: number;
  sort: number;
  reference_lines: number;
}

export interface SheetParityStats {
  sheet_ref: string;
  sheet_name: string;
  /** The sheet's own composed `Visual`, when one exists — needed to fetch its captures. */
  visual_id: string | null;
  cases_run: number;
  pass: number;
  fail: number;
  inconclusive: number;
  waived_count: number;
  /** `null` when no case on this sheet has ever had a first verdict — never 0/0. */
  first_pass_rate: number | null;
  failing_cells: FailingCellRow[];
  /** §10.5 (story S7.6.1), advisory only — never gates. `null` until this sheet's own
   * visual has been scored at least once. */
  structural_score: number | null;
  structural_score_breakdown: StructuralScoreBreakdown | null;
  /** `null` either before scoring, or when the source adapter does not claim the
   * screenshot capability — an honest absence, not a zero. */
  image_score: number | null;
  visual_score_computed_at: string | null;
  source_screenshot_ref: string | null;
  target_render_ref: string | null;
}

export interface ParityRunTrendEntry {
  run_id: string;
  started: string | null;
  finished: string | null;
  charter_version: string;
  cases: number;
  pass: number;
  fail: number;
  inconclusive: number;
  pass_rate: number | null;
}

export interface MenderPassesInfo {
  /** `false` until at least one ExceptionCase for this workbook has closed through the
   *  Mender (story S8.2.1); see `detail`. `true` once `closed_count`/`mean_passes_to_pass`
   *  are real. */
  available: boolean;
  detail?: string;
  closed_count?: number;
  mean_passes_to_pass?: number;
}

export interface ParityDashboardResponse {
  workbook_id: string;
  charter_version: string;
  passes_the_charter: boolean;
  latest_run_id: string;
  sheets: SheetParityStats[];
  trend: {
    runs: ParityRunTrendEntry[];
    mender_passes: MenderPassesInfo;
  };
}

// ---------------------------------------------------------------------------- Regression

/** §10.6 (story S7.7.1) -- one workbook's own recurring regression check. */
export interface RegressionScheduleRecord {
  id: string;
  workbook_id: string;
  workspace: string;
  cadence: { every_minutes: number } | { daily_at: string };
  cadence_description: string;
  enabled: boolean;
  paused_reason: string | null;
  /** `null` when disabled -- a paused schedule has no next firing to show. */
  next_run_at: string | null;
  last_run: {
    id: string | null;
    at: string | null;
    /** `"PASS" | "FAIL" | "INCONCLUSIVE"`, or `null` before the first check ever runs. */
    result: string | null;
    error: string | null;
  };
  consecutive_failures: number;
  created_by: string;
  created_at: string | null;
}

export interface RegressionDriftAlert {
  /** True when a real `SOURCE_DRIFT` event names this workbook and this schedule has
   * not yet checked it (§10.6's own "on SOURCE_DRIFT" trigger). */
  unaddressed: boolean;
  last_drift_at: string | null;
}

export interface RegressionMonitorRow {
  workbook_id: string;
  workbook_name: string;
  /** `null` for a released workbook nobody has enrolled in scheduled regression yet. */
  schedule: RegressionScheduleRecord | null;
  last_result: string | null;
  drift_alert: RegressionDriftAlert;
}

export interface RegressionMonitorResponse {
  workbooks: RegressionMonitorRow[];
  count: number;
}

export interface RegressionExportRecord {
  id: string;
  kind: string;
  mu_ref: string;
  size_bytes: number;
}

export interface VerdictRow {
  id: string;
  case_ref: string;
  result: 'PASS' | 'FAIL' | 'INCONCLUSIVE';
  failing_cells: Array<{
    grain_key: unknown[];
    measure: string;
    kind: string;
    expected: unknown;
    candidate: unknown;
    delta: number | null;
    reason: string;
  }>;
  evidence_ref: string | null;
  /** §10.4 (story S7.5.1): whether this verdict's own cell comparison ran on a
   * stratified sample rather than every shared key. A sampled PASS is labelled
   * SAMPLED wherever this verdict is shown. */
  sampled: boolean;
}

export interface ParityRunResponse {
  run_id: string;
  workbook_id: string;
  suite_ref: string;
  charter_version: string;
  started: string | null;
  finished: string | null;
  verdicts: VerdictRow[];
}

export interface RunParityResult {
  workbook_id: string;
  run_id: string;
  charter_version: string;
  cases_diffed: number;
  pass: number;
  fail: number;
  inconclusive: number;
  proved_by_report: string | null;
  results: Array<{
    case_id: string;
    verdict_id: string;
    result: string;
    failing_cell_count: number;
    evidence_ref: string;
    sampled: boolean;
  }>;
}

export interface RunVisualParityResult {
  workbook_id: string;
  visuals_scored: number;
  results: Array<{
    visual_id: string;
    sheet_ref: string;
    structural_score: number;
    image_score: number | null;
  }>;
}

/** §10.5's own "side-by-side images" — both captures for one scored visual, base64
 * encoded so the console can render them with a plain `<img>` data URI. */
export interface VisualCapturePair {
  visual_id: string;
  source: { media_type: string; content_base64: string };
  target: { media_type: string; content_base64: string };
}

// ------------------------------------------------------------- S8.3.1: the Exception Desk

/** One queue row (§11.3, §15.3.4) — every live OPEN/BLOCKED `ExceptionCase`, enriched with
 * its real train position, site and age. `train_id`/`train_sequence` are `null` for a
 * workbook the Train Planner has never sequenced (it sorts last). */
export interface ExceptionQueueEntry {
  id: string;
  mu_ref: string;
  class: string | null;
  passes_consumed: number | null;
  assignee: string | null;
  state: string;
  train_id: string | null;
  train_sequence: number | null;
  site: string | null;
  created_at: string | null;
  age_seconds: number | null;
}

export interface ExceptionQueueResponse {
  entries: ExceptionQueueEntry[];
  count: number;
}

export interface ExceptionQueueFilters {
  train?: string | null;
  failureClass?: string | null;
  site?: string | null;
  assignee?: string | null;
}

export interface ExceptionCaseFailingCell {
  case_ref: string;
  grain_key: unknown[];
  measure: string;
  kind?: string;
  expected: unknown;
  candidate: unknown;
  delta: number | null;
  reason?: string;
}

/** §10.3's own full evidence bundle, a wider read than the Mender's own repair-request
 * evidence — this case page's own `missing_keys`/`extra_keys` (the AC's own "key diffs")
 * and every case's own real `param_values` are read nowhere else. */
export interface ExceptionCaseEvidence {
  failing_cells: ExceptionCaseFailingCell[];
  missing_keys: unknown[][];
  extra_keys: unknown[][];
  filter_ctx: Record<string, unknown>;
  param_values: Record<string, Record<string, unknown>>;
}

export interface ExceptionCaseArtefact {
  calc_id: string | null;
  calc_name: string | null;
  source_formula: string | null;
  measure_id: string | null;
  current_dax: string | null;
  current_m_query: string | null;
}

export interface MenderPassRow {
  id: string;
  pass_number: number;
  strategy: string;
  result: string;
  measure_ref: string | null;
  cases_reproved: string[];
  cases_still_failing: string[];
  evidence_ref: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface ExceptionCaseDetail {
  id: string;
  mu_ref: string;
  class: string | null;
  state: string;
  passes_consumed: number | null;
  assignee: string | null;
  decision: string | null;
  train_id: string | null;
  train_sequence: number | null;
  site: string | null;
  created_at: string | null;
  evidence: ExceptionCaseEvidence;
  artefact: ExceptionCaseArtefact;
  mender_passes: MenderPassRow[];
  [key: string]: unknown;
}

export interface BulkAssignExceptionsResult {
  assignee: string;
  updated: string[];
  count: number;
}

export interface PatchExceptionResult {
  exception_case_id: string;
  gate_decision_id: string;
  measure_id: string;
  outcome: 'closed' | 'still_failing';
  cases_reproved: string[];
  cases_still_failing: string[];
}

export interface RedesignExceptionResult {
  exception_case_id: string;
  gate_decision_id: string;
  route: 'desktop' | 'foundry';
  detail: Record<string, unknown>;
}

export interface ModelDefectDecisionResult {
  exception_case_id: string;
  gate_decision_id: string;
  route_result: Record<string, unknown>;
}

export interface SourceDefectDecisionResult {
  exception_case_id: string;
  gate_decision_id: string;
  resolution: 'REPRODUCE' | 'FIX_WITH_SIGN_OFF';
  owner_sign_off: string | null;
  notified: boolean;
}

// ------------------------------------------------------- S8.3.2: exception ageing tile

export interface ExceptionAgeingEntry {
  class: string;
  age_band: string;
  age_band_label: string;
  count: number;
}

export interface AgeBand {
  key: string;
  label: string;
}

/** §16.6/§25's own literal "Mender close rate" -- a Mender-only close (no human
 * Exception Desk decision) over every real failure, `VISUAL_REDESIGN` excluded (it is
 * never something the Mender can repair). `rate`/`meets_target` are `null` when no
 * eligible failure exists yet, an honest disclosed-absent rather than a misleading 0. */
export interface MenderCloseRateSummary {
  mender_closed: number;
  total_failures: number;
  rate: number | null;
  target: number;
  meets_target: boolean | null;
}

export interface ExceptionAgeingResponse {
  open_by_class_and_age_band: ExceptionAgeingEntry[];
  age_bands: AgeBand[];
  total_open: number;
  mender_close_rate: MenderCloseRateSummary;
}

// ------------------------------------------------------------- S9.1.1: the G3 gate card

export interface G3CardWhat {
  name: string | null;
  site: string | null;
  pages: number;
  visuals: number;
}

export interface G3CardWaiver {
  subject_ref: string | null;
  approver: string | null;
  rationale: string | null;
  timestamp: string | null;
}

export interface G3CardProof {
  cases_run: number;
  cases_pass: number;
  charter_version: string | null;
  sampled: boolean;
  passes_the_charter: boolean;
  waivers: G3CardWaiver[];
}

export interface G3CardVisualReview {
  visual_id: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
}

export interface G3CardVisual {
  structural_score: number | null;
  image_score: number | null;
  reviewed: G3CardVisualReview[];
  human_review_status: 'reviewed' | 'not yet reviewed';
}

export interface G3CardC4Decision {
  calc_id: string;
  name: string | null;
  redesign_decision: string | null;
  redesign_decision_reason: string | null;
  redesign_decision_by: string | null;
  redesign_decision_at: string | null;
}

export interface G3CardRedesign {
  subject_ref: string | null;
  approver: string | null;
  rationale: string | null;
  timestamp: string | null;
}

export interface G3CardModel {
  family_id: string;
  name: string | null;
  state: string | null;
  approved_at: string | null;
}

export interface G3CardChanges {
  c4_decisions: G3CardC4Decision[];
  redesigns: G3CardRedesign[];
  model: G3CardModel | null;
}

export interface G3CardNext {
  on_approval: string;
  parallel_window_weeks: number;
}

export interface G3CardLatestDecision {
  decision: string;
  approver: string;
  countersigner: string | null;
  timestamp: string;
  rationale: string | null;
}

export interface G3Card {
  workbook_id: string;
  what: G3CardWhat;
  proof: G3CardProof;
  visual: G3CardVisual;
  changes: G3CardChanges;
  next: G3CardNext;
  latest_decision: G3CardLatestDecision | null;
}

export interface G3DecisionResult {
  workbook_id: string;
  gate_decision_id: string;
  decision: string;
  /** S9.1.2: whether Approve also raised a real mu.accepted invoice line -- false on
   * Request changes/Ask a question (never invoiced) and on an Approve for a workbook
   * with no real tier set yet (honestly skipped, not invoiced with a guessed tier). */
  invoiced: boolean;
  tier: string | null;
  unit_price: number | null;
}

export interface G3Question {
  id: string;
  workbook_id: string;
  question: string;
  asked_by: string;
  asked_at: string;
}

// ---------------------------------------------------- S9.1.2: accepted units by tier

export interface AcceptanceTierRow {
  tier: string;
  accepted: number;
  planned: number;
  delta: number;
  unit_price: number;
  accepted_value: number;
}

export interface AcceptanceSummary {
  by_tier: AcceptanceTierRow[];
  total_accepted: number;
  total_planned: number;
  total_accepted_value: number;
}

// ---------------------------------------------- S9.2.1: promotion pipeline and the Release Board

export interface PromotionStep {
  name: string;
  ok: boolean;
  detail: string;
}

export interface PromotionRecord {
  id: string;
  workbook_id: string;
  to_stage: string;
  workspace: string;
  state: string;
  steps: PromotionStep[];
  model_git_ref: string | null;
  report_deploy_id: string | null;
  approved_by: string | null;
  approver_role: string | null;
  rationale: string | null;
  triggered_by: string;
  started_at: string;
  finished_at: string;
}

export interface ReleaseBoardMu {
  workbook_id: string;
  name: string;
  sequence: number;
  /** "NOT_ACCEPTED" | "ACCEPTED" | "TEST" | "PROD" -- derived, never a stored flag. */
  stage: string;
  next_stage: 'test' | 'prod' | null;
  blockers: string[];
  evidence: PromotionRecord[];
}

export interface ReleaseBoardTrain {
  id: string;
  name: string | null;
  mus: ReleaseBoardMu[];
}

export interface ReleaseSiteRow {
  site_id: string;
  name: string;
  released_mu_count: number;
  total_mu_count: number;
  parallel_run_start: string | null;
  parallel_run_end: string | null;
}

export interface ReleaseBoard {
  trains: ReleaseBoardTrain[];
  sites: ReleaseSiteRow[];
}

export interface AdoptionSnapshot {
  id: string;
  workbook_id: string;
  captured_at: string;
  /** `null` when the source adapter's own usage capability is absent -- never a fabricated zero. */
  source_views: number | null;
  target_views: number;
  /** `null` with no real source-side denominator (S9.2.2). */
  ratio: number | null;
  /** The configured threshold *at capture time* -- frozen on the row. */
  threshold: number;
  meets_threshold: boolean | null;
  triggered_by: string;
}

export interface DecommissionTrackerMu {
  workbook_id: string;
  name: string;
  /** `null` until the first weekly capture has run for this MU. */
  snapshot: AdoptionSnapshot | null;
}

export interface DecommissionTrackerSite {
  site_id: string;
  name: string;
  mus: DecommissionTrackerMu[];
  released_mu_count: number;
  meeting_threshold_count: number;
}

export interface DecommissionTracker {
  threshold: number;
  sites: DecommissionTrackerSite[];
}

export interface AdoptionConfig {
  threshold: number;
}

export interface AdoptionCaptureResult {
  captured: AdoptionSnapshot[];
  count: number;
}

export interface ReadinessItem {
  key: string;
  label: string;
  met: boolean;
  evidence: Record<string, unknown>;
}

export interface G4CardMu {
  workbook_id: string;
  name: string;
}

export interface G4LatestDecision {
  decision: string;
  approver: string | null;
  countersigner: string | null;
  timestamp: string | null;
  rationale: string | null;
  target_date: string | null;
}

export interface G4Card {
  site_id: string;
  name: string;
  licence_tier: string | null;
  licence_cost_annual: number | null;
  mus: G4CardMu[];
  released_mu_count: number;
  source_workbooks_to_archive: G4CardMu[];
  confirmation_text: string;
  ready: boolean;
  checklist: ReadinessItem[];
  next: { on_approval: string };
  latest_decision: G4LatestDecision | null;
}

export interface G4DecisionResult {
  site_id: string;
  gate_decision_id: string;
  decision: string;
  archived_count: number;
  licence_release_value: number | null;
  decommissioned_at: string | null;
  target_date: string | null;
}

export interface DecommissionConfirmation {
  workbook_id: string;
  confirmed_by: string;
  confirmed_at: string;
}

export interface Api {
  estate(query: EstateQuery, identity: Identity): Promise<EstateResponse>;
  workbook(id: string, identity: Identity): Promise<WorkbookDetail>;
  reTier(id: string, tier: string, reason: string, identity: Identity): Promise<unknown>;
  withdraw(id: string, reason: string, identity: Identity): Promise<unknown>;
  reinstate(id: string, reason: string, identity: Identity): Promise<unknown>;
  reharvest(site: string, credential: string, identity: Identity): Promise<{ id: string }>;
  lineage(query: LineageQuery, identity: Identity): Promise<LineageResponse>;
  parseQualityQueue(identity: Identity): Promise<QueueResponse>;
  constructs(identity: Identity): Promise<ConstructsResponse>;
  markIgnorable(
    construct: string,
    reason: string,
    identity: Identity,
  ): Promise<{ workbooks_released: number; workbooks_rescored: number }>;
  openGrammarIssue(
    construct: string,
    summary: string,
    detail: string,
    identity: Identity,
  ): Promise<GrammarIssue>;
  programmes(identity: Identity): Promise<ProgrammesResponse>;
  confirmFamilyCount(programmeId: string, identity: Identity): Promise<ProgrammeRecord>;
  trains(identity: Identity): Promise<TrainsResponse>;
  moveMember(
    trainId: string,
    workbookId: string,
    identity: Identity,
    reason?: string,
  ): Promise<MoveMemberResult>;
  resequenceMember(
    trainId: string,
    workbookId: string,
    position: number,
    identity: Identity,
  ): Promise<{ train_id: string; workbook_id: string; position: number }>;
  setWipLimits(
    trainId: string,
    trainLimit: number | null,
    stateLimits: Record<string, number>,
    reason: string,
    identity: Identity,
  ): Promise<{ train_id: string; wip_limits: WipLimits }>;
  trainEvents(trainId: string, identity: Identity): Promise<TrainEventsResponse>;
  trainProjections(identity: Identity): Promise<TrainProjectionsResponse>;
  families(identity: Identity): Promise<FamiliesResponse>;
  family(familyId: string, identity: Identity): Promise<FamilyRecord>;
  proposeDesign(familyId: string, identity: Identity): Promise<DesignDocument>;
  getDesign(familyId: string, identity: Identity, semanticModelId?: string): Promise<DesignDocument>;
  getVersions(familyId: string, identity: Identity): Promise<VersionsResponse>;
  requestNewVersion(familyId: string, reason: string, identity: Identity): Promise<RequestNewVersionResult>;
  promote(familyId: string, identity: Identity): Promise<PromoteResult>;
  acceptFamily(familyId: string, identity: Identity): Promise<FamilyRecord>;
  submitForReview(
    familyId: string,
    identity: Identity,
  ): Promise<FamilyRecord & { semantic_model_id: string; version: string }>;
  familyTransitions(familyId: string, identity: Identity): Promise<FamilyTransitionsResponse>;
  editGrainStatement(
    familyId: string,
    grainStatement: string,
    identity: Identity,
  ): Promise<{ family_id: string; semantic_model_id: string; grain_statement: string }>;
  setTableMode(
    familyId: string,
    tableId: string,
    mode: string,
    identity: Identity,
  ): Promise<{ family_id: string; table_id: string; mode: string }>;
  setRelationshipCardinality(
    familyId: string,
    fromTable: string,
    toTable: string,
    cardinality: string,
    identity: Identity,
  ): Promise<{ family_id: string; semantic_model_id: string; relationship: DesignRelationship }>;
  editDomain(familyId: string, domain: string, identity: Identity): Promise<{ family_id: string; domain: string }>;
  editOwner(familyId: string, owner: string, identity: Identity): Promise<{ family_id: string; owner: string }>;
  awaitingG2(identity: Identity): Promise<AwaitingG2Response>;
  sendG2Reminders(identity: Identity): Promise<SendG2RemindersResponse>;
  familiesForReview(identity: Identity): Promise<FamiliesResponse>;
  proposal(familyId: string, identity: Identity): Promise<ModelProposal>;
  questions(familyId: string, identity: Identity): Promise<QuestionsResponse>;
  askQuestion(familyId: string, question: string, category: string, identity: Identity): Promise<G2Question>;
  replyToQuestion(questionId: string, message: string, identity: Identity): Promise<G2Question>;
  answerQuestion(questionId: string, identity: Identity): Promise<G2Question>;
  approveG2(
    familyId: string,
    countersignedBy: string,
    rationale: string,
    identity: Identity,
  ): Promise<ApproveResult>;
  requestChangesG2(
    familyId: string,
    comment: string,
    identity: Identity,
  ): Promise<RequestChangesResult>;
  getBuild(familyId: string, identity: Identity): Promise<BuildResponse>;
  triggerBuild(familyId: string, identity: Identity): Promise<BuildRecord>;
  conformanceRules(identity: Identity): Promise<ConformanceRulesResponse>;
  saveConformanceRules(rules: RuleConfig[], identity: Identity): Promise<ConformanceRulesResponse>;
  toleranceCharter(identity: Identity): Promise<ToleranceCharterResponse>;
  saveToleranceCharter(
    charter: ToleranceCharter,
    identity: Identity,
    clientAnalyticsLeadAck?: string,
    reason?: string,
  ): Promise<SaveToleranceCharterResult>;
  approveG1(
    version: number,
    countersignedBy: string,
    rationale: string,
    identity: Identity,
  ): Promise<ApproveG1Result>;
  simulateToleranceCharter(
    workbookId: string,
    charter: ToleranceCharter,
    identity: Identity,
  ): Promise<SimulateResult>;
  classMix(identity: Identity): Promise<ClassMix>;
  reclassify(identity: Identity): Promise<ReclassifyResult>;
  ruleCatalog(identity: Identity): Promise<RuleCatalog>;
  ruleCoverage(identity: Identity): Promise<RuleCoverage>;
  applyRules(identity: Identity): Promise<ApplyRulesResult>;
  patterns(identity: Identity): Promise<PatternsResponse>;
  patternPromotionStatus(patternId: string, identity: Identity): Promise<PatternPromotionStatus>;
  promotePattern(patternId: string, identity: Identity): Promise<PatternRecord>;
  retirePattern(patternId: string, reason: string, identity: Identity): Promise<PatternRecord>;
  editPatternGuards(
    patternId: string,
    guards: string[],
    reason: string,
    identity: Identity,
  ): Promise<PatternRecord>;
  parityDashboard(workbookId: string, identity: Identity): Promise<ParityDashboardResponse>;
  parityRun(workbookId: string, identity: Identity): Promise<ParityRunResponse>;
  runParity(workbookId: string, identity: Identity): Promise<RunParityResult>;
  runVisualParity(workbookId: string, identity: Identity): Promise<RunVisualParityResult>;
  visualCaptures(workbookId: string, visualId: string, identity: Identity): Promise<VisualCapturePair>;
  regressionMonitor(identity: Identity): Promise<RegressionMonitorResponse>;
  scheduleRegression(
    workbookId: string,
    workspace: string,
    identity: Identity,
    cadence?: { every_minutes: number } | { daily_at: string },
  ): Promise<RegressionScheduleRecord>;
  exportRegressionSuite(workbookId: string, identity: Identity): Promise<RegressionExportRecord>;
  exceptionQueue(filters: ExceptionQueueFilters, identity: Identity): Promise<ExceptionQueueResponse>;
  exceptionCase(exceptionCaseId: string, identity: Identity): Promise<ExceptionCaseDetail>;
  bulkAssignExceptions(
    exceptionCaseIds: string[],
    assignee: string,
    identity: Identity,
  ): Promise<BulkAssignExceptionsResult>;
  patchException(
    exceptionCaseId: string,
    dax: string,
    rationale: string,
    workspace: string,
    identity: Identity,
  ): Promise<PatchExceptionResult>;
  redesignException(
    exceptionCaseId: string,
    route: 'desktop' | 'foundry',
    rationale: string,
    identity: Identity,
    desktopCommitHash?: string,
  ): Promise<RedesignExceptionResult>;
  decideModelDefect(
    exceptionCaseId: string,
    rationale: string,
    identity: Identity,
  ): Promise<ModelDefectDecisionResult>;
  decideSourceDefect(
    exceptionCaseId: string,
    rationale: string,
    resolution: 'REPRODUCE' | 'FIX_WITH_SIGN_OFF',
    identity: Identity,
    ownerSignOff?: string,
  ): Promise<SourceDefectDecisionResult>;
  exceptionAgeing(identity: Identity): Promise<ExceptionAgeingResponse>;
  g3Card(workbookId: string, identity: Identity, format?: 'adaptive_card'): Promise<G3Card>;
  approveG3(
    workbookId: string,
    rationale: string,
    countersignedBy: string,
    identity: Identity,
  ): Promise<G3DecisionResult>;
  requestChangesG3(workbookId: string, rationale: string, identity: Identity): Promise<G3DecisionResult>;
  askG3Question(workbookId: string, question: string, identity: Identity): Promise<G3Question>;
  g3Questions(workbookId: string, identity: Identity): Promise<{ questions: G3Question[]; count: number }>;
  acceptanceSummary(identity: Identity): Promise<AcceptanceSummary>;
  releaseBoard(identity: Identity): Promise<ReleaseBoard>;
  promoteToTest(workbookId: string, identity: Identity): Promise<PromotionRecord>;
  promoteToProd(workbookId: string, rationale: string, identity: Identity): Promise<PromotionRecord>;
  decommissionTracker(identity: Identity): Promise<DecommissionTracker>;
  adoptionConfig(identity: Identity): Promise<AdoptionConfig>;
  setAdoptionConfig(threshold: number, identity: Identity): Promise<AdoptionConfig>;
  captureAdoption(identity: Identity): Promise<AdoptionCaptureResult>;
  g4Card(siteId: string, identity: Identity): Promise<G4Card>;
  approveG4(siteId: string, rationale: string, countersignedBy: string, identity: Identity): Promise<G4DecisionResult>;
  deferG4(siteId: string, reason: string, targetDate: string, identity: Identity): Promise<G4DecisionResult>;
  confirmDecommission(workbookId: string, identity: Identity): Promise<DecommissionConfirmation>;
}

export function createApi(base = ''): Api {
  const get = async (path: string, identity: Identity): Promise<unknown> =>
    unwrap(await fetch(`${base}${path}`, { headers: headers(identity) }));

  const post = async (path: string, body: unknown, identity: Identity): Promise<unknown> =>
    unwrap(
      await fetch(`${base}${path}`, {
        method: 'POST',
        headers: headers(identity),
        body: JSON.stringify(body),
      }),
    );

  return {
    async estate(query, identity) {
      return (await get(`/v1/estate${estateQueryString(query)}`, identity)) as EstateResponse;
    },
    async workbook(id, identity) {
      return (await get(`/v1/estate/workbooks/${id}`, identity)) as WorkbookDetail;
    },
    reTier: (id, tier, reason, identity) =>
      post(`/v1/estate/workbooks/${id}:re-tier`, { tier, reason }, identity),
    withdraw: (id, reason, identity) =>
      post(`/v1/estate/workbooks/${id}:withdraw`, { reason }, identity),
    reinstate: (id, reason, identity) =>
      post(`/v1/estate/workbooks/${id}:reinstate`, { reason }, identity),
    async reharvest(site, credential, identity) {
      return (await post('/v1/harvests', { site, credential }, identity)) as { id: string };
    },
    async parseQualityQueue(identity) {
      return (await get('/v1/parse-quality/queue', identity)) as QueueResponse;
    },
    async constructs(identity) {
      return (await get('/v1/parse-quality/constructs', identity)) as ConstructsResponse;
    },
    async markIgnorable(construct, reason, identity) {
      return (await post(
        '/v1/parse-quality/constructs:ignorable',
        { construct, reason },
        identity,
      )) as { workbooks_released: number; workbooks_rescored: number };
    },
    async openGrammarIssue(construct, summary, detail, identity) {
      return (await post(
        '/v1/parse-quality/constructs:issue',
        { construct, summary, detail },
        identity,
      )) as GrammarIssue;
    },
    async programmes(identity) {
      return (await get('/v1/programmes', identity)) as ProgrammesResponse;
    },
    async confirmFamilyCount(programmeId, identity) {
      return (await post(
        `/v1/programmes/${programmeId}:confirm-family-count`,
        {},
        identity,
      )) as ProgrammeRecord;
    },
    async trains(identity) {
      return (await get('/v1/trains', identity)) as TrainsResponse;
    },
    async moveMember(trainId, workbookId, identity, reason) {
      return (await post(
        `/v1/trains/${trainId}:move-member`,
        { workbook_id: workbookId, reason: reason ?? null },
        identity,
      )) as MoveMemberResult;
    },
    async resequenceMember(trainId, workbookId, position, identity) {
      return (await post(
        `/v1/trains/${trainId}:resequence-member`,
        { workbook_id: workbookId, position },
        identity,
      )) as { train_id: string; workbook_id: string; position: number };
    },
    async setWipLimits(trainId, trainLimit, stateLimits, reason, identity) {
      return (await post(
        `/v1/trains/${trainId}:set-wip-limits`,
        { train_limit: trainLimit, state_limits: stateLimits, reason },
        identity,
      )) as { train_id: string; wip_limits: WipLimits };
    },
    async trainEvents(trainId, identity) {
      return (await get(`/v1/trains/${trainId}/events`, identity)) as TrainEventsResponse;
    },
    async trainProjections(identity) {
      return (await get('/v1/trains:projections', identity)) as TrainProjectionsResponse;
    },
    async families(identity) {
      return (await get('/v1/families', identity)) as FamiliesResponse;
    },
    async family(familyId, identity) {
      return (await get(`/v1/families/${familyId}`, identity)) as FamilyRecord;
    },
    async proposeDesign(familyId, identity) {
      return (await post(`/v1/families/${familyId}:propose-design`, {}, identity)) as DesignDocument;
    },
    async getDesign(familyId, identity, semanticModelId) {
      const query = semanticModelId ? `?semantic_model_id=${encodeURIComponent(semanticModelId)}` : '';
      return (await get(`/v1/families/${familyId}/design${query}`, identity)) as DesignDocument;
    },
    async getVersions(familyId, identity) {
      return (await get(`/v1/families/${familyId}/versions`, identity)) as VersionsResponse;
    },
    async requestNewVersion(familyId, reason, identity) {
      return (await post(
        `/v1/families/${familyId}:request-new-version`,
        { reason },
        identity,
      )) as RequestNewVersionResult;
    },
    async promote(familyId, identity) {
      return (await post(`/v1/families/${familyId}:promote`, {}, identity)) as PromoteResult;
    },
    async acceptFamily(familyId, identity) {
      return (await post(`/v1/families/${familyId}:accept`, {}, identity)) as FamilyRecord;
    },
    async submitForReview(familyId, identity) {
      return (await post(`/v1/families/${familyId}:submit-for-review`, {}, identity)) as FamilyRecord & {
        semantic_model_id: string;
        version: string;
      };
    },
    async familyTransitions(familyId, identity) {
      return (await get(`/v1/families/${familyId}/transitions`, identity)) as FamilyTransitionsResponse;
    },
    async editGrainStatement(familyId, grainStatement, identity) {
      return (await post(
        `/v1/families/${familyId}:edit-grain-statement`,
        { grain_statement: grainStatement },
        identity,
      )) as { family_id: string; semantic_model_id: string; grain_statement: string };
    },
    async setTableMode(familyId, tableId, mode, identity) {
      return (await post(
        `/v1/families/${familyId}/tables/${tableId}:set-mode`,
        { mode },
        identity,
      )) as { family_id: string; table_id: string; mode: string };
    },
    async setRelationshipCardinality(familyId, fromTable, toTable, cardinality, identity) {
      return (await post(
        `/v1/families/${familyId}/relationships:set-cardinality`,
        { from_table: fromTable, to_table: toTable, cardinality },
        identity,
      )) as { family_id: string; semantic_model_id: string; relationship: DesignRelationship };
    },
    async editDomain(familyId, domain, identity) {
      return (await post(`/v1/families/${familyId}:edit-domain`, { domain }, identity)) as {
        family_id: string;
        domain: string;
      };
    },
    async editOwner(familyId, owner, identity) {
      return (await post(`/v1/families/${familyId}:edit-owner`, { owner }, identity)) as {
        family_id: string;
        owner: string;
      };
    },
    async awaitingG2(identity) {
      return (await get('/v1/families:awaiting-g2', identity)) as AwaitingG2Response;
    },
    async sendG2Reminders(identity) {
      return (await post('/v1/g2/reminders:send', {}, identity)) as SendG2RemindersResponse;
    },
    async familiesForReview(identity) {
      return (await get('/v1/families:for-review', identity)) as FamiliesResponse;
    },
    async proposal(familyId, identity) {
      return (await get(`/v1/families/${familyId}/proposal`, identity)) as ModelProposal;
    },
    async questions(familyId, identity) {
      return (await get(`/v1/families/${familyId}/questions`, identity)) as QuestionsResponse;
    },
    async askQuestion(familyId, question, category, identity) {
      return (await post(
        `/v1/families/${familyId}/questions:ask`,
        { question, category },
        identity,
      )) as G2Question;
    },
    async replyToQuestion(questionId, message, identity) {
      return (await post(`/v1/questions/${questionId}:reply`, { message }, identity)) as G2Question;
    },
    async answerQuestion(questionId, identity) {
      return (await post(`/v1/questions/${questionId}:answer`, {}, identity)) as G2Question;
    },
    async approveG2(familyId, countersignedBy, rationale, identity) {
      return (await post(
        `/v1/families/${familyId}:approve-g2`,
        { countersigned_by: countersignedBy, rationale },
        identity,
      )) as ApproveResult;
    },
    async requestChangesG2(familyId, comment, identity) {
      return (await post(
        `/v1/families/${familyId}:request-changes`,
        { comment },
        identity,
      )) as RequestChangesResult;
    },
    async getBuild(familyId, identity) {
      return (await get(`/v1/families/${familyId}/build`, identity)) as BuildResponse;
    },
    async triggerBuild(familyId, identity) {
      return (await post(`/v1/families/${familyId}:build`, {}, identity)) as BuildRecord;
    },
    async conformanceRules(identity) {
      return (await get('/v1/conformance/rules', identity)) as ConformanceRulesResponse;
    },
    async saveConformanceRules(rules, identity) {
      return (await post('/v1/conformance/rules', { rules }, identity)) as ConformanceRulesResponse;
    },
    async toleranceCharter(identity) {
      return (await get('/v1/tolerance-charter', identity)) as ToleranceCharterResponse;
    },
    async saveToleranceCharter(charter, identity, clientAnalyticsLeadAck, reason) {
      return (await post(
        '/v1/tolerance-charter',
        { charter, client_analytics_lead_ack: clientAnalyticsLeadAck ?? null, reason: reason ?? null },
        identity,
      )) as SaveToleranceCharterResult;
    },
    async approveG1(version, countersignedBy, rationale, identity) {
      return (await post(
        `/v1/tolerance-charter/${version}:approve-g1`,
        { countersigned_by: countersignedBy, rationale },
        identity,
      )) as ApproveG1Result;
    },
    async simulateToleranceCharter(workbookId, charter, identity) {
      return (await post(
        `/v1/workbooks/${workbookId}/tolerance-charter:simulate`,
        { charter },
        identity,
      )) as SimulateResult;
    },
    async classMix(identity) {
      return (await get('/v1/calculations:class-mix', identity)) as ClassMix;
    },
    async reclassify(identity) {
      return (await post('/v1/calculations:reclassify', {}, identity)) as ReclassifyResult;
    },
    async ruleCatalog(identity) {
      return (await get('/v1/calculations:rule-catalog', identity)) as RuleCatalog;
    },
    async ruleCoverage(identity) {
      return (await get('/v1/calculations:rule-coverage', identity)) as RuleCoverage;
    },
    async applyRules(identity) {
      return (await post('/v1/calculations:apply-rules', {}, identity)) as ApplyRulesResult;
    },
    async patterns(identity) {
      return (await get('/v1/patterns', identity)) as PatternsResponse;
    },
    async patternPromotionStatus(patternId, identity) {
      return (await get(`/v1/patterns/${patternId}:promotion-status`, identity)) as PatternPromotionStatus;
    },
    async promotePattern(patternId, identity) {
      return (await post(`/v1/patterns/${patternId}:promote`, {}, identity)) as PatternRecord;
    },
    async retirePattern(patternId, reason, identity) {
      return (await post(`/v1/patterns/${patternId}:retire`, { reason }, identity)) as PatternRecord;
    },
    async editPatternGuards(patternId, guards, reason, identity) {
      return (await post(
        `/v1/patterns/${patternId}:edit-guards`,
        { guards, reason },
        identity,
      )) as PatternRecord;
    },
    async lineage(query, identity) {
      const params = new URLSearchParams();
      for (const [key, value] of Object.entries(query)) {
        if (value === null || value === undefined || value === '') continue;
        params.set(key, String(value));
      }
      const rendered = params.toString();
      return (await get(`/v1/lineage${rendered ? `?${rendered}` : ''}`, identity)) as
        LineageResponse;
    },
    async parityDashboard(workbookId, identity) {
      return (await get(`/v1/workbooks/${workbookId}/parity-dashboard`, identity)) as ParityDashboardResponse;
    },
    async parityRun(workbookId, identity) {
      return (await get(`/v1/workbooks/${workbookId}/parity-run`, identity)) as ParityRunResponse;
    },
    async runParity(workbookId, identity) {
      return (await post(`/v1/workbooks/${workbookId}:run-parity`, {}, identity)) as RunParityResult;
    },
    async runVisualParity(workbookId, identity) {
      return (await post(`/v1/workbooks/${workbookId}:run-visual-parity`, {}, identity)) as RunVisualParityResult;
    },
    async visualCaptures(workbookId, visualId, identity) {
      return (await get(
        `/v1/workbooks/${workbookId}/visual-captures/${visualId}`,
        identity,
      )) as VisualCapturePair;
    },
    async regressionMonitor(identity) {
      return (await get('/v1/regression-monitor', identity)) as RegressionMonitorResponse;
    },
    async scheduleRegression(workbookId, workspace, identity, cadence) {
      return (await post(
        `/v1/workbooks/${workbookId}:schedule-regression`,
        cadence ? { workspace, cadence } : { workspace },
        identity,
      )) as RegressionScheduleRecord;
    },
    async exportRegressionSuite(workbookId, identity) {
      return (await post(
        `/v1/workbooks/${workbookId}:export-regression-suite`,
        {},
        identity,
      )) as RegressionExportRecord;
    },
    async exceptionQueue(filters, identity) {
      const params = new URLSearchParams();
      if (filters.train) params.set('train', filters.train);
      if (filters.failureClass) params.set('class', filters.failureClass);
      if (filters.site) params.set('site', filters.site);
      if (filters.assignee) params.set('assignee', filters.assignee);
      const rendered = params.toString();
      return (await get(
        `/v1/exception-desk${rendered ? `?${rendered}` : ''}`,
        identity,
      )) as ExceptionQueueResponse;
    },
    async exceptionCase(exceptionCaseId, identity) {
      return (await get(`/v1/exceptions/${exceptionCaseId}`, identity)) as ExceptionCaseDetail;
    },
    async bulkAssignExceptions(exceptionCaseIds, assignee, identity) {
      return (await post(
        '/v1/exceptions:bulk-assign',
        { exception_case_ids: exceptionCaseIds, assignee },
        identity,
      )) as BulkAssignExceptionsResult;
    },
    async patchException(exceptionCaseId, dax, rationale, workspace, identity) {
      return (await post(
        `/v1/exceptions/${exceptionCaseId}:patch`,
        { dax, rationale, workspace },
        identity,
      )) as PatchExceptionResult;
    },
    async redesignException(exceptionCaseId, route, rationale, identity, desktopCommitHash) {
      return (await post(
        `/v1/exceptions/${exceptionCaseId}:redesign`,
        { route, rationale, desktop_commit_hash: desktopCommitHash ?? null },
        identity,
      )) as RedesignExceptionResult;
    },
    async decideModelDefect(exceptionCaseId, rationale, identity) {
      return (await post(
        `/v1/exceptions/${exceptionCaseId}:decide-model-defect`,
        { rationale },
        identity,
      )) as ModelDefectDecisionResult;
    },
    async decideSourceDefect(exceptionCaseId, rationale, resolution, identity, ownerSignOff) {
      return (await post(
        `/v1/exceptions/${exceptionCaseId}:decide-source-defect`,
        { rationale, resolution, owner_sign_off: ownerSignOff ?? null },
        identity,
      )) as SourceDefectDecisionResult;
    },
    async exceptionAgeing(identity) {
      return (await get('/v1/exceptions:ageing', identity)) as ExceptionAgeingResponse;
    },
    async g3Card(workbookId, identity, format) {
      const query = format ? `?format=${format}` : '';
      return (await get(`/v1/workbooks/${workbookId}:g3-card${query}`, identity)) as G3Card;
    },
    async approveG3(workbookId, rationale, countersignedBy, identity) {
      return (await post(
        `/v1/workbooks/${workbookId}:approve-g3`,
        { rationale, countersigned_by: countersignedBy },
        identity,
      )) as G3DecisionResult;
    },
    async requestChangesG3(workbookId, rationale, identity) {
      return (await post(
        `/v1/workbooks/${workbookId}:request-changes-g3`,
        { rationale },
        identity,
      )) as G3DecisionResult;
    },
    async askG3Question(workbookId, question, identity) {
      return (await post(
        `/v1/workbooks/${workbookId}:ask-g3-question`,
        { question },
        identity,
      )) as G3Question;
    },
    async g3Questions(workbookId, identity) {
      return (await get(`/v1/workbooks/${workbookId}/g3-questions`, identity)) as {
        questions: G3Question[];
        count: number;
      };
    },
    async acceptanceSummary(identity) {
      return (await get('/v1/programmes:acceptance', identity)) as AcceptanceSummary;
    },
    async releaseBoard(identity) {
      return (await get('/v1/release:board', identity)) as ReleaseBoard;
    },
    async promoteToTest(workbookId, identity) {
      return (await post(`/v1/workbooks/${workbookId}:promote-to-test`, {}, identity)) as PromotionRecord;
    },
    async promoteToProd(workbookId, rationale, identity) {
      return (await post(
        `/v1/workbooks/${workbookId}:promote-to-prod`,
        { rationale },
        identity,
      )) as PromotionRecord;
    },
    async decommissionTracker(identity) {
      return (await get('/v1/decommission:tracker', identity)) as DecommissionTracker;
    },
    async adoptionConfig(identity) {
      return (await get('/v1/adoption:config', identity)) as AdoptionConfig;
    },
    async setAdoptionConfig(threshold, identity) {
      return (await post('/v1/adoption:config', { threshold }, identity)) as AdoptionConfig;
    },
    async captureAdoption(identity) {
      return (await post('/v1/adoption:capture', {}, identity)) as AdoptionCaptureResult;
    },
    async g4Card(siteId, identity) {
      return (await get(`/v1/sites/${siteId}:g4-card`, identity)) as G4Card;
    },
    async approveG4(siteId, rationale, countersignedBy, identity) {
      return (await post(
        `/v1/sites/${siteId}:approve-g4`,
        { rationale, countersigned_by: countersignedBy },
        identity,
      )) as G4DecisionResult;
    },
    async deferG4(siteId, reason, targetDate, identity) {
      return (await post(
        `/v1/sites/${siteId}:defer-g4`,
        { reason, target_date: targetDate },
        identity,
      )) as G4DecisionResult;
    },
    async confirmDecommission(workbookId, identity) {
      return (await post(`/v1/workbooks/${workbookId}:confirm-decommission`, {}, identity)) as DecommissionConfirmation;
    },
  };
}
