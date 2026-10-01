import type { TripWorkspaceState } from '../../../components/TripWorkspace';
import type { RouteLeg, TripActivity, TripDay } from '../../../components/tripViewModel';
import type {
  MoneyV3,
  TravelPlanDocumentV3,
  TripActivityV3,
  TripPlaceV3,
} from './travelPlannerTypes';

export interface RestoredFormalTravelState {
  document: Record<string, unknown>;
  plan: Record<string, unknown>;
  workspace: TripWorkspaceState;
}

const isRecord = (value: unknown): value is Record<string, unknown> => (
  typeof value === 'object' && value !== null && !Array.isArray(value)
);

const moneyAmount = (value: MoneyV3 | null | undefined): number | null => (
  value && value.currency === 'CNY' && Number.isFinite(value.amount) ? value.amount
    : value && typeof value.cny_reference_amount === 'number' ? value.cny_reference_amount : null
);

const displayTime = (value: string | null | undefined): string | null => {
  if (!value) return null;
  const match = value.match(/(?:T|^)(\d{2}:\d{2})/);
  return match?.[1] || value;
};

const adaptPlace = (place: TripPlaceV3 | null | undefined) => (
  place && place.coordinates
    ? {
        id: place.poi_id,
        name: place.name,
        category: place.category,
        lat: place.coordinates.latitude,
        lng: place.coordinates.longitude,
        address: place.address || undefined,
        summary: place.summary || undefined,
        review_summary: place.review_summary ? { ...place.review_summary } : null,
        data_type: 'map_reference',
      }
    : null
);

const locationSources = (document: TravelPlanDocumentV3, sourceRefs: string[]) => {
  const sourceIds = new Set(sourceRefs.filter(Boolean));
  return document.sources
    .filter((source) => sourceIds.has(source.source_id))
    .map((source) => ({
      source_reference_id: source.source_id,
      title: source.title,
      source: source.source_name,
      url: source.url || undefined,
      updated_at: source.updated_at || undefined,
    }));
};

const adaptRoute = (activity: TripActivityV3): RouteLeg | null => {
  const route = activity.route_to_next;
  if (!route) return null;
  return {
    mode: route.mode,
    distance_meters: route.distance_meters ?? null,
    duration_minutes: route.duration_minutes ?? null,
    estimated_cost: moneyAmount(route.estimated_cost),
    source: route.provider || null,
    sources: route.provider ? [route.provider] : [],
    data_type: route.status === 'ready' ? 'confirmed_live_data' : 'reference_data',
    calculated_at: route.calculated_at ?? null,
  };
};

const adaptActivity = (
  activity: TripActivityV3,
  day: number,
  notes: Map<string, string>,
): TripActivity => ({
  id: activity.activity_id,
  day,
  start_time: displayTime(activity.start_at),
  end_time: displayTime(activity.end_at),
  duration_minutes: activity.duration_minutes ?? null,
  fixed_time: activity.fixed_time,
  title: activity.title,
  place: adaptPlace(activity.place),
  images: Array.isArray(activity.images) ? [...activity.images] : [],
  estimated_cost: moneyAmount(activity.estimated_cost),
  notes: activity.note_id && notes.has(activity.note_id) ? [notes.get(activity.note_id)!] : [],
  data_type: activity.evidence_refs.length > 0 ? 'reference_data' : null,
  source: activity.evidence_refs[0] || null,
  sources: [...activity.evidence_refs],
  evidence_refs: [...activity.evidence_refs],
  route_to_next: adaptRoute(activity),
});

const adaptDays = (document: TravelPlanDocumentV3): TripDay[] => {
  const notes = new Map(document.notes.map((note) => [note.note_id, note.content]));
  return document.itinerary.days.map((day) => {
    const activities = day.activities.map((activity) => adaptActivity(activity, day.day, notes));
    const knownCosts = activities
      .map((activity) => activity.estimated_cost)
      .filter((value): value is number => value !== null);
    return {
      id: `trip-day-${day.day}-${day.date || 'flexible'}`,
      day: day.day,
      date: day.date,
      theme: day.theme ?? null,
      revision: document.revision,
      estimated_cost: knownCosts.length > 0
        ? knownCosts.reduce((total, amount) => total + amount, 0)
        : null,
      warnings: [],
      data_type: document.status === 'formal' ? 'confirmed_live_data' : 'reference_data',
      source: null,
      sources: [],
      activities,
    };
  });
};

