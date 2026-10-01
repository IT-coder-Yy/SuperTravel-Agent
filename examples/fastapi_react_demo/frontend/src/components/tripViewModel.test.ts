import { describe, expect, it } from 'vitest';
import {
  adaptTripDay,
  adaptTripPlanToViewModel,
  upsertTripDay,
  upsertTripDayPayload,
} from './tripViewModel';

describe('tripViewModel', () => {
  it('将旧版扁平 activities 按天转换并保留迁移字段', () => {
    const result = adaptTripPlanToViewModel({
      activities: [
        {
          day: 2,
          start_time: '09:00',
          end_time: '11:00',
          title: '西湖',
          place: { name: '西湖', source: 'poi-db', data_type: 'reference_data' },
          transport_to_next: '步行 15 分钟',
          estimated_cost: 25,
          data_type: 'confirmed_live_data',
          source: 'legacy-source',
          source_references: [{ title: '景区公告' }],
        },
        { day: 1, title: '灵隐寺' },
      ],
    });

    expect(result.days.map((day) => day.day)).toEqual([1, 2]);
    const activity = result.days[1].activities[0];
    expect(activity).toMatchObject({
      start_time: '09:00',
      end_time: '11:00',
      estimated_cost: 25,
      data_type: 'confirmed_live_data',
      source: 'legacy-source',
    });
    expect(activity.sources).toEqual([{ title: '景区公告' }]);
    expect(activity.place).toMatchObject({ source: 'poi-db', data_type: 'reference_data' });
    expect(activity.route_to_next).toMatchObject({
      mode: '步行 15 分钟',
      data_type: 'confirmed_live_data',
    });
  });

  it('优先使用新版 days[].activities 并完整转换 RouteLeg', () => {
    const result = adaptTripPlanToViewModel({
      days: [{
        id: 'server-day-1',
        day: 1,
        date: '2026-10-01',
        theme: '古都中轴线',
        revision: 3,
        estimated_cost: 300,
        warnings: ['需预约'],
        source_references: ['planner'],
        activities: [{
          activity_id: 'server-activity-1',
          title: '故宫',
          duration_minutes: 90,
          fixed_time: true,
          images: [{ image_id: 'forbidden-image', display_allowed: false }],
          evidence_refs: [{ id: 'evidence-1' }],
          route_to_next: {
            mode: '地铁',
            distance_meters: 5200,
            duration_minutes: 28,
            estimated_cost: 4,
            source: 'route-api',
            sources: [{ title: '实时路线' }],
            data_type: 'confirmed_live_data',
            calculated_at: '2026-09-30T08:00:00Z',
          },
        }],
      }],
      activities: [{ day: 1, title: '不应重复出现' }],
    });

    const day = result.days[0];
    expect(day).toMatchObject({
      id: 'server-day-1',
      date: '2026-10-01',
      theme: '古都中轴线',
      revision: 3,
      estimated_cost: 300,
      warnings: ['需预约'],
      sources: ['planner'],
    });
    expect(day.activities).toHaveLength(1);
    expect(day.activities[0].id).toBe('server-activity-1');
    expect(day.activities[0].duration_minutes).toBe(90);
    expect(day.activities[0].fixed_time).toBe(true);
    expect(day.activities[0].images).toEqual([{ image_id: 'forbidden-image', display_allowed: false }]);
    expect(day.activities[0].evidence_refs).toEqual([{ id: 'evidence-1' }]);
    expect(day.activities[0].route_to_next).toEqual({
      mode: '地铁',
      distance_meters: 5200,
      duration_minutes: 28,
      estimated_cost: 4,
      source: 'route-api',
      sources: [{ title: '实时路线' }],
      data_type: 'confirmed_live_data',
      calculated_at: '2026-09-30T08:00:00Z',
    });
  });

  it('兼容后端 TripPlan 的 days 计数与 trip_days 明细组合', () => {
    const result = adaptTripPlanToViewModel({
      days: 2,
      trip_days: [{
        day: 1,
        theme: '中轴线',
        revision: 2,
        activities: [{ activity_id: 'a1', title: '故宫' }],
      }],
      activities: [{ activity_id: 'legacy', day: 1, title: '旧扁平副本' }],
    });

    expect(result.days).toHaveLength(1);
    expect(result.days[0].theme).toBe('中轴线');
    expect(result.days[0].revision).toBe(2);
    expect(result.days[0].activities[0].id).toBe('a1');
  });

  it('相同输入生成稳定的日期和活动 ID，展示字段变化不改变 ID', () => {
    const first = adaptTripDay({
      day: 1,
      activities: [{
        title: '天坛',
        start_time: '09:00',
        place: { name: '天坛公园' },
        estimated_cost: 20,
      }],
    });
    const changed = adaptTripDay({
      day: 1,
      revision: 2,
      activities: [{
        title: '天坛',
        start_time: '09:00',
        place: { name: '天坛公园' },
        estimated_cost: 35,
        route_to_next: { mode: '公交' },
      }],
    });

    expect(first).not.toBeNull();
    expect(changed).not.toBeNull();
    expect(changed?.id).toBe(first?.id);
    expect(changed?.activities[0].id).toBe(first?.activities[0].id);
  });

  it('同一天高版本覆盖、低版本忽略且不会追加重复日期', () => {
    const revisionTwo = adaptTripDay({
      day: 2,
      revision: 2,
      activities: [{ title: '新版内容' }],
    });
    const revisionOne = adaptTripDay({
      day: 2,
      revision: 1,
      activities: [{ title: '过期内容' }],
    });
    expect(revisionTwo).not.toBeNull();
    expect(revisionOne).not.toBeNull();

    const initial = upsertTripDay([], revisionTwo!);
    const ignored = upsertTripDay(initial, revisionOne!);
    const repeated = upsertTripDay(ignored, revisionTwo!);

    expect(ignored).toBe(initial);
    expect(repeated).toHaveLength(1);
    expect(repeated[0].activities[0].title).toBe('新版内容');
  });

  it('增量载荷 upsert 保持日期顺序并替换同日快照', () => {
    let days = upsertTripDayPayload([], {
      day: 2,
      revision: 1,
      activities: [{ title: 'Day 2 草稿' }],
    });
    days = upsertTripDayPayload(days, {
      day: 1,
      revision: 1,
      activities: [{ title: 'Day 1' }],
    });
    days = upsertTripDayPayload(days, {
      day: 2,
      revision: 2,
      activities: [{ title: 'Day 2 路线修正版' }],
    });

    expect(days.map((day) => day.day)).toEqual([1, 2]);
    expect(days[1].revision).toBe(2);
    expect(days[1].activities[0].title).toBe('Day 2 路线修正版');
  });

  it('安全忽略无效输入', () => {
    expect(adaptTripPlanToViewModel(null)).toEqual({ days: [] });
    expect(adaptTripPlanToViewModel({ days: ['invalid'] })).toEqual({ days: [] });
    expect(upsertTripDayPayload([], 'invalid')).toEqual([]);
  });
});
