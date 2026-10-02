import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Sidebar from './Sidebar';

const historyState = vi.hoisted(() => ({ loadMore: vi.fn(), deleteChat: vi.fn(), clearHistory: vi.fn() }));
vi.mock('../hooks/useChatHistory', () => ({
  useChatHistory: () => ({
    history: [
      { id: 'hangzhou', title: '杭州慢游', preview: '西湖', messages: [], contentUpdatedAt: new Date() },
      { id: 'chengdu', title: '成都周末', preview: '美食', messages: [], contentUpdatedAt: new Date() },
    ],
    hasMore: true, isLoading: false, ...historyState,
  }),
}));
const RouteProbe = () => <output data-testid="route">{useLocation().pathname}</output>;
beforeEach(() => vi.clearAllMocks());

describe('C1 常驻导航与旅程管理', () => {
  it('keeps supporting pages reachable and opens new trip through the existing callback', () => {
    const onNewChat = vi.fn();
    render(<MemoryRouter><Sidebar onNewChat={onNewChat} /><RouteProbe /></MemoryRouter>);
    for (const [label, path] of [['知识库', '/knowledge'], ['用户画像', '/profile'], ['设置', '/settings'], ['旅行照片', '/photo-editor']]) {
      fireEvent.click(screen.getByRole('button', { name: label }));
      expect(screen.getByTestId('route').textContent).toBe(path);
    }
    fireEvent.click(screen.getByRole('button', { name: '新旅程' }));
    expect(onNewChat).toHaveBeenCalledOnce();
  });

  it('searches history, loads further pages, and restores the chosen journey without deleting data', async () => {
    const onChatSelect = vi.fn().mockResolvedValue(undefined);
    render(<MemoryRouter><Sidebar onChatSelect={onChatSelect} /></MemoryRouter>);
    fireEvent.click(screen.getByRole('button', { name: '旅程管理' }));
    fireEvent.change(screen.getByRole('textbox', { name: '搜索行程' }), { target: { value: '西湖' } });
    expect(screen.getByRole('button', { name: /杭州慢游 今天/ })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /成都周末 今天/ })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '加载更多' }));
    expect(historyState.loadMore).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: /杭州慢游 今天/ }));
    await waitFor(() => expect(onChatSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 'hangzhou' })));
    expect(historyState.deleteChat).not.toHaveBeenCalled();
    expect(historyState.clearHistory).not.toHaveBeenCalled();
  });
});
