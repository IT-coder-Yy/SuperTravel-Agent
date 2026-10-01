import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { resetTripHistoryApiForTests } from '../services/tripHistoryApi';
import { useChatHistory } from './useChatHistory';
import formalFixture from '../../../backend/tests/fixtures/trip_v3_domestic_3d.json';


const jsonResponse = (body: unknown, status = 200): Response => ({
  ok: status >= 200 && status < 300,
  status,
  statusText: status >= 400 ? 'Error' : 'OK',
  json: async () => body,
} as Response);

const message = (content: string) => [{
  id: 'm1', role: 'user' as const, content, displayContent: content,
  timestamp: new Date('2026-07-15T08:00:00Z'),
}];

const createHistoryServer = () => {
  const store = new Map<string, Record<string, any>>();
  let tick = 0;
  const now = () => `2026-07-15T${String(8 + tick++).padStart(2, '0')}:00:00.000Z`;
  const summary = (item: Record<string, any>) => ({
    id: item.id,
    title: item.title,
    status: item.status || 'draft',
    preview: item.messages?.find((entry: any) => entry.role === 'user')?.content || '',
    createdAt: item.createdAt,
    updatedAt: item.updatedAt,
    contentUpdatedAt: item.contentUpdatedAt,
    currentRevision: item.currentRevision || 0,
  });

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = init.method || 'GET';
    if (url === '/api/device') return jsonResponse({ status: 'ready', device_id: 'device-1' });
    if (url === '/api/trips/migrate' && method === 'POST') {
      const items = JSON.parse(String(init.body || '{}')).items as Record<string, any>[];
      let imported = 0;
      items.forEach((item) => {
        if (store.has(item.id)) return;
        store.set(item.id, {
          ...item,
          status: 'draft',
          currentRevision: 0,
          updatedAt: item.updatedAt || item.contentUpdatedAt || item.createdAt,
        });
        imported += 1;
      });
      return jsonResponse({ imported, skipped: items.length - imported });
    }
    if (url.startsWith('/api/trips?page=') && method === 'GET') {
      const items = [...store.values()]
        .sort((a, b) => String(b.contentUpdatedAt).localeCompare(String(a.contentUpdatedAt)))
        .map(summary);
      return jsonResponse({ items, page: 1, page_size: 20, total: items.length, has_more: false });
    }
    if (url === '/api/trips' && method === 'DELETE') {
      store.clear();
      return jsonResponse({ deleted: 1 });
    }
    const match = url.match(/^\/api\/trips\/([^/]+)$/);
    if (match) {
      const id = decodeURIComponent(match[1]);
      if (method === 'GET') {
        const item = store.get(id);
        return item ? jsonResponse({ ...summary(item), ...item }) : jsonResponse({ detail: '不存在' }, 404);
      }
      if (method === 'PUT') {
        const payload = JSON.parse(String(init.body || '{}'));
        const existing = store.get(id);
        const timestamp = now();
        const next = {
          ...existing,
          id,
          title: payload.title,
          messages: payload.messages,
          tripPlan: 'trip_plan' in payload ? payload.trip_plan : existing?.tripPlan,
          tripDocument: 'trip_document' in payload ? payload.trip_document : existing?.tripDocument,
          tripWorkspace: 'trip_workspace' in payload ? payload.trip_workspace : existing?.tripWorkspace,
          agentTimeline: 'agent_timeline' in payload ? payload.agent_timeline : existing?.agentTimeline,
          status: 'draft',
          createdAt: existing?.createdAt || timestamp,
          updatedAt: timestamp,
          contentUpdatedAt: payload.change_reason === 'system' && existing
            ? existing.contentUpdatedAt
            : timestamp,
          currentRevision: existing?.currentRevision || 0,
        };
        store.set(id, next);
        return jsonResponse({ ...summary(next), ...next });
      }
      if (method === 'DELETE') {
        store.delete(id);
        return jsonResponse({ deleted: 1 });
      }
    }
    return jsonResponse({ detail: '未知接口' }, 404);
  });
  return { store, fetchMock };
};


