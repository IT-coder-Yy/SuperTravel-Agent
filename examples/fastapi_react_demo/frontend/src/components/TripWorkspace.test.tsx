import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import TripWorkspace, {
  TripWorkspaceLocationGroup,
  TripWorkspaceState,
} from './TripWorkspace';
import type { TripDay } from './tripViewModel';

const currentLocation = {
  id: 'west-lake',
  name: '西湖',
  lat: 30.2501,
  lng: 120.1536,
  category: '自然风光',
  day: 1,
};

const historicalLocation = {
  id: 'lingyin-temple',
  name: '灵隐寺',
  lat: 30.2409,
  lng: 120.1014,
  category: '人文古迹',
  day: 1,
};

const data: TripWorkspaceState = {
  locations: [currentLocation],
  sources: [],
  budget: null,
  validation: null,
  repair: null,
};

const locationGroups: TripWorkspaceLocationGroup[] = [
  {
    groupId: 'structured_trip_workspace_day_1',
    title: '第1天路线',
    locations: [currentLocation],
    sourceKind: 'current_plan',
  },
  {
    groupId: 'group_1_history_day_1',
    title: '第1天路线',
    locations: [historicalLocation],
    sourceKind: 'historical_answer',
  },
];

const structuredDays: TripDay[] = [
  {
    id: 'day-1',
    day: 1,
    date: '2026-08-01',
    theme: '西湖经典线',
    revision: 1,
    estimated_cost: 120,
    warnings: [],
    data_type: 'estimated_data',
    source: null,
    sources: [],
    activities: [
      {
        id: 'activity-west-lake',
        day: 1,
        start_time: '09:00',
        end_time: '10:30',
        title: '环湖漫步',
        place: {
          name: '西湖',
          category: '自然风光',
          lat: 30.2501,
          lng: 120.1536,
          data_type: 'confirmed_live_data',
        },
        estimated_cost: 0,
        notes: ['从断桥开始游览'],
        data_type: 'confirmed_live_data',
        source: null,
        sources: [],
        evidence_refs: [],
        route_to_next: {
          mode: 'taxi',
          distance_meters: 2400,
          duration_minutes: 18,
          estimated_cost: 12,
          source: null,
          sources: [],
          data_type: 'reference_data',
          calculated_at: '2026-07-13T10:00:00+08:00',
        },
      },
      {
        id: 'activity-lingyin',
        day: 1,
        start_time: '11:00',
        end_time: '12:15',
        title: '参观古刹',
        place: {
          name: '灵隐寺',
          category: '人文古迹',
          lat: 30.2409,
          lng: 120.1014,
        },
        estimated_cost: 75,
        notes: [],
        data_type: 'estimated_data',
        source: null,
        sources: [],
        evidence_refs: [],
        route_to_next: null,
      },
    ],
  },
  {
    id: 'day-2',
    day: 2,
    date: null,
    theme: '运河文化线',
    revision: 1,
    estimated_cost: null,
    warnings: [],
    data_type: null,
    source: null,
    sources: [],
    activities: [
      {
        id: 'activity-canal',
        day: 2,
        start_time: '10:00',
        end_time: null,
        title: '乘船体验',
        place: { name: '京杭大运河', category: '城市人文' },
        estimated_cost: 60,
        notes: [],
        data_type: 'reference_data',
        source: null,
        sources: [],
        evidence_refs: [],
        route_to_next: null,
      },
    ],
  },
];

const renderWorkspace = (overrides: Partial<React.ComponentProps<typeof TripWorkspace>> = {}) => {
  const props: React.ComponentProps<typeof TripWorkspace> = {
    data,
    locationGroups,
    activeGroupId: '',
    selectedLocationId: '',
    onSelectGroup: vi.fn(),
    onSelectLocation: vi.fn(),
    onFocusMap: vi.fn(),
    onQuickAction: vi.fn(),
    ...overrides,
  };
  return { ...render(<TripWorkspace {...props} />), props };
};

