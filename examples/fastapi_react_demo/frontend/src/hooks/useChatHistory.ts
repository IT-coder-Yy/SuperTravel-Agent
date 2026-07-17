import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ensureLegacyHistoryMigration,
  tripHistoryApi,
  TripDetailPayload,
  TripHistorySummaryPayload,
  TripUpsertPayload,
} from '../services/tripHistoryApi';

export interface ChatHistoryMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  displayContent: string;
  timestamp: Date;
  type?: string;
  agentType?: string;
}

export interface ChatHistoryItem {
  id: string;
  title: string;
  messages: ChatHistoryMessage[];
  createdAt: Date;
  updatedAt: Date;
  contentUpdatedAt: Date;
  status?: string;
  preview?: string;
  currentRevision?: number;
  tripPlan?: Record<string, unknown> | null;
  tripDocument?: Record<string, unknown> | null;
  tripWorkspace?: Record<string, unknown> | null;
}

export interface SaveChatOptions {
  changeReason?: 'user_message' | 'final_answer' | 'trip_edit' | 'import' | 'checklist' | 'note' | 'rename' | 'system';
  touchUpdatedAt?: boolean;
  tripPlan?: Record<string, unknown> | null;
  tripDocument?: Record<string, unknown> | null;
  tripWorkspace?: Record<string, unknown> | null;
}

const LEGACY_STORAGE_KEY = 'sage_chat_history';
const MIRROR_STORAGE_KEY = 'sage_trip_history_mirror_v1';
const MIGRATION_MARKER_KEY = 'sage_trip_history_migrated_v1';
const HISTORY_UPDATED_EVENT = 'sage_trip_history_updated';
const saveQueues = new Map<string, Promise<unknown>>();

const normalizeMessage = (message: Record<string, unknown>): ChatHistoryMessage => ({
  ...message,
  id: String(message.id || ''),
  role: message.role === 'user' || message.role === 'system' ? message.role : 'assistant',
  content: typeof message.content === 'string' ? message.content : '',
  displayContent: typeof message.displayContent === 'string'
    ? message.displayContent
    : (typeof message.content === 'string' ? message.content : ''),
  timestamp: new Date(String(message.timestamp || new Date().toISOString())),
});

const normalizeSummary = (item: TripHistorySummaryPayload): ChatHistoryItem => ({
  id: item.id,
  title: item.title,
  messages: [],
  createdAt: new Date(item.createdAt),
  updatedAt: new Date(item.updatedAt),
  contentUpdatedAt: new Date(item.contentUpdatedAt),
  status: item.status,
  preview: item.preview,
  currentRevision: item.currentRevision,
});

const normalizeDetail = (item: TripDetailPayload): ChatHistoryItem => ({
  ...normalizeSummary({
    id: item.id,
    title: item.title,
    status: item.status,
    preview: item.preview || '',
    createdAt: item.createdAt,
    updatedAt: item.updatedAt,
    contentUpdatedAt: item.contentUpdatedAt,
    currentRevision: item.currentRevision,
  }),
  messages: (item.messages || []).map(normalizeMessage),
  tripPlan: item.tripPlan,
  tripDocument: item.tripDocument,
  tripWorkspace: item.tripWorkspace,
});

