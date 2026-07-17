import { useCallback, useEffect, useState } from 'react';
import type { TravelKnowledgeSearchItem } from '../services/apiClient';

export interface SelectedKnowledgeContextItem {
  city: string;
  province: string;
  title: string;
  source: string;
  source_url: string;
  snippet: string;
}

export const SELECTED_KNOWLEDGE_CONTEXT_STORAGE_KEY = 'supertravelagent.selectedKnowledgeContext';

const normalizeKnowledgeItem = (item: TravelKnowledgeSearchItem): SelectedKnowledgeContextItem => ({
  city: item.city,
  province: item.province,
  title: item.title,
  source: item.source,
  source_url: item.source_url,
  snippet: item.snippet,
});

const itemKey = (item: SelectedKnowledgeContextItem) => [
  item.city,
  item.source,
  item.title,
  item.snippet.slice(0, 80),
].join('|');

export const readSelectedKnowledgeContext = (): SelectedKnowledgeContextItem[] => {
  try {
    const raw = window.localStorage.getItem(SELECTED_KNOWLEDGE_CONTEXT_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item): item is SelectedKnowledgeContextItem => Boolean(item) && typeof item === 'object')
      .map((item) => ({
        city: String(item.city || ''),
        province: String(item.province || ''),
        title: String(item.title || ''),
        source: String(item.source || ''),
        source_url: String(item.source_url || ''),
        snippet: String(item.snippet || ''),
      }))
      .filter((item) => item.title || item.snippet);
  } catch (_error) {
    return [];
  }
};

export const writeSelectedKnowledgeContext = (items: SelectedKnowledgeContextItem[]) => {
  window.localStorage.setItem(SELECTED_KNOWLEDGE_CONTEXT_STORAGE_KEY, JSON.stringify(items.slice(0, 8)));
  window.dispatchEvent(new Event('selected-knowledge-context-change'));
};

export const addSelectedKnowledgeContext = (item: TravelKnowledgeSearchItem): SelectedKnowledgeContextItem[] => {
  const normalized = normalizeKnowledgeItem(item);
  const previous = readSelectedKnowledgeContext();
  const seen = new Set<string>();
  const next = [normalized, ...previous]
    .filter((entry) => {
      const key = itemKey(entry);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 8);
  writeSelectedKnowledgeContext(next);
  return next;
};

export const clearSelectedKnowledgeContext = () => {
  window.localStorage.removeItem(SELECTED_KNOWLEDGE_CONTEXT_STORAGE_KEY);
  window.dispatchEvent(new Event('selected-knowledge-context-change'));
};

export const useSelectedKnowledgeContext = () => {
  const [selectedKnowledgeContext, setSelectedKnowledgeContext] = useState<SelectedKnowledgeContextItem[]>(() => readSelectedKnowledgeContext());

  const refresh = useCallback(() => {
    setSelectedKnowledgeContext(readSelectedKnowledgeContext());
  }, []);

  useEffect(() => {
    window.addEventListener('storage', refresh);
    window.addEventListener('selected-knowledge-context-change', refresh);
    return () => {
      window.removeEventListener('storage', refresh);
      window.removeEventListener('selected-knowledge-context-change', refresh);
    };
  }, [refresh]);

  return {
    selectedKnowledgeContext,
    clearSelectedKnowledgeContext,
  };
};
