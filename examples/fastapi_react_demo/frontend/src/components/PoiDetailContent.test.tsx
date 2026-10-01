import { fireEvent, render, screen } from '@testing-library/react';
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
      review_summary: {
        text: '游客普遍建议避开节假日午后的人流高峰。',
        source_refs: ['source-1'],
        updated_at: '2026-07-14T08:00:00Z',
      },
    }} />);

    expect(screen.getByText('4.8')).not.toBeNull();
    expect(screen.getByText('全天开放')).not.toBeNull();
    expect(screen.getAllByText(/来源：景区公告/).length).toBeGreaterThan(1);
    expect(screen.getByRole('heading', { name: '游客评价摘要' })).not.toBeNull();
    expect(screen.getByText('游客普遍建议避开节假日午后的人流高峰。')).not.toBeNull();
    expect(document.querySelector('[data-poi-id="poi-west-lake"]')).not.toBeNull();
    const navigation = screen.getByRole('link', { name: /百度地图搜索导航/ });
    expect(navigation.getAttribute('href')).toContain('query=%E8%A5%BF%E6%B9%96');
    expect(navigation.getAttribute('href')).toContain('region=%E6%9D%AD%E5%B7%9E%E5%B8%82');
  });

  it('labels missing realtime information instead of inventing it', () => {
    render(<PoiDetailContent location={{ id: 'unknown', name: '待确认地点', lat: 1, lng: 1 }} />);
    expect(screen.getAllByText('暂无可靠实时数据').length).toBeGreaterThan(2);
    expect(screen.queryByText('暂无可靠图片')).toBeNull();
  });

  it('does not use raw map image urls and only shows up to three permitted image assets', () => {
    const image = {
      image_id: 'poi-image-1', url: 'https://images.unsplash.com/photo-1', alt: '西湖景色',
      display_allowed: true, export_allowed: false, attribution_required: true,
      attribution_text: '摄影师甲', attribution_url: 'https://unsplash.com/@photographer-a',
      provider_name: 'Unsplash', provider_url: 'https://unsplash.com/photos/example', source_ref: 'source-1',
    };
    const { container } = render(<PoiDetailContent location={{
      id: 'west-lake', name: '西湖', lat: 30.25, lng: 120.15,
      images: ['https://unverified.example/raw.jpg'],
      image_assets: [image, { ...image, image_id: 'poi-image-2' }, { ...image, image_id: 'poi-image-3' }, { ...image, image_id: 'poi-image-4' }],
    }} />);

    expect(container.querySelectorAll('.poi-detail-image-grid img')).toHaveLength(3);
    expect(container.querySelector('img[src="https://unverified.example/raw.jpg"]')).toBeNull();
  });

  it('hides an untraceable review summary instead of presenting it as sourced content', () => {
    render(<PoiDetailContent location={{
      id: 'west-lake', name: '西湖', lat: 30.25, lng: 120.15,
      review_summary: { text: '这段摘要没有可追溯来源。', source_refs: ['missing-source'] },
      sources: [{ source_reference_id: 'source-1', title: '景区公告' }],
    }} />);

    expect(screen.queryByRole('heading', { name: '游客评价摘要' })).toBeNull();
    expect(screen.queryByText('这段摘要没有可追溯来源。')).toBeNull();
  });

  it('collapses the image area after every permitted image fails to load', () => {
    const image = {
      image_id: 'poi-image-1', url: 'https://images.unsplash.com/photo-1', alt: '西湖景色',
      display_allowed: true, export_allowed: false, attribution_required: true,
      attribution_text: '摄影师甲', attribution_url: 'https://unsplash.com/@photographer-a',
      provider_name: 'Unsplash', provider_url: 'https://unsplash.com/photos/example', source_ref: 'source-1',
    };
    const { container } = render(<PoiDetailContent location={{
      id: 'west-lake', name: '西湖', lat: 30.25, lng: 120.15, image_assets: [image],
    }} />);

    fireEvent.error(screen.getByRole('img', { name: '西湖景色' }));

    expect(container.querySelector('.poi-detail-image-grid')).toBeNull();
  });
});
