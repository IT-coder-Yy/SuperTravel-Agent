export interface TripHistorySummaryPayload {
  id: string;
  title: string;
  status: string;
  preview: string;
  createdAt: string;
  updatedAt: string;
  contentUpdatedAt: string;
  currentRevision: number;
}

export interface TripHistoryPagePayload {
  items: TripHistorySummaryPayload[];
  page: number;
  page_size: number;
  total: number;
  has_more: boolean;
}

export interface TripDetailPayload extends TripHistorySummaryPayload {
  messages: Array<Record<string, unknown>>;
  tripPlan?: Record<string, unknown> | null;
  tripDocument?: Record<string, unknown> | null;
  tripWorkspace?: Record<string, unknown> | null;
  formalSnapshots?: Record<string, unknown>;
  draft?: Record<string, unknown> | null;
}

export interface TripUpsertPayload {
  title: string;
  messages: unknown[];
  change_reason: string;
  trip_plan?: Record<string, unknown> | null;
  trip_document?: Record<string, unknown> | null;
  trip_workspace?: Record<string, unknown> | null;
}

let deviceBootstrapPromise: Promise<void> | null = null;
let legacyMigrationPromise: Promise<void> | null = null;

const responseError = async (response: Response): Promise<Error> => {
  try {
    const body = await response.json() as { detail?: string | { message?: string }; message?: string };
    if (typeof body.detail === 'string') return new Error(body.detail);
    if (body.detail?.message) return new Error(body.detail.message);
    if (body.message) return new Error(body.message);
  } catch (_error) {
    // Fall through to the stable HTTP message.
  }
  return new Error(`HTTP ${response.status}: ${response.statusText}`);
};

const requestJson = async <T>(url: string, init?: RequestInit): Promise<T> => {
  const response = await fetch(url, { credentials: 'same-origin', ...init });
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<T>;
};

const bootstrapDevice = (): Promise<void> => {
  if (!deviceBootstrapPromise) {
    deviceBootstrapPromise = requestJson<{ status: string }>('/api/device').then(() => undefined);
  }
  return deviceBootstrapPromise;
};

export const tripHistoryApi = {
  ensureDevice: bootstrapDevice,

  migrateLegacy: async (items: unknown[]): Promise<{ imported: number; skipped: number }> => {
    await bootstrapDevice();
    return requestJson('/api/trips/migrate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items }),
    });
  },

  list: async (page = 1): Promise<TripHistoryPagePayload> => {
    await bootstrapDevice();
    return requestJson(`/api/trips?page=${page}&page_size=20`);
  },

  get: async (tripId: string): Promise<TripDetailPayload> => {
    await bootstrapDevice();
    return requestJson(`/api/trips/${encodeURIComponent(tripId)}`);
  },

  upsert: async (tripId: string, payload: TripUpsertPayload): Promise<TripDetailPayload> => {
    await bootstrapDevice();
    return requestJson(`/api/trips/${encodeURIComponent(tripId)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  },

  delete: async (tripId: string): Promise<void> => {
    await bootstrapDevice();
    await requestJson(`/api/trips/${encodeURIComponent(tripId)}`, { method: 'DELETE' });
  },

  deleteAll: async (): Promise<void> => {
    await bootstrapDevice();
    await requestJson('/api/trips', { method: 'DELETE' });
  },
};

export const ensureLegacyHistoryMigration = (
  readLegacyItems: () => unknown[],
  onMigrated: () => void,
): Promise<void> => {
  if (!legacyMigrationPromise) {
    legacyMigrationPromise = (async () => {
      await bootstrapDevice();
      const items = readLegacyItems();
      if (items.length > 0) await tripHistoryApi.migrateLegacy(items);
      onMigrated();
    })().catch((error) => {
      legacyMigrationPromise = null;
      throw error;
    });
  }
  return legacyMigrationPromise;
};

export const resetTripHistoryApiForTests = (): void => {
  deviceBootstrapPromise = null;
  legacyMigrationPromise = null;
};
