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
}

const STORAGE_KEY = 'sage_chat_history';
const MAX_HISTORY_ITEMS = 50;
const HISTORY_UPDATED_EVENT = 'sage_chat_history_updated';

const normalizeHistory = (parsed: any[]): ChatHistoryItem[] => {
  return parsed.map((item: any) => ({
    ...item,
    createdAt: new Date(item.createdAt),
    updatedAt: new Date(item.updatedAt),
    messages: (item.messages || []).map((msg: any) => ({
      ...msg,
      content: typeof msg.content === 'string' ? msg.content : '',
      displayContent: typeof msg.displayContent === 'string'
        ? msg.displayContent
        : (typeof msg.content === 'string' ? msg.content : ''),
      timestamp: new Date(msg.timestamp)
    }))
  }));
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
    title?: string
  ): void => {
    if (messages.length === 0) return;

    const now = new Date();
    const chatTitle = title || generateTitle(messages);
    const latestHistory = readHistoryFromStorage();
    const existingIndex = latestHistory.findIndex(item => item.id === chatId);
    let newHistory: ChatHistoryItem[];

    if (existingIndex >= 0) {
      // 更新现有对话
      newHistory = [...latestHistory];
      newHistory[existingIndex] = {
        ...newHistory[existingIndex],
        title: chatTitle,
        messages: [...messages],
        updatedAt: now
      };
    } else {
      // 添加新对话
      const newItem: ChatHistoryItem = {
        id: chatId,
        title: chatTitle,
        messages: [...messages],
        createdAt: now,
        updatedAt: now
      };
      newHistory = [newItem, ...latestHistory];
    }

    // 限制历史记录数量
    if (newHistory.length > MAX_HISTORY_ITEMS) {
      newHistory = newHistory.slice(0, MAX_HISTORY_ITEMS);
    }

    // 按更新时间排序
    newHistory.sort((a, b) => b.updatedAt.getTime() - a.updatedAt.getTime());

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