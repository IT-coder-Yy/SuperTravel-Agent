import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import SharedTripPage from './SharedTripPage';

describe('SharedTripPage', () => {
  it('renders only the public read-only snapshot', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ title: '杭州行程', scopes: ['itinerary', 'budget', 'sources'], plan: { title: '杭州行程', days: 1, activities: [{ activity_id: 'a1', day: 1, title: '西湖', place: { name: '西湖' } }] }, budget: { budget_total: 1000, estimated_total: 860, categories: { transport: 120, food: 240 } }, sources: [{ title: '官方页面', type: 'official', updated_at: '2026-07-14T00:00:00Z', confidence: 'high', related_fields: ['opening_hours'] }] }),
    }));
    render(<MemoryRouter initialEntries={['/share/token']}><Routes><Route path="/share/:token" element={<SharedTripPage />} /></Routes></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('杭州行程')).not.toBeNull());
    expect(screen.getByText('只读快照')).not.toBeNull();
    expect(screen.getAllByText('西湖').length).toBeGreaterThan(0);
    expect(screen.getByText('¥860')).not.toBeNull();
    expect(screen.getByText(/可信度：high/)).not.toBeNull();
    expect(screen.getByText(/对应：opening_hours/)).not.toBeNull();
    expect(screen.queryByText(/API Key|工具调用|serper_site_search/)).toBeNull();
  });
});
