export type SourceStatus =
  | 'realtime_verified'
  | 'official_reference'
  | 'map_reference'
  | 'guide_reference'
  | 'user_confirmation_required';

export type DocumentStatus = 'formal' | 'degraded';
export type SectionStatus = 'ready' | 'degraded' | 'unavailable' | 'user_confirmation_required';
export type ActivityKind =
  | 'attraction'
  | 'food'
  | 'transport'
  | 'hotel'
  | 'shopping'
  | 'free_time'
  | 'other';

export type PlanningLifecycleStatus =
  | 'idle'
  | 'planning'
  | 'completed'
  | 'completed_degraded'
  | 'cancelled'
  | 'error'
  | 'draft'
  | 'demo_preview'
  | 'demo_replaying'
  | 'demo_completed';

export const planningStatusLabels: Record<PlanningLifecycleStatus, string> = {
  idle: '待开始',
  planning: '规划中',
  completed: '规划已完成',
  completed_degraded: '已完成，部分信息待确认',
  cancelled: '规划已停止',
  error: '生成失败',
  draft: '有未应用修改',
  demo_preview: '案例预览',
  demo_replaying: '示例回放中',
  demo_completed: '示例回放完成',
};

export interface TravelerCountV3 {
  adults: number;
  children: number;
  seniors: number;
}

export interface MoneyV3 {
  amount: number;
  currency: string;
  cny_reference_amount?: number | null;
  exchange_rate_as_of?: string | null;
  source_status: SourceStatus;
}

export interface TripIntentV3 {
  origin: string;
  destination: string;
  date_mode: 'fixed' | 'flexible';
  start_date: string | null;
  end_date: string | null;
  days: number;
  travelers: TravelerCountV3;
  budget: MoneyV3;
  preferences: string[];
  dietary_preferences: string[];
  pace: 'relaxed' | 'balanced' | 'intensive';
}

export interface CoordinatesV3 {
  latitude: number;
  longitude: number;
  coordinate_system: 'WGS84' | 'BD09LL';
}

export interface ReviewSummaryV3 {
  text: string;
  source_refs: string[];
  updated_at?: string | null;
}

export interface TripPlaceV3 {
  poi_id: string;
  name: string;
  category: ActivityKind;
  city?: string | null;
  address?: string | null;
  phone?: string | null;
  opening_hours?: string | null;
  timezone?: string | null;
  coordinates?: CoordinatesV3 | null;
  summary?: string | null;
  review_summary?: ReviewSummaryV3 | null;
  evidence_refs: string[];
}

export interface ImageAssetV3 {
  image_id: string;
  url: string;
  alt: string;
  mime_type?: string | null;
  width?: number | null;
  height?: number | null;
  display_allowed: boolean;
  export_allowed: boolean;
  attribution_required: boolean;
  attribution_text?: string | null;
  attribution_url?: string | null;
  provider_name?: string | null;
  provider_url?: string | null;
  download_location?: string | null;
  source_ref?: string | null;
  checked_at?: string | null;
}

export interface TransportTimeV3 {
  display_text: string;
  utc?: string | null;
  local_iso?: string | null;
  timezone?: string | null;
  beijing_iso?: string | null;
  day_offset: number;
}

export interface TransportSeatOptionV3 {
  name: string;
  availability: 'available' | 'limited' | 'unavailable' | 'unknown';
  remaining_text?: string | null;
  price?: MoneyV3 | null;
}

export interface TransportOptionV3 {
  option_id: string;
  mode: 'train' | 'intercity_bus' | 'flight';
  service_number?: string | null;
  departure_place?: string | null;
  arrival_place?: string | null;
  departure_hub?: TripPlaceV3 | null;
  arrival_hub?: TripPlaceV3 | null;
  departure_time?: TransportTimeV3 | null;
  arrival_time?: TransportTimeV3 | null;
  duration_minutes?: number | null;
  transfers?: number | null;
  price?: MoneyV3 | null;
  availability: 'available' | 'limited' | 'unknown';
  seat_options: TransportSeatOptionV3[];
  booking_url?: string | null;
  source_status: SourceStatus;
  source_refs: string[];
  verified_at?: string | null;
}

export interface TransportSectionV3 {
  direction: 'outbound' | 'return';
  scope: 'domestic' | 'international';
  status: SectionStatus;
  status_reason?: string | null;
  selected_option_id?: string | null;
  options: TransportOptionV3[];
  official_query_url?: string | null;
}

export interface LodgingOptionV3 {
  lodging_id: string;
  name: string;
  area?: string | null;
  place?: TripPlaceV3 | null;
  nightly_price?: MoneyV3 | null;
  total_price?: MoneyV3 | null;
  rating?: number | null;
  reasons: string[];
  booking_url?: string | null;
  source_status: SourceStatus;
  source_refs: string[];
}

export interface LodgingPlanV3 {
  status: SectionStatus;
  status_reason?: string | null;
  planning_lodging_id?: string | null;
  options: LodgingOptionV3[];
}

export interface RouteLegV3 {
  from_id: string;
  to_id: string;
  mode: string;
  distance_meters?: number | null;
  duration_minutes?: number | null;
  estimated_cost?: MoneyV3 | null;
  provider?: string | null;
  coordinate_system?: 'WGS84' | 'BD09LL' | null;
  geometry?: Record<string, unknown> | null;
  calculated_at?: string | null;
  status: 'ready' | 'unavailable';
}

