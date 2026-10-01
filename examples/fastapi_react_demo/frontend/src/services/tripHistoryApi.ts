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
  agentTimeline?: Record<string, unknown> | null;
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
  agent_timeline?: Record<string, unknown> | null;
}

export interface TripDraftPayload {
  document: Record<string, unknown>;
  operations: Array<{
    operation_id: string;
    type: string;
    payload: Record<string, unknown>;
    base_version: number;
  }>;
}

export interface ApplyDraftPayload {
  operation_id: string;
  expected_draft_revision: number;
}

export interface FormalSnapshotResult {
  operation_id: string;
  trip_id: string;
  revision: number;
  checksum: string;
  status: 'applied' | 'restored';
  document: Record<string, unknown>;
  idempotent_replay: boolean;
}

let deviceBootstrapPromise: Promise<void> | null = null;
let legacyMigrationPromise: Promise<void> | null = null;
const draftSaveQueues = new Map<string, Promise<unknown>>();

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

const enqueueDraftRequest = <T,>(tripId: string, request: () => Promise<T>): Promise<T> => {
  const previous = draftSaveQueues.get(tripId) || Promise.resolve();
  const queued = previous
    .catch(() => undefined)
    .then(request)
    .finally(() => {
      if (draftSaveQueues.get(tripId) === queued) draftSaveQueues.delete(tripId);
    });
  draftSaveQueues.set(tripId, queued);
  return queued;
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

  saveDraft: (tripId: string, payload: TripDraftPayload): Promise<{ trip_id: string; document: Record<string, unknown>; updated_at: string }> => {
    return enqueueDraftRequest(tripId, async () => {
      await bootstrapDevice();
      return requestJson<{ trip_id: string; document: Record<string, unknown>; updated_at: string }>(
        `/api/trips/${encodeURIComponent(tripId)}/draft`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        },
      );
    });
  },

  discardDraft: (tripId: string): Promise<{ discarded: boolean }> => {
    return enqueueDraftRequest(tripId, async () => {
      await bootstrapDevice();
      return requestJson<{ discarded: boolean }>(`/api/trips/${encodeURIComponent(tripId)}/draft`, {
        method: 'DELETE',
      });
    });
  },

  applyDraft: (tripId: string, payload: ApplyDraftPayload): Promise<FormalSnapshotResult> => {
    return enqueueDraftRequest(tripId, async () => {
      await bootstrapDevice();
      return requestJson<FormalSnapshotResult>(`/api/trips/${encodeURIComponent(tripId)}/draft/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    });
  },

  restorePrevious: (tripId: string, payload: { operation_id: string; expected_current_revision: number }): Promise<FormalSnapshotResult> => {
    return enqueueDraftRequest(tripId, async () => {
      await bootstrapDevice();
      return requestJson<FormalSnapshotResult>(`/api/trips/${encodeURIComponent(tripId)}/restore`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    });
  },

  streamFormalRevision: async (
    tripId: string,
    operationId: string,
    afterSequence = 0,
    signal?: AbortSignal,
  ): Promise<Response> => {
    await bootstrapDevice();
    const response = await fetch(
      `/api/trips/${encodeURIComponent(tripId)}/revisions/${encodeURIComponent(operationId)}/events?after_sequence=${Math.max(0, afterSequence)}`,
      { credentials: 'same-origin', signal },
    );
    if (!response.ok) throw await responseError(response);
    return response;
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
  draftSaveQueues.clear();
};