describe('useChatHistory', () => {
  beforeEach(() => {
    localStorage.clear();
    resetTripHistoryApiForTests();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('首次进入会迁移旧历史，成功后只留下摘要镜像', async () => {
    const server = createHistoryServer();
    vi.stubGlobal('fetch', server.fetchMock);
    localStorage.setItem('sage_chat_history', JSON.stringify([{
      id: 'legacy-1', title: '杭州旧行程', messages: message('杭州三日游'),
      createdAt: '2026-07-15T08:00:00.000Z', updatedAt: '2026-07-15T08:00:00.000Z',
      contentUpdatedAt: '2026-07-15T08:00:00.000Z',
    }]));

    const { result } = renderHook(() => useChatHistory());
    await waitFor(() => expect(result.current.history).toHaveLength(1));
    const detail = await result.current.getChat('legacy-1');

    expect(detail?.messages[0].content).toBe('杭州三日游');
    expect(localStorage.getItem('sage_chat_history')).toBeNull();
    expect(localStorage.getItem('sage_trip_history_migrated_v1')).toBe('1');
    const mirror = JSON.parse(localStorage.getItem('sage_trip_history_mirror_v1') || '[]');
    expect(mirror[0].messages).toBeUndefined();
    expect(server.fetchMock).toHaveBeenCalledWith('/api/trips/migrate', expect.objectContaining({ method: 'POST' }));
  });

  it('相同内容的系统保存不会刷新内容更新时间', async () => {
    const server = createHistoryServer();
    vi.stubGlobal('fetch', server.fetchMock);
    const { result } = renderHook(() => useChatHistory());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => result.current.saveChat('trip-1', message('杭州三日游')));
    await waitFor(() => expect(server.store.has('trip-1')).toBe(true));
    const original = server.store.get('trip-1')?.contentUpdatedAt;
    act(() => result.current.saveChat('trip-1', message('杭州三日游'), undefined, { touchUpdatedAt: false }));
    await waitFor(() => expect(server.fetchMock.mock.calls.filter((call) => call[1]?.method === 'PUT')).toHaveLength(2));

    expect(server.store.get('trip-1')?.contentUpdatedAt).toBe(original);
  });

  it('保存并恢复可回看的 Agent 安全阶段摘要', async () => {
    const server = createHistoryServer();
    vi.stubGlobal('fetch', server.fetchMock);
    const { result } = renderHook(() => useChatHistory());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    const agentTimeline = {
      request_id: 'request-history-review',
      run_id: 'run-history-review',
      status: 'completed',
      stages: {
        research: {
          label: '资料研究',
          status: 'completed',
          agent_name: '资料研究 Agent',
          task: '整理公开资料',
          data_sources: ['本地知识库'],
          summary: '已汇总公开资料',
        },
      },
    };

    act(() => result.current.saveChat('trip-agent-timeline', message('杭州三日游'), undefined, { agentTimeline }));
    await waitFor(() => expect(server.store.get('trip-agent-timeline')?.agentTimeline).toEqual(agentTimeline));

    const detail = await result.current.getChat('trip-agent-timeline');

    expect(detail?.agentTimeline).toEqual(agentTimeline);
  });

  it('单程删除和删除全部都调用后端并同步摘要镜像', async () => {
    const server = createHistoryServer();
    server.store.set('trip-1', {
      id: 'trip-1', title: '杭州', messages: message('杭州'), status: 'draft',
      createdAt: '2026-07-15T08:00:00.000Z', updatedAt: '2026-07-15T08:00:00.000Z',
      contentUpdatedAt: '2026-07-15T08:00:00.000Z', currentRevision: 0,
    });
    server.store.set('trip-2', {
      id: 'trip-2', title: '东京', messages: message('东京'), status: 'draft',
      createdAt: '2026-07-15T09:00:00.000Z', updatedAt: '2026-07-15T09:00:00.000Z',
      contentUpdatedAt: '2026-07-15T09:00:00.000Z', currentRevision: 0,
    });
    vi.stubGlobal('fetch', server.fetchMock);
    const { result } = renderHook(() => useChatHistory());
    await waitFor(() => expect(result.current.history).toHaveLength(2));

    await act(async () => result.current.deleteChat('trip-1'));
    await waitFor(() => expect(result.current.history).toHaveLength(1));
    await act(async () => result.current.clearHistory());
    await waitFor(() => expect(result.current.history).toHaveLength(0));

    expect(server.store.size).toBe(0);
    expect(JSON.parse(localStorage.getItem('sage_trip_history_mirror_v1') || '[]')).toEqual([]);
  });

  it('旧历史迁移失败时不会删除浏览器原始数据', async () => {
    const server = createHistoryServer();
    server.fetchMock.mockImplementationOnce(async () => jsonResponse({ status: 'ready' }));
    server.fetchMock.mockImplementationOnce(async () => jsonResponse({ detail: '迁移失败' }, 500));
    vi.stubGlobal('fetch', server.fetchMock);
    const legacy = [{
      id: 'legacy-1', title: '保留的旧行程', messages: message('杭州'),
      createdAt: '2026-07-15T08:00:00.000Z', updatedAt: '2026-07-15T08:00:00.000Z',
      contentUpdatedAt: '2026-07-15T08:00:00.000Z',
    }];
    const legacyRaw = JSON.stringify(legacy);
    localStorage.setItem('sage_chat_history', legacyRaw);

    const { result } = renderHook(() => useChatHistory());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(localStorage.getItem('sage_chat_history')).toBe(legacyRaw);
    expect(localStorage.getItem('sage_trip_history_migrated_v1')).toBeNull();
  });

  it('读取旅程时始终以当前正式快照恢复工作台', async () => {
    const server = createHistoryServer();
    server.store.set('trip-formal', {
      id: 'trip-formal', title: '杭州正式方案', messages: message('杭州三日游'), status: 'completed',
      createdAt: '2026-07-15T08:00:00.000Z', updatedAt: '2026-07-15T08:00:00.000Z',
      contentUpdatedAt: '2026-07-15T08:00:00.000Z', currentRevision: 1,
      tripPlan: null,
      tripDocument: null,
      tripWorkspace: { locations: [], sources: [], budget: null, validation: null, repair: null },
      formalSnapshots: {
        current: { revision: 1, document: formalFixture },
        previous: { revision: 0, document: formalFixture },
      },
    });
    vi.stubGlobal('fetch', server.fetchMock);
    const { result } = renderHook(() => useChatHistory());
    await waitFor(() => expect(result.current.history).toHaveLength(1));

    const detail = await result.current.getChat('trip-formal');

    expect(detail?.tripPlan?.plan_id).toBe('plan_fixture_hangzhou_3d');
    expect(detail?.tripDocument?.schema_version).toBe('3.0');
    expect((detail?.tripWorkspace?.locations as unknown[])).not.toHaveLength(0);
    expect(detail?.hasPreviousFormalSnapshot).toBe(true);
  });
});
