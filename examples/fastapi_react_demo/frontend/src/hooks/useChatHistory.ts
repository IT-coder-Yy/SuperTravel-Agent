import { useState, useEffect } from 'react';

export interface ChatHistoryItem {
  id: string;
  title: string;
  messages: Array<{
    id: string;
    role: 'user' | 'assistant' | 'system';
    content: string;
    displayContent: string;
    timestamp: Date;
    type?: string;
    agentType?: string;
  }>;
  createdAt: Date;
  updatedAt: Date;
  contentUpdatedAt: Date;
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

const STORAGE_KEY = 'sage_chat_history';
const MAX_HISTORY_ITEMS = 50;
const HISTORY_UPDATED_EVENT = 'sage_chat_history_updated';

const normalizeHistory = (parsed: any[]): ChatHistoryItem[] => {
  return parsed.map((item: any) => ({
    ...item,
    createdAt: new Date(item.createdAt),
    updatedAt: new Date(item.updatedAt),
    contentUpdatedAt: new Date(item.contentUpdatedAt || item.updatedAt || item.createdAt),
    messages: (item.messages || []).map((msg: any) => ({
      ...msg,
      content: typeof msg.content === 'string' ? msg.content : '',
      displayContent: typeof msg.displayContent === 'string'
        ? msg.displayContent
        : (typeof msg.content === 'string' ? msg.content : ''),
      timestamp: new Date(msg.timestamp)
    }))
  })).sort((a, b) => b.contentUpdatedAt.getTime() - a.contentUpdatedAt.getTime());
};

const readHistoryFromStorage = (): ChatHistoryItem[] => {
  try {
    const savedHistory = localStorage.getItem(STORAGE_KEY);
    if (!savedHistory) return [];
    const parsed = JSON.parse(savedHistory);
    return normalizeHistory(Array.isArray(parsed) ? parsed : []);
  } catch (error) {
    console.error('加载对话历史失败:', error);
    return [];
  }
};

export const useChatHistory = () => {
  const [history, setHistory] = useState<ChatHistoryItem[]>([]);

  // 从 localStorage 加载历史记录
  useEffect(() => {
    const loadLatest = () => {
      setHistory(readHistoryFromStorage());
    };

    const onStorage = (event: StorageEvent) => {
      if (!event.key || event.key === STORAGE_KEY) {
        loadLatest();
      }
    };

    const onHistoryUpdated = () => {
      loadLatest();
    };

    loadLatest();
    window.addEventListener('storage', onStorage);
    window.addEventListener(HISTORY_UPDATED_EVENT, onHistoryUpdated as EventListener);

    return () => {
      window.removeEventListener('storage', onStorage);
      window.removeEventListener(HISTORY_UPDATED_EVENT, onHistoryUpdated as EventListener);
    };
  }, []);

  // 保存历史记录到 localStorage
  const saveToStorage = (newHistory: ChatHistoryItem[]) => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(newHistory));
      window.dispatchEvent(new Event(HISTORY_UPDATED_EVENT));
    } catch (error) {
      console.error('保存对话历史失败:', error);
    }
  };

  // 生成对话标题（取第一条用户消息的前20个字符）
  const generateTitle = (messages: ChatHistoryItem['messages']): string => {
    const firstUserMessage = messages.find(msg => msg.role === 'user');
    if (firstUserMessage) {
      const content = firstUserMessage.content || firstUserMessage.displayContent;
      return content.length > 20 ? content.substring(0, 20) + '...' : content;
    }
    return '新对话';
  };

  // 添加或更新对话
  const saveChat = (
    chatId: string,
    messages: ChatHistoryItem['messages'],
    title?: string,
    options: SaveChatOptions = {},
  ): void => {
    if (messages.length === 0) return;

    const now = new Date();
    const chatTitle = title || generateTitle(messages);
    const latestHistory = readHistoryFromStorage();
    const existingIndex = latestHistory.findIndex(item => item.id === chatId);
    let newHistory: ChatHistoryItem[];

    if (existingIndex >= 0) {
      const existing = latestHistory[existingIndex];
      const nextTripPlan = options.tripPlan === undefined ? existing.tripPlan : options.tripPlan;
      const nextTripDocument = options.tripDocument === undefined ? existing.tripDocument : options.tripDocument;
      const nextTripWorkspace = options.tripWorkspace === undefined ? existing.tripWorkspace : options.tripWorkspace;
      const contentChanged = JSON.stringify(existing.messages) !== JSON.stringify(messages)
        || JSON.stringify(existing.tripPlan ?? null) !== JSON.stringify(nextTripPlan ?? null)
        || JSON.stringify(existing.tripDocument ?? null) !== JSON.stringify(nextTripDocument ?? null)
        || JSON.stringify(existing.tripWorkspace ?? null) !== JSON.stringify(nextTripWorkspace ?? null)
        || existing.title !== chatTitle;
      const shouldTouch = options.touchUpdatedAt ?? contentChanged;
      // 更新现有对话
      newHistory = [...latestHistory];
      newHistory[existingIndex] = {
        ...existing,
        title: chatTitle,
        messages: [...messages],
        tripPlan: nextTripPlan,
        tripDocument: nextTripDocument,
        tripWorkspace: nextTripWorkspace,
        updatedAt: shouldTouch ? now : existing.updatedAt,
        contentUpdatedAt: shouldTouch ? now : existing.contentUpdatedAt,
      };
    } else {
      // 添加新对话
      const newItem: ChatHistoryItem = {
        id: chatId,
        title: chatTitle,
        messages: [...messages],
        createdAt: now,
        updatedAt: now,
        contentUpdatedAt: now,
        tripPlan: options.tripPlan,
        tripDocument: options.tripDocument,
        tripWorkspace: options.tripWorkspace,
      };
      newHistory = [newItem, ...latestHistory];
    }

    // 限制历史记录数量
    if (newHistory.length > MAX_HISTORY_ITEMS) {
      newHistory = newHistory.slice(0, MAX_HISTORY_ITEMS);
    }

    // 按更新时间排序
    newHistory.sort((a, b) => b.contentUpdatedAt.getTime() - a.contentUpdatedAt.getTime());

    saveToStorage(newHistory);
    setHistory(newHistory);
  };

  // 删除单个对话
  const deleteChat = (chatId: string): void => {
    const latestHistory = readHistoryFromStorage();
    const newHistory = latestHistory.filter(item => item.id !== chatId);
    saveToStorage(newHistory);
    setHistory(newHistory);
  };

  // 清空所有历史记录
  const clearHistory = (): void => {
    setHistory([]);
    localStorage.removeItem(STORAGE_KEY);
    window.dispatchEvent(new Event(HISTORY_UPDATED_EVENT));
  };

  // 获取对话
  const getChat = (chatId: string): ChatHistoryItem | undefined => {
    return history.find(item => item.id === chatId);
  };

  return {
    history,
    saveChat,
    deleteChat,
    clearHistory,
    getChat
  };
};
