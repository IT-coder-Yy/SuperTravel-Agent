import { restoreFormalSnapshotState, type RestoredFormalTravelState } from './formalSnapshotRestore';
import type { TravelPlanDocumentV3 } from './travelPlannerTypes';

export interface TripEditDraftOperation {
  operation_id: string;
  type: string;
  payload: Record<string, unknown>;
  base_version: number;
}

export interface TripEditDraftPayload {
  document: Record<string, unknown>;
  operations: TripEditDraftOperation[];
  updatedAt?: string;
}

export interface RestoredTripEditDraft extends RestoredFormalTravelState {
  document: Record<string, unknown>;
  operations: TripEditDraftOperation[];
}

const DRAFT_MIRROR_STORAGE_KEY = 'sage_trip_edit_draft_mirror_v1';

const asRecord = (value: unknown): Record<string, unknown> | null => (
  typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
);

const isV3Document = (value: unknown): value is TravelPlanDocumentV3 => {
  const document = asRecord(value);
  return document?.schema_version === '3.0'
    && typeof document.plan_id === 'string'
    && document.plan_id.trim().length > 0
    && Number.isInteger(Number(document.revision));
};

/** 历史切换时优先使用页面正在展示的正式文档，避免 effect 中的 ref 短暂滞后。 */
export const selectTripEditDocument = (
  activeDocument: unknown,
  referencedDocument: unknown,
  formalDocument: unknown,
): Record<string, unknown> | null => {
  for (const candidate of [activeDocument, referencedDocument, formalDocument]) {
    if (isV3Document(candidate)) return candidate as unknown as Record<string, unknown>;
  }
  return null;
};

const normalizeOperations = (value: unknown): TripEditDraftOperation[] => {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    const operation = asRecord(item);
    const payload = asRecord(operation?.payload);
    const operationId = typeof operation?.operation_id === 'string' ? operation.operation_id.trim() : '';
    const type = typeof operation?.type === 'string' ? operation.type.trim() : '';
    const baseVersion = Number(operation?.base_version);
    if (!operationId || !type || !payload || !Number.isInteger(baseVersion)) return [];
    return [{ operation_id: operationId, type, payload, base_version: baseVersion }];
  });
};

const normalizePayload = (value: unknown): TripEditDraftPayload | null => {
  const payload = asRecord(value);
  const document = asRecord(payload?.document);
  if (!document || !isV3Document(document)) return null;
  return {
    document,
    operations: normalizeOperations(payload?.operations),
    updatedAt: typeof payload?.updatedAt === 'string' ? payload.updatedAt : undefined,
  };
};

export const createTripEditDraft = (
  document: Record<string, unknown>,
  operations: TripEditDraftOperation[],
): TripEditDraftPayload => ({ document, operations: normalizeOperations(operations) });

/** 草稿只能建立在同一份正式 V3 方案之上，且必须比正式版本更新。 */
export const restoreTripEditDraft = (
  value: unknown,
  formalDocument: Record<string, unknown> | null,
): RestoredTripEditDraft | null => {
  const draft = normalizePayload(value);
  if (!draft) return null;
  const formal = isV3Document(formalDocument) ? formalDocument : null;
  if (formal && (
    draft.document.plan_id !== formal.plan_id
    || Number(draft.document.revision) <= Number(formal.revision)
  )) return null;

  const restored = restoreFormalSnapshotState({ current: { document: draft.document } });
  if (!restored) return null;
  return {
    ...restored,
    document: restored.document,
    operations: draft.operations,
  };
};

export const readTripEditDraftMirror = (tripId: string): TripEditDraftPayload | null => {
  if (!tripId || typeof window === 'undefined') return null;
  try {
    const stored = asRecord(JSON.parse(window.localStorage.getItem(DRAFT_MIRROR_STORAGE_KEY) || 'null'));
    if (stored?.tripId !== tripId) return null;
    return normalizePayload(stored.draft);
  } catch (_error) {
    return null;
  }
};

export const writeTripEditDraftMirror = (tripId: string, draft: TripEditDraftPayload): void => {
  if (!tripId || typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(DRAFT_MIRROR_STORAGE_KEY, JSON.stringify({ tripId, draft }));
  } catch (_error) {
    // 本地镜像不可用时仍以服务端草稿为准，不影响本次编辑。
  }
};

export const removeTripEditDraftMirror = (tripId: string): void => {
  if (!tripId || typeof window === 'undefined') return;
  try {
    const stored = asRecord(JSON.parse(window.localStorage.getItem(DRAFT_MIRROR_STORAGE_KEY) || 'null'));
    if (stored?.tripId === tripId) window.localStorage.removeItem(DRAFT_MIRROR_STORAGE_KEY);
  } catch (_error) {
    window.localStorage.removeItem(DRAFT_MIRROR_STORAGE_KEY);
  }
};
