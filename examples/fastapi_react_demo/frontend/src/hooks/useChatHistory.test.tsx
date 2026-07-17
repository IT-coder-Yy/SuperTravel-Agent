import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useChatHistory } from './useChatHistory';

const message = (content: string) => [{
  id: 'm1', role: 'user' as const, content, displayContent: content, timestamp: new Date('2026-07-15T08:00:00Z'),
}];

describe('useChatHistory', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-07-15T08:00:00Z'));
  });

  afterEach(() => vi.useRealTimers());

  it('查看或重复持久化相同内容不会刷新内容更新时间', () => {
    const { result } = renderHook(() => useChatHistory());
    act(() => result.current.saveChat('chat-1', message('杭州三日游')));
    const original = JSON.parse(localStorage.getItem('sage_chat_history') || '[]')[0].contentUpdatedAt;
    vi.setSystemTime(new Date('2026-07-15T09:00:00Z'));
    act(() => result.current.saveChat('chat-1', message('杭州三日游'), undefined, { touchUpdatedAt: false }));
    expect(JSON.parse(localStorage.getItem('sage_chat_history') || '[]')[0].contentUpdatedAt).toBe(original);
  });

  it('行程版本变化会保存方案快照并更新时间', () => {
    const { result } = renderHook(() => useChatHistory());
    act(() => result.current.saveChat('chat-1', message('杭州三日游')));
    vi.setSystemTime(new Date('2026-07-15T09:00:00Z'));
    act(() => result.current.saveChat('chat-1', message('杭州三日游'), undefined, {
      changeReason: 'trip_edit', touchUpdatedAt: true,
      tripPlan: { plan_id: 'p1', version: 2 },
      tripDocument: { schema_version: '2.0', plan_id: 'p1', version: 2 },
      tripWorkspace: { days: [{ day: 1 }] },
    }));
    const saved = JSON.parse(localStorage.getItem('sage_chat_history') || '[]')[0];
    expect(saved.tripPlan.version).toBe(2);
    expect(saved.tripDocument.schema_version).toBe('2.0');
    expect(saved.contentUpdatedAt).toBe('2026-07-15T09:00:00.000Z');
  });
});