const adaptLocations = (document: TravelPlanDocumentV3) => {
  const formalIds = new Set(document.map_guidance.formal_location_ids);
  const includeAll = formalIds.size === 0;
  const locations = document.itinerary.days.flatMap((day) => {
    const activities = day.activities.flatMap((activity) => {
      if (!activity.place?.coordinates) return [];
      if (!includeAll && !formalIds.has(activity.activity_id) && !formalIds.has(activity.place.poi_id)) return [];
      const sourceRefs = [
        ...activity.evidence_refs,
        ...activity.place.evidence_refs,
        ...(activity.place.review_summary?.source_refs || []),
      ];
      return [{
        id: activity.place.poi_id,
        poi_id: activity.place.poi_id,
        name: activity.place.name,
        lat: activity.place.coordinates.latitude,
        lng: activity.place.coordinates.longitude,
        description: activity.place.summary || activity.place.address || undefined,
        summary: activity.place.summary || undefined,
        category: activity.place.category,
        review_summary: activity.place.review_summary ? { ...activity.place.review_summary } : null,
        image_assets: Array.isArray(activity.images) ? [...activity.images] : [],
        sources: locationSources(document, sourceRefs),
        day: day.day,
        order: activity.order ?? undefined,
      }];
    });
    const anchors = day.anchors.flatMap((anchor) => {
      if (!anchor.place.coordinates || (!includeAll && !formalIds.has(anchor.anchor_id) && !formalIds.has(anchor.place.poi_id))) return [];
      const sourceRefs = [
        ...anchor.place.evidence_refs,
        ...(anchor.place.review_summary?.source_refs || []),
      ];
      return [{
        id: anchor.anchor_id,
        poi_id: anchor.place.poi_id,
        name: anchor.place.name,
        lat: anchor.place.coordinates.latitude,
        lng: anchor.place.coordinates.longitude,
        description: anchor.display_label,
        summary: anchor.place.summary || undefined,
        category: anchor.place.category,
        anchor_kind: anchor.kind,
        anchor_label: anchor.display_label,
        order: anchor.kind === 'arrival_hub' || anchor.kind === 'lodging_departure' ? 0 : 9999,
        review_summary: anchor.place.review_summary ? { ...anchor.place.review_summary } : null,
        sources: locationSources(document, sourceRefs),
        day: day.day,
      }];
    });
    return [...activities, ...anchors];
  });
  return Array.from(new Map(locations.map((location) => [location.id, location])).values());
};

const isFormalDocument = (value: unknown): value is TravelPlanDocumentV3 => (
  isRecord(value)
  && value.schema_version === '3.0'
  && typeof value.plan_id === 'string'
  && Array.isArray((value.itinerary as Record<string, unknown> | undefined)?.days)
);

export const restoreFormalSnapshotState = (
  formalSnapshots: Record<string, unknown> | undefined,
): RestoredFormalTravelState | null => {
  const current = isRecord(formalSnapshots?.current) ? formalSnapshots.current : null;
  const document = current?.document;
  if (!isFormalDocument(document)) return null;

  const days = adaptDays(document);
  const locations = adaptLocations(document);
  const sources = document.sources.map((source) => ({
    type: source.status,
    title: source.title,
    source: source.source_name,
    url: source.url || undefined,
    data_type: source.status,
    updated_at: source.updated_at || undefined,
    related_fields: source.related_fields,
    related_places: source.related_place_ids,
  }));
  const categories = Object.fromEntries(
    document.budget.categories.map((category) => [category.category, category.amount.amount]),
  );
  const peopleCount = Object.values(document.intent.travelers)
    .reduce((total, count) => total + count, 0);
  const validation = {
    valid: document.validation.valid,
    issues: document.validation.issues.map((issue) => ({ ...issue })),
  };
  const budget = {
    currency: document.budget.total_budget.currency,
    budget_total: document.budget.total_budget.amount,
    people_count: peopleCount,
    estimated_total: moneyAmount(document.budget.estimated_total),
    known_total: moneyAmount(document.budget.estimated_total),
    unknown_count: document.budget.unknown_cost_count,
    categories,
    currency_breakdown: document.budget.categories.map((category) => ({
      category: category.category,
      amount: { ...category.amount },
    })),
    traveler_costs: (document.budget.traveler_costs || []).map((item) => ({
      traveler_type: item.traveler_type,
      count: item.count,
      estimated_total: item.estimated_total ? { ...item.estimated_total } : null,
      pricing_status: item.pricing_status,
    })),
    over_budget: document.budget.over_budget,
    overrun_amount: moneyAmount(document.budget.overrun_amount),
    data_type: document.budget.total_budget.source_status,
    source_label: '正式方案快照',
  };
  const plan = {
    plan_id: document.plan_id,
    version: document.revision,
    title: document.title,
    intent: document.intent,
    trip_days: days,
    activities: days.flatMap((day) => day.activities),
    map_locations: locations,
    budget_summary: budget,
    source_references: sources,
    warnings: document.validation.issues.map((issue) => issue.message),
  };

  return {
    document: document as unknown as Record<string, unknown>,
    plan,
    workspace: {
      days,
      locations,
      sources,
      budget,
      validation,
      repair: null,
    },
  };
};
