import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import PoiDetailContent from './PoiDetailContent';

describe('PoiDetailContent', () => {
  it('shows complete sourced POI fields from one shared record', () => {
    render(<PoiDetailContent location={{
      id: 'west-lake', poi_id: 'poi-west-lake', name: '西湖', category: '景点', lat: 30.25, lng: 120.15,
      rating: 4.8, summary: '世界文化景观', address: '杭州市西湖区', suggested_duration_minutes: 240,
      opening_hours: '全天开放', reservation: '部分景点需预约', price: '免费', suitable_for: ['首次到访'],
      unsuitable_for: ['行动不便者长距离步行'], updated_at: '2026-07-14T08:00:00Z',
      sources: [{ source_reference_id: 'source-1', title: '景区公告', updated_at: '2026-07-14T08:00:00Z' }],
      field_evidence: { opening_hours: { source_reference_id: 'source-1', updated_at: '2026-07-14T08:00:00Z' } },
    }} />);

    expect(screen.getByText('4.8')).not.toBeNull();
    expect(screen.getByText('全天开放')).not.toBeNull();
    expect(screen.getAllByText(/来源：景区公告/).length).toBeGreaterThan(1);
    expect(document.querySelector('[data-poi-id="poi-west-lake"]')).not.toBeNull();
  });

  it('labels missing realtime information instead of inventing it', () => {
    render(<PoiDetailContent location={{ id: 'unknown', name: '待确认地点', lat: 1, lng: 1 }} />);
    expect(screen.getAllByText('暂无可靠实时数据').length).toBeGreaterThan(2);
    expect(screen.getByText('暂无可靠图片')).not.toBeNull();
  });
});