describe('TripWorkspace', () => {
  it('renders explicit budget categories, unknown costs and overrun warning', () => {
    renderWorkspace({ data: { ...data, budget: {
      currency: 'CNY', budget_total: 1000, budget_per_person: 500, people_count: 2,
      known_total: 1260, unknown_count: 2, unknown_items: ['夜间交通', '行李寄存'],
      categories: { transport: 200, accommodation: 600, food: 240, tickets: 180, other: 40 },
      over_budget: true, overrun_amount: 260, source_label: '基于地点类别估算', updated_at: '2026-07-14T08:00:00Z', data_type: 'estimated_data',
    } } });
    fireEvent.click(screen.getByRole('tab', { name: '预算' }));
    expect(screen.getByText('¥1,000')).toBeTruthy();
    expect(screen.getByText('¥600')).toBeTruthy();
    expect(screen.getByText('2 项')).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toContain('¥260');
    expect(screen.getByText(/基于地点类别估算/)).toBeTruthy();
  });

  it('renders source metadata and no-source state accurately', () => {
    renderWorkspace({ data: { ...data, sources: [{
      type: 'official', title: '西湖景区公告', url: 'https://example.com/west-lake', snippet: '开放信息',
      updated_at: '2026-07-14T08:00:00Z', confidence: 0.9, related_fields: ['开放时间'], related_places: ['西湖'], data_type: 'reference_data',
    }] } });
    fireEvent.click(screen.getByRole('tab', { name: '来源' }));
    expect(screen.getByText('西湖景区公告')).toBeTruthy();
    expect(screen.getByText('可信度：90%')).toBeTruthy();
    expect(screen.getByText('关联字段：开放时间')).toBeTruthy();
    expect(screen.getByText('https://example.com/west-lake')).toBeTruthy();
  });

  it('renders structured days as date tabs with one-day activity details', () => {
    renderWorkspace({ data: { ...data, days: structuredDays } });

    expect(screen.getByRole('tab', { name: '2026-08-01 · Day 1' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'Day 2' })).toBeTruthy();
    expect(screen.getByLabelText('Day 1 行程')).toBeTruthy();
    expect(screen.getByText('09:00–10:30')).toBeTruthy();
    expect(screen.getByText('停留 1小时30分钟')).toBeTruthy();
    expect(screen.getByText('西湖')).toBeTruthy();
    expect(screen.getByText('环湖漫步')).toBeTruthy();
    expect(screen.getByText('自然风光')).toBeTruthy();
    expect(screen.getByText('免费')).toBeTruthy();
    expect(screen.getByText('实时确认')).toBeTruthy();
    expect(screen.queryByText('京杭大运河')).toBeNull();

    fireEvent.click(screen.getByRole('tab', { name: 'Day 2' }));

    const dayTwoTimeline = screen.getByLabelText('Day 2 行程');
    expect(dayTwoTimeline.textContent).toContain('京杭大运河');
    expect(dayTwoTimeline.textContent).not.toContain('西湖');
  });

  it('shows route details between adjacent activities', () => {
    renderWorkspace({ data: { ...data, days: structuredDays } });

    const route = screen.getByLabelText('前往下一站：出租车');
    expect(route.textContent).toContain('出租车');
    expect(route.textContent).toContain('2.4 公里');
    expect(route.textContent).toContain('18 分钟');
    expect(route.textContent).toContain('¥12');
    expect(route.textContent).toContain('可信度：参考资料');
  });

  it('keeps structured activity location actions compatible with map callbacks', () => {
    const onSelectGroup = vi.fn();
    const onSelectLocation = vi.fn();
    const onFocusMap = vi.fn();
    renderWorkspace({
      data: { ...data, days: structuredDays },
      onSelectGroup,
      onSelectLocation,
      onFocusMap,
    });

    fireEvent.click(screen.getByLabelText('在地图中查看西湖'));

    expect(onSelectGroup).toHaveBeenCalledWith('structured_trip_workspace_day_1');
    expect(onSelectLocation).toHaveBeenCalledWith('west-lake');
    expect(onFocusMap).toHaveBeenCalledTimes(1);
  });

  it('renders the fixed five tabs and identifies current and historical groups', () => {
    renderWorkspace();

    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      '行程',
      '地图分组',
      '预算',
      '来源',
      '校验',
    ]);

    fireEvent.click(screen.getByRole('tab', { name: '地图分组' }));

    expect(screen.getByText('当前方案')).toBeTruthy();
    expect(screen.getByText('历史回答')).toBeTruthy();
    expect(screen.getAllByText('1 个地点')).toHaveLength(2);
    expect(screen.getByText('Day 1 · 当前方案')).toBeTruthy();
    expect(screen.getByText('Day 1 · 历史回答')).toBeTruthy();
  });

  it('uses only parent callbacks when selecting a map group location', () => {
    const onSelectGroup = vi.fn();
    const onSelectLocation = vi.fn();
    const onFocusMap = vi.fn();
    const openSpy = vi.spyOn(window, 'open');
    renderWorkspace({ onSelectGroup, onSelectLocation, onFocusMap });

    fireEvent.click(screen.getByRole('tab', { name: '地图分组' }));
    fireEvent.click(screen.getByRole('button', { name: /西湖/ }));

    expect(onSelectGroup).toHaveBeenCalledWith('structured_trip_workspace_day_1');
    expect(onSelectLocation).toHaveBeenCalledWith('west-lake');
    expect(onFocusMap).toHaveBeenCalledTimes(1);
    expect(openSpy).not.toHaveBeenCalled();
    openSpy.mockRestore();
  });

  it('filters groups through the controlled parent callback', () => {
    const onSelectGroup = vi.fn();
    renderWorkspace({ onSelectGroup });

    fireEvent.click(screen.getByRole('tab', { name: '地图分组' }));
    fireEvent.click(screen.getByText('Day 1 · 历史回答'));

    expect(onSelectGroup).toHaveBeenCalledWith('group_1_history_day_1');
  });

  it('renders the active group and selected location from controlled props', () => {
    renderWorkspace({
      activeGroupId: 'group_1_history_day_1',
      selectedLocationId: 'lingyin-temple',
    });

    fireEvent.click(screen.getByRole('tab', { name: '地图分组' }));

    expect(screen.queryByRole('button', { name: /西湖/ })).toBeNull();
    expect(screen.getByRole('button', { name: /灵隐寺/ }).getAttribute('aria-pressed')).toBe('true');
  });

  it('links the itinerary map action to the controlled selection', () => {
    const onSelectGroup = vi.fn();
    const onSelectLocation = vi.fn();
    const onFocusMap = vi.fn();
    const openSpy = vi.spyOn(window, 'open');
    renderWorkspace({ onSelectGroup, onSelectLocation, onFocusMap });

    fireEvent.click(screen.getByLabelText('在地图中查看西湖'));

    expect(onSelectGroup).toHaveBeenCalledWith('structured_trip_workspace_day_1');
    expect(onSelectLocation).toHaveBeenCalledWith('west-lake');
    expect(onFocusMap).toHaveBeenCalledTimes(1);
    expect(openSpy).not.toHaveBeenCalled();
    openSpy.mockRestore();
  });

  it('keeps existing quick actions wired to replanning prompts', () => {
    const onQuickAction = vi.fn();
    renderWorkspace({ onQuickAction });

    fireEvent.click(screen.getByRole('button', { name: /放慢节奏/ }));

    expect(onQuickAction).toHaveBeenCalledTimes(1);
    expect(onQuickAction.mock.calls[0][0]).toContain('直接更新当前旅行规划');
  });

  it('uses deterministic edit callbacks for structured activity deletion and undo', () => {
    const onEditOperation = vi.fn();
    const onUndoEdit = vi.fn();
    renderWorkspace({
      data: { ...data, days: structuredDays },
      onEditOperation,
      onUndoEdit,
      canUndoEdit: true,
    });

    fireEvent.click(screen.getByLabelText('删除西湖'));
    fireEvent.click(screen.getByRole('button', { name: '删除并更新' }));
    fireEvent.click(screen.getByLabelText('撤销上一次行程修改'));

    expect(onEditOperation).toHaveBeenCalledWith('remove_activity', {
      activity_id: 'activity-west-lake',
    });
    expect(onUndoEdit).toHaveBeenCalledTimes(1);
  });
});
