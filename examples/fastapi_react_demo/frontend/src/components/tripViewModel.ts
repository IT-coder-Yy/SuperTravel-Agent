export type TripDataType =
  | 'confirmed_live_data'
  | 'reference_data'
  | 'estimated_data'
  | string;

export type TripSource = string | Record<string, unknown>;

export interface RouteLeg {
  mode: string;
  distance_meters: number | null;
  duration_minutes: number | null;
  estimated_cost: number | null;
  source: TripSource | null;
  sources: TripSource[];
  data_type: TripDataType | null;
  calculated_at: string | null;
}

export interface TripActivityPlace {
  name: string;
  category?: string;
  lat?: number | null;
  lng?: number | null;
  source?: TripSource | null;
  data_type?: TripDataType | null;
  [key: string]: unknown;
}

export interface TripActivity {
  id: string;
  day: number;
  start_time: string | null;
  end_time: string | null;
  duration_minutes?: number | null;
  fixed_time?: boolean;
  title: string;
  place: TripActivityPlace | null;
  images?: unknown[];
  estimated_cost: number | null;
  notes: string[];
  data_type: TripDataType | null;
  source: TripSource | null;
  sources: TripSource[];
  evidence_refs: TripSource[];
  route_to_next: RouteLeg | null;
}

export interface TripDay {
  id: string;
  day: number;
  date: string | null;
  theme: string | null;
  revision: number;
  estimated_cost: number | null;
  warnings: string[];
  data_type: TripDataType | null;
  source: TripSource | null;
  sources: TripSource[];
  activities: TripActivity[];
}

export interface TripPlanViewModel {
  days: TripDay[];
}

type UnknownRecord = Record<string, unknown>;

const isRecord = (value: unknown): value is UnknownRecord => (
  typeof value === 'object' && value !== null && !Array.isArray(value)
);

const asString = (value: unknown): string | null => {
  if (typeof value !== 'string') return null;
  const normalized = value.trim();
  return normalized || null;
};

const asNumber = (value: unknown): number | null => {
  if (value === null || value === undefined || value === '') return null;
  const normalized = Number(value);
  return Number.isFinite(normalized) ? normalized : null;
};

const asPositiveInteger = (value: unknown, fallback: number): number => {
  const normalized = asNumber(value);
  return normalized !== null && normalized >= 1
    ? Math.floor(normalized)
    : fallback;
};

const asRevision = (value: unknown): number => {
  const normalized = asNumber(value);
  return normalized !== null && normalized >= 0
    ? Math.floor(normalized)
    : 0;
};

const asStringArray = (value: unknown): string[] => (
  Array.isArray(value)
    ? value.map(asString).filter((item): item is string => item !== null)
    : []
);

const asSources = (value: unknown): TripSource[] => {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is TripSource => (
    typeof item === 'string' || isRecord(item)
  ));
};

const asSource = (value: unknown): TripSource | null => (
  typeof value === 'string' || isRecord(value) ? value : null
);

const asDataType = (value: unknown): TripDataType | null => asString(value);

const stableHash = (value: string): string => {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
};

const explicitId = (value: UnknownRecord, keys: string[]): string | null => {
  for (const key of keys) {
    const id = asString(value[key]);
    if (id) return id;
  }
  return null;
};

const adaptPlace = (value: unknown): TripActivityPlace | null => {
  if (!isRecord(value)) return null;
  const name = asString(value.name) ?? '';
  return {
    ...value,
    name,
    category: asString(value.category) ?? undefined,
    lat: asNumber(value.lat),
    lng: asNumber(value.lng),
    source: asSource(value.source),
    data_type: asDataType(value.data_type),
  };
};

const adaptRouteLeg = (
  value: unknown,
  fallbackDataType: TripDataType | null,
): RouteLeg | null => {
  const legacyMode = asString(value);
  if (legacyMode) {
    return {
      mode: legacyMode,
      distance_meters: null,
      duration_minutes: null,
      estimated_cost: null,
      source: null,
      sources: [],
      data_type: fallbackDataType,
      calculated_at: null,
    };
  }
  if (!isRecord(value)) return null;

  return {
    mode: asString(value.mode) ?? asString(value.transport) ?? '',
    distance_meters: asNumber(value.distance_meters),
    duration_minutes: asNumber(value.duration_minutes),
    estimated_cost: asNumber(value.estimated_cost),
    source: asSource(value.source),
    sources: asSources(value.sources ?? value.source_references),
    data_type: asDataType(value.data_type) ?? fallbackDataType,
    calculated_at: asString(value.calculated_at),
  };
};

const activityIdentity = (value: UnknownRecord, day: number): string => {
  const place = isRecord(value.place) ? asString(value.place.name) : null;
  return [
    day,
    asString(value.start_time) ?? '',
    asString(value.end_time) ?? '',
    asString(value.title) ?? '',
    place ?? '',
  ].join('|').toLocaleLowerCase();
};

