import { describe, expect, it } from 'vitest';
import fixture from '../../../../../backend/tests/fixtures/trip_v3_domestic_3d.json';
import { restoreFormalSnapshotState } from './formalSnapshotRestore';

describe('restoreFormalSnapshotState', () => {
  it('从 V3 当前正式快照恢复方案、工作台和地图地点', () => {
    const restored = restoreFormalSnapshotState({
      current: { revision: 1, document: fixture },
    });

    expect(restored?.plan.plan_id).toBe('plan_fixture_hangzhou_3d');
    expect(restored?.plan.version).toBe(1);
    expect(restored?.workspace.days).toHaveLength(3);
    expect(restored?.workspace.locations.some((location) => (
      location.name === '杭州契约景点一'
      && location.lat === 30.259
      && location.lng === 120.142
    ))).toBe(true);
    expect(restored?.workspace.sources[0]?.source).toBe('契约测试地图 Provider');
    expect(restored?.workspace.validation?.valid).toBe(true);
    expect(restored?.document.schema_version).toBe('3.0');
  });

  it('保留地点详情所需的合规图片、游客评价摘要及其来源', () => {
    const document = JSON.parse(JSON.stringify(fixture));
    const activity = document.itinerary.days[0].activities[0];
    activity.images = [{
      image_id: 'detail-image-1',
      url: 'https://images.unsplash.com/photo-1',
      alt: '杭州景色',
      display_allowed: true,
      export_allowed: false,
      attribution_required: true,
      attribution_text: '摄影师甲',
      attribution_url: 'https://unsplash.com/@photographer-a',
      provider_name: 'Unsplash',
      provider_url: 'https://unsplash.com/photos/example',
      source_ref: 'review-source',
    }];
    activity.place.review_summary = {
      text: '游客普遍建议预留充足步行时间。',
      source_refs: ['review-source'],
      updated_at: '2026-07-20T08:00:00+08:00',
    };
    document.sources.push({
      source_id: 'review-source',
      title: '可追溯游客评价摘要',
      source_name: '点评聚合服务',
      status: 'reference_data',
      url: 'https://example.com/review-source',
      updated_at: '2026-07-20T08:00:00+08:00',
      related_fields: ['review_summary'],
      related_place_ids: [activity.place.poi_id],
    });

    const restored = restoreFormalSnapshotState({ current: { revision: 1, document } });
    const location = restored?.workspace.locations.find((item) => item.id === activity.place.poi_id);

    expect(location?.image_assets).toHaveLength(1);
    expect(location?.review_summary?.text).toBe('游客普遍建议预留充足步行时间。');
    expect(location?.sources).toContainEqual(expect.objectContaining({
      source_reference_id: 'review-source',
      title: '可追溯游客评价摘要',
    }));
  });

  it('将规划住宿和市内交通锚点作为独立地图节点恢复，不混入正式活动', () => {
    const document = JSON.parse(JSON.stringify(fixture));
    const day = document.itinerary.days[0];
    day.anchors = [{
      anchor_id: 'anchor-arrival-hub',
      kind: 'arrival_hub',
      day: 1,
      display_label: '从杭州东站抵达',
      place: {
        poi_id: 'poi-hangzhou-east',
        name: '杭州东站',
        category: 'transport',
        coordinates: { latitude: 30.294, longitude: 120.212, coordinate_system: 'WGS84' },
        evidence_refs: [],
      },
    }];
    document.map_guidance.formal_location_ids.push('anchor-arrival-hub');

    const restored = restoreFormalSnapshotState({ current: { revision: 1, document } });
    const anchor = restored?.workspace.locations.find((location) => location.id === 'anchor-arrival-hub');

    expect(anchor).toMatchObject({
      id: 'anchor-arrival-hub',
      poi_id: 'poi-hangzhou-east',
      anchor_kind: 'arrival_hub',
      anchor_label: '从杭州东站抵达',
      day: 1,
    });
    expect(restored?.workspace.days?.[0].activities.some((activity) => activity.id === 'anchor-arrival-hub')).toBe(false);
  });

  it('没有有效当前正式快照时不覆盖旧版展示数据', () => {
    expect(restoreFormalSnapshotState({ previous: { document: fixture } })).toBeNull();
    expect(restoreFormalSnapshotState({ current: { document: { schema_version: '2.0' } } })).toBeNull();
  });
});
