import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import App from './App';

const mocks = vi.hoisted(() => ({
  history: [] as any[], getChat: vi.fn(), activeRun: vi.fn(),
}));
vi.mock('./hooks/useChatHistory', () => ({
  useChatHistory: () => ({ history: mocks.history, getChat: mocks.getChat }),
}));
vi.mock('./services/apiClient', () => ({ apiClient: { getActivePlanningRun: mocks.activeRun } }));
vi.mock('./context/SystemContext', () => ({ SystemProvider: ({ children }: any) => children }));
vi.mock('./components/Sidebar', () => ({
  default: ({ onChatSelect }: any) => <button onClick={() => onChatSelect({ id: 'selected', messages: [] })}>选择历史</button>,
}));
vi.mock('./features/travel/TravelPlannerPage', async () => {
  const { forwardRef } = await import('react');
  return { default: forwardRef(({ currentChatId, loadedTripDocument }: any, _ref) => (
    <output data-testid="current">{currentChatId}:{loadedTripDocument?.revision || 0}</output>
  )) };
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}
const detail = (id: string, revision = 1) => ({ id, messages: [], tripDocument: { revision } });

describe('历史恢复请求顺序', () => {
  beforeEach(() => {
    mocks.history = [{ id: 'latest' }];
    mocks.getChat.mockReset();
    mocks.activeRun.mockReset();
    mocks.getChat.mockImplementation(async (id) => detail(id));
  });
  afterEach(() => vi.useRealTimers());

  it('后台状态迟到但没有活动任务时，不重复读取已恢复的正式历史', async () => {
    const status = deferred<any>();
    mocks.activeRun.mockReturnValue(status.promise);
    render(<App />);
    await waitFor(() => expect(screen.getByTestId('current').textContent).toBe('latest:1'));
    await act(async () => status.resolve({ active_run: null }));
    expect(mocks.getChat).toHaveBeenCalledTimes(1);
  });

  it('其它旅程的后台任务不会触发当前历史再次恢复', async () => {
    mocks.activeRun.mockResolvedValue({ active_run: { trip_id: 'other' } });
    render(<App />);
    await waitFor(() => expect(mocks.activeRun).toHaveBeenCalledTimes(1));
    expect(mocks.getChat).toHaveBeenCalledTimes(1);
  });

  it('明确选历史后，迟到的历史列表不再启动自动恢复', async () => {
    mocks.history = [];
    const selected = deferred<any>();
    mocks.getChat.mockReturnValue(selected.promise);
    const view = render(<App />);
    fireEvent.click(screen.getByText('选择历史'));
    mocks.history = [{ id: 'latest' }];
    view.rerender(<App />);
    await act(async () => selected.resolve(detail('selected', 2)));
    expect(screen.getByTestId('current').textContent).toBe('selected:2');
    expect(mocks.getChat.mock.calls).toEqual([['selected']]);
  });

  it('已确认当前旅程有后台规划，完成后仍读取新正式修订', async () => {
    vi.useFakeTimers();
    mocks.activeRun.mockResolvedValueOnce({ active_run: { trip_id: 'latest' } })
      .mockResolvedValueOnce({ active_run: null });
    mocks.getChat.mockResolvedValueOnce(detail('latest')).mockResolvedValueOnce(detail('latest', 2));
    render(<App />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByTestId('current').textContent).toBe('latest:1');
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(screen.getByTestId('current').textContent).toBe('latest:2');
    expect(mocks.getChat).toHaveBeenCalledTimes(2);
  });
});