const adaptActivities = (values: unknown[], day: number): TripActivity[] => {
  const identityOccurrences = new Map<string, number>();
  return values.flatMap((rawActivity) => {
    if (!isRecord(rawActivity)) return [];
    const activityDay = asPositiveInteger(rawActivity.day, day);
    const identity = activityIdentity(rawActivity, activityDay);
    const occurrence = (identityOccurrences.get(identity) ?? 0) + 1;
    identityOccurrences.set(identity, occurrence);
    const dataType = asDataType(rawActivity.data_type);
    const routeValue = rawActivity.route_to_next ?? rawActivity.transport_to_next;

    return [{
      id: explicitId(rawActivity, ['id', 'activity_id'])
        ?? `activity-${stableHash(`${identity}|${occurrence}`)}`,
      day: activityDay,
      start_time: asString(rawActivity.start_time),
      end_time: asString(rawActivity.end_time),
      duration_minutes: asNumber(rawActivity.duration_minutes),
      fixed_time: rawActivity.fixed_time === true,
      title: asString(rawActivity.title) ?? '',
      place: adaptPlace(rawActivity.place),
      images: Array.isArray(rawActivity.images) ? rawActivity.images : [],
      estimated_cost: asNumber(rawActivity.estimated_cost),
      notes: asStringArray(rawActivity.notes),
      data_type: dataType,
      source: asSource(rawActivity.source),
      sources: asSources(rawActivity.sources ?? rawActivity.source_references),
      evidence_refs: asSources(rawActivity.evidence_refs),
      route_to_next: adaptRouteLeg(routeValue, dataType),
    }];
  });
};

export const adaptTripDay = (value: unknown, fallbackDay = 1): TripDay | null => {
  if (!isRecord(value)) return null;
  const day = asPositiveInteger(value.day, fallbackDay);
  const activities = Array.isArray(value.activities) ? value.activities : [];
  const date = asString(value.date);
  const dayIdentity = date ? `date:${date}` : `day:${day}`;

  return {
    id: explicitId(value, ['id', 'day_id']) ?? `trip-day-${stableHash(dayIdentity)}`,
    day,
    date,
    theme: asString(value.theme),
    revision: asRevision(value.revision),
    estimated_cost: asNumber(value.estimated_cost ?? value.budget_subtotal),
    warnings: asStringArray(value.warnings),
    data_type: asDataType(value.data_type),
    source: asSource(value.source),
    sources: asSources(value.sources ?? value.source_references),
    activities: adaptActivities(activities, day).map((activity) => ({
      ...activity,
      day,
    })),
  };
};

const adaptFlatActivities = (activities: unknown[]): TripDay[] => {
  const grouped = new Map<number, unknown[]>();
  activities.forEach((activity) => {
    if (!isRecord(activity)) return;
    const day = asPositiveInteger(activity.day, 1);
    grouped.set(day, [...(grouped.get(day) ?? []), activity]);
  });

  return [...grouped.entries()]
    .sort(([left], [right]) => left - right)
    .map(([day, dayActivities]) => adaptTripDay({ day, activities: dayActivities }, day))
    .filter((item): item is TripDay => item !== null);
};

export const adaptTripPlanToViewModel = (value: unknown): TripPlanViewModel => {
  if (!isRecord(value)) return { days: [] };
  const structuredDays = Array.isArray(value.days)
    ? value.days
    : Array.isArray(value.trip_days)
      ? value.trip_days
      : [];
  const days = structuredDays.length > 0
    ? structuredDays
      .map((day, index) => adaptTripDay(day, index + 1))
      .filter((item): item is TripDay => item !== null)
    : adaptFlatActivities(Array.isArray(value.activities) ? value.activities : []);

  return { days: [...days].sort((left, right) => left.day - right.day) };
};

export const normalizeTripDays = (value: unknown): TripDay[] => (
  adaptTripPlanToViewModel(value).days
);

export const upsertTripDay = (currentDays: TripDay[], incomingDay: TripDay): TripDay[] => {
  const existingIndex = currentDays.findIndex((day) => day.day === incomingDay.day);
  if (existingIndex < 0) {
    return [...currentDays, incomingDay].sort((left, right) => left.day - right.day);
  }
  if (currentDays[existingIndex].revision > incomingDay.revision) return currentDays;

  const nextDays = [...currentDays];
  nextDays[existingIndex] = incomingDay;
  return nextDays.sort((left, right) => left.day - right.day);
};

export const upsertTripDayPayload = (currentDays: TripDay[], value: unknown): TripDay[] => {
  const incomingDay = adaptTripDay(value);
  return incomingDay ? upsertTripDay(currentDays, incomingDay) : currentDays;
};

export const upsertTripDays = (currentDays: TripDay[], incomingDays: TripDay[]): TripDay[] => (
  incomingDays.reduce(upsertTripDay, currentDays)
);