export interface ReservationInfoV3 {
  required: boolean;
  status: 'not_required' | 'needs_confirmation' | 'recommended';
  guidance?: string | null;
  source_ref?: string | null;
}

export interface TripActivityV3 {
  activity_id: string;
  kind: ActivityKind;
  title: string;
  day?: number | null;
  order?: number | null;
  start_at?: string | null;
  end_at?: string | null;
  duration_minutes?: number | null;
  fixed_time: boolean;
  meal_type?: 'breakfast' | 'lunch' | 'dinner' | null;
  place?: TripPlaceV3 | null;
  images: ImageAssetV3[];
  cover_image_id?: string | null;
  estimated_cost?: MoneyV3 | null;
  reservation?: ReservationInfoV3 | null;
  note_id?: string | null;
  evidence_refs: string[];
  route_to_next?: RouteLegV3 | null;
}

export interface TripAnchorV3 {
  anchor_id: string;
  kind: 'lodging_departure' | 'lodging_return' | 'arrival_hub' | 'departure_hub';
  day: number;
  place: TripPlaceV3;
  display_label: string;
}

export interface TripDayV3 {
  day: number;
  date: string | null;
  timezone: string;
  theme?: string | null;
  note_id?: string | null;
  activities: TripActivityV3[];
  anchors: TripAnchorV3[];
}

export interface TripActionItemV3 {
  action_id: string;
  kind: 'checklist' | 'reservation_reminder';
  scope: 'trip' | 'day' | 'activity';
  target_id?: string | null;
  text: string;
  status: 'pending' | 'completed' | 'overdue';
  due_at?: string | null;
  timezone?: string | null;
  source_ref?: string | null;
}

export interface TripNoteV3 {
  note_id: string;
  scope: 'trip' | 'day' | 'activity';
  target_id?: string | null;
  content: string;
  updated_at: string;
}

export interface BudgetSummaryV3 {
  total_budget: MoneyV3;
  estimated_total?: MoneyV3 | null;
  categories: Array<{
    category: 'transport' | 'lodging' | 'food' | 'activities' | 'shopping' | 'other';
    amount: MoneyV3;
  }>;
  unknown_cost_count: number;
  over_budget: boolean;
  overrun_amount?: MoneyV3 | null;
  traveler_costs?: Array<{
    traveler_type: 'adult' | 'child' | 'senior';
    count: number;
    estimated_total?: MoneyV3 | null;
    pricing_status: 'standard_price' | 'official_discount_verified' | 'adult_price_assumed' | 'not_applicable';
  }>;
  warnings: string[];
}

export interface MapGuidanceV3 {
  status: SectionStatus;
  status_reason?: string | null;
  formal_location_ids: string[];
  day_routes: Array<{
    day: number;
    status: 'ready' | 'partial' | 'unavailable';
    legs: RouteLegV3[];
  }>;
  candidates_visible_by_default: false;
}

export interface TripSourceV3 {
  source_id: string;
  title: string;
  source_name: string;
  status: SourceStatus;
  url?: string | null;
  updated_at?: string | null;
  related_fields: string[];
  related_place_ids: string[];
}

export interface TravelPlanDocumentV3 {
  schema_version: '3.0';
  plan_id: string;
  revision: number;
  status: DocumentStatus;
  title: string;
  generated_at: string;
  locale: string;
  intent: TripIntentV3;
  destination_overview: {
    name_zh: string;
    name_en?: string | null;
    country_code?: string | null;
    country_name?: string | null;
    timezone: string;
    currency: string;
    summary?: string | null;
    themes: string[];
    cover_image?: ImageAssetV3 | null;
    evidence_refs: string[];
  };
  outbound_transport: TransportSectionV3;
  lodging_plan: LodgingPlanV3;
  itinerary: { days: TripDayV3[] };
  candidate_pool: TripActivityV3[];
  action_items: TripActionItemV3[];
  notes: TripNoteV3[];
  budget: BudgetSummaryV3;
  return_transport: TransportSectionV3;
  map_guidance: MapGuidanceV3;
  sources: TripSourceV3[];
  delivery: {
    markdown_filename: string;
    pdf_filename: string;
    markdown_enabled: boolean;
    pdf_enabled: boolean;
  };
  validation: {
    valid: boolean;
    degraded: boolean;
    issues: Array<{
      code: string;
      message: string;
      severity: 'info' | 'warning' | 'error';
      target_id?: string | null;
    }>;
  };
}

export type PlanningStage =
  | 'requirements_analysis'
  | 'research'
  | 'route_planning'
  | 'realtime_verification'
  | 'validation_completed';

export type PlanningEventType =
  | 'run_started'
  | 'agent_stage_started'
  | 'agent_stage_updated'
  | 'agent_stage_retrying'
  | 'agent_stage_completed'
  | 'provider_queue_waiting'
  | 'run_soft_timeout'
  | 'final_plan_section'
  | 'trip_plan_completed'
  | 'plan_revision_started'
  | 'plan_revision_section'
  | 'plan_revision_completed'
  | 'run_cancelled'
  | 'error';

export interface ProviderQueueWaitingPayload {
  stage: 'realtime_verification';
  provider: string;
  operation: string;
  priority: 'formal' | 'scheduled_dining' | 'candidate' | 'supplemental';
  queue_position: number;
  message: '地点核验排队中';
}

export interface PlanningEventEnvelope<TPayload = Record<string, unknown>> {
  event_version: 1;
  event_id: string;
  run_id: string;
  request_id: string;
  sequence: number;
  occurred_at: string;
  type: PlanningEventType;
  payload: TPayload;
}
