import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import TripWorkspaceEditor from './TripWorkspaceEditor';

afterEach(() => vi.unstubAllGlobals());

describe('TripWorkspaceEditor', () => {
  it('uses a server selection for a new POI and saves exact duration', async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [{ selection_id: 'server-token', place: { name: '浙江省博物馆', address: '杭州市' } }] }) });
    vi.stubGlobal('fetch', fetcher);
    const save = vi.fn().mockResolvedValue(true);
    const close = vi.fn();
    render(<TripWorkspaceEditor target={{ type: 'add_activity', day: 2, title: '新增地点' }} destination="杭州" onClose={close} onSave={save} />);
    const submit = screen.getByRole('button', { name: '保存到草稿' });
    expect(submit.hasAttribute('disabled')).toBe(true);
    fireEvent.change(screen.getByLabelText('搜索真实地点'), { target: { value: '浙江省博物馆' } });
    fireEvent.click(screen.getByRole('button', { name: '搜 索' }));
    fireEvent.click(await screen.findByRole('button', { name: '浙江省博物馆 · 杭州市' }));
    fireEvent.change(screen.getByLabelText('开始时间'), { target: { value: '13:00' } });
    fireEvent.change(screen.getByLabelText('结束时间'), { target: { value: '14:45' } });
    fireEvent.click(submit);
    await waitFor(() => expect(save).toHaveBeenCalledWith('add_activity', { day: 2, selection_id: 'server-token', start_at: '13:00', end_at: '14:45' }));
    expect(close).toHaveBeenCalledOnce();
    expect(String(fetcher.mock.calls[0][0])).toContain('destination=');
  });

  it('retains note text when saving fails', async () => {
    const close = vi.fn();
    render(<TripWorkspaceEditor target={{ type: 'update_day_note', day: 1, title: '当日备注' }} destination="杭州" onClose={close} onSave={async () => false} />);
    fireEvent.change(screen.getByRole('textbox', { name: '当日备注' }), { target: { value: '下午再出发' } });
    fireEvent.click(screen.getByRole('button', { name: '保存到草稿' }));
    await screen.findByText('未能保存，请检查提示后重试。');
    expect((screen.getByRole('textbox', { name: '当日备注' }) as HTMLTextAreaElement).value).toBe('下午再出发');
    expect(close).not.toHaveBeenCalled();
  });
});