const readLegacyItems = (): unknown[] => {
  if (localStorage.getItem(MIGRATION_MARKER_KEY) === '1') return [];
  try {
    const raw = localStorage.getItem(LEGACY_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (_error) {
    return [];
  }
};

const markMigrationComplete = (): void => {
  localStorage.setItem(MIGRATION_MARKER_KEY, '1');
  localStorage.removeItem(LEGACY_STORAGE_KEY);
};

const readMirror = (): ChatHistoryItem[] => {
  try {
    const parsed = JSON.parse(localStorage.getItem(MIRROR_STORAGE_KEY) || '[]');
    if (!Array.isArray(parsed)) return [];
    return parsed.map((item) => normalizeSummary(item));
  } catch (_error) {
    return [];
  }
};

const writeMirror = (items: ChatHistoryItem[]): void => {
  const mirror = items.map((item) => ({
    id: item.id,
    title: item.title,
    status: item.status || 'draft',
    preview: item.preview || '',
    createdAt: item.createdAt.toISOString(),
    updatedAt: item.updatedAt.toISOString(),
    contentUpdatedAt: item.contentUpdatedAt.toISOString(),
    currentRevision: item.currentRevision || 0,
  }));
  localStorage.setItem(MIRROR_STORAGE_KEY, JSON.stringify(mirror));
};

const notifyHistoryUpdated = (): void => {
  window.dispatchEvent(new Event(HISTORY_UPDATED_EVENT));
};

const generateTitle = (messages: ChatHistoryMessage[]): string => {
  const firstUserMessage = messages.find((message) => message.role === 'user');
  if (!firstUserMessage) return '新旅程';
  const content = firstUserMessage.content || firstUserMessage.displayContent;
  return content.length > 20 ? `${content.substring(0, 20)}...` : content;
};

export const useChatHistory = () => {
  const [history, setHistory] = useState<ChatHistoryItem[]>(() => readMirror());
  const [hasMore, setHasMore] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const pageRef = useRef(1);

  const loadHistory = useCallback(async (page = 1, append = false) => {
    try {
      await ensureLegacyHistoryMigration(readLegacyItems, markMigrationComplete);
      const payload = await tripHistoryApi.list(page);
      const next = payload.items.map(normalizeSummary);
      setHistory((current) => {
        const merged = append
          ? [...current, ...next.filter((item) => !current.some((existing) => existing.id === item.id))]
          : next;
        writeMirror(merged);
        return merged;
      });
      pageRef.current = page;
      setHasMore(payload.has_more);
    } catch (error) {
      console.error('加载旅程历史失败:', error);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadHistory();
    const onHistoryUpdated = () => void loadHistory();
    const onStorage = (event: StorageEvent) => {
      if (event.key === MIRROR_STORAGE_KEY) setHistory(readMirror());
    };
    window.addEventListener(HISTORY_UPDATED_EVENT, onHistoryUpdated);
    window.addEventListener('storage', onStorage);
    return () => {
      window.removeEventListener(HISTORY_UPDATED_EVENT, onHistoryUpdated);
      window.removeEventListener('storage', onStorage);
    };
  }, [loadHistory]);

  const saveChat = (
    chatId: string,
    messages: ChatHistoryMessage[],
    title?: string,
    options: SaveChatOptions = {},
  ): void => {
    if (!chatId || messages.length === 0) return;
    const payload: TripUpsertPayload = {
      title: title || generateTitle(messages),
      messages,
      change_reason: options.touchUpdatedAt === false
        ? 'system'
        : (options.changeReason || 'user_message'),
    };
    if (options.tripPlan !== undefined) payload.trip_plan = options.tripPlan;
    if (options.tripDocument !== undefined) payload.trip_document = options.tripDocument;
    if (options.tripWorkspace !== undefined) payload.trip_workspace = options.tripWorkspace;

    const previous = saveQueues.get(chatId) || Promise.resolve();
    const queued = previous
      .catch(() => undefined)
      .then(() => tripHistoryApi.upsert(chatId, payload))
      .then(() => notifyHistoryUpdated())
      .catch((error) => console.error('保存旅程失败:', error))
      .finally(() => {
        if (saveQueues.get(chatId) === queued) saveQueues.delete(chatId);
      });
    saveQueues.set(chatId, queued);
  };

  const deleteChat = async (chatId: string): Promise<void> => {
    await tripHistoryApi.delete(chatId);
    setHistory((current) => {
      const next = current.filter((item) => item.id !== chatId);
      writeMirror(next);
      return next;
    });
    notifyHistoryUpdated();
  };

  const clearHistory = async (): Promise<void> => {
    await tripHistoryApi.deleteAll();
    setHistory([]);
    writeMirror([]);
    notifyHistoryUpdated();
  };

  const getChat = async (chatId: string): Promise<ChatHistoryItem | undefined> => {
    try {
      await ensureLegacyHistoryMigration(readLegacyItems, markMigrationComplete);
      return normalizeDetail(await tripHistoryApi.get(chatId));
    } catch (error) {
      console.error('加载旅程详情失败:', error);
      return undefined;
    }
  };

  const loadMore = async (): Promise<void> => {
    if (!hasMore || isLoading) return;
    setIsLoading(true);
    await loadHistory(pageRef.current + 1, true);
  };

  return {
    history,
    hasMore,
    isLoading,
    saveChat,
    deleteChat,
    clearHistory,
    getChat,
    loadMore,
  };
};
