import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import TripWorkspace, {
  TripWorkspaceLocationGroup,
  TripWorkspaceState,
} from './TripWorkspace';
import type { TripDay } from './tripViewModel';
import formalFixture from '../../../backend/tests/fixtures/trip_v3_domestic_3d.json';
import { restoreFormalSnapshotState } from '../features/travel/state/formalSnapshotRestore';

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
        duration_minutes: 90,
        fixed_time: true,
        title: '环湖漫步',
        place: {
          name: '西湖',
          category: '自然风光',
          lat: 30.2501,
          lng: 120.1536,
          data_type: 'confirmed_live_data',
        },
        images: [{
          image_id: 'west-lake-image',
          url: 'https://example.com/west-lake.jpg',
          alt: '西湖图片',
          display_allowed: true,
          export_allowed: false,
          attribution_required: false,
          source_ref: 'fixture-west-lake-image',
        }],
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
        duration_minutes: null,
        fixed_time: false,
        title: '参观古刹',
        place: {
          name: '灵隐寺',
          category: '人文古迹',
          lat: 30.2409,
          lng: 120.1014,
        },
        images: [],
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
        duration_minutes: null,
        fixed_time: false,
        title: '乘船体验',
        place: { name: '京杭大运河', category: '城市人文' },
        images: [],
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
  it('explains an empty transport day using its saved transfer anchors', () => {
    renderWorkspace({
      data: { ...data, days: [{ ...structuredDays[0], activities: [] }] },
      document: { schema_version: '3.0', itinerary: { days: [{ day: 1, anchors: [
        { display_label: '从规划住宿点出发' }, { display_label: '前往杭州南站离开' },
      ] }] } },
    });
    fireEvent.click(screen.getByRole('tab', { name: '日程' }));
    expect(screen.getByText('当天未安排游览活动；接驳：从规划住宿点出发 → 前往杭州南站离开。')).toBeTruthy();
  });

  it('shows a date-flexible reference notice and hides time-sensitive transport sections', () => {
    const flexibleDocument = JSON.parse(JSON.stringify(formalFixture));
    flexibleDocument.intent.date_mode = 'flexible';
    flexibleDocument.intent.start_date = null;
    flexibleDocument.intent.end_date = null;
    flexibleDocument.itinerary.days.forEach((day: Record<string, unknown>) => {
      day.date = null;
    });
    flexibleDocument.outbound_transport.options = [];
    flexibleDocument.return_transport.options = [];

    renderWorkspace({ document: flexibleDocument });

    expect(screen.getByText(/当前为日期待定参考方案/)).toBeTruthy();
    expect(screen.queryByRole('heading', { name: '2. 去程交通' })).toBeNull();
    expect(screen.queryByRole('heading', { name: '5. 返程交通' })).toBeNull();
  });

  it('renders explicit budget categories, unknown costs and overrun warning', () => {
    renderWorkspace({ data: { ...data, budget: {
      currency: 'CNY', budget_total: 1000, budget_per_person: 500, people_count: 2,
      known_total: 1260, unknown_count: 2, unknown_items: ['夜间交通', '行李寄存'],
      categories: { transport: 200, accommodation: 600, food: 240, tickets: 180, other: 40 },
      currency_breakdown: [{ category: 'activities', amount: { amount: 15000, currency: 'JPY', cny_reference_amount: 750, exchange_rate_as_of: '2026-07-28' } }],
      traveler_costs: [
        { traveler_type: 'child', count: 1, estimated_total: { amount: 250, currency: 'CNY' }, pricing_status: 'official_discount_verified' },
        { traveler_type: 'senior', count: 0, estimated_total: null, pricing_status: 'not_applicable' },
      ],
      over_budget: true, overrun_amount: 260, source_label: '基于地点类别估算', updated_at: '2026-07-14T08:00:00Z', data_type: 'estimated_data',
    } } });
    expect(screen.getAllByText('¥1,000')).toHaveLength(2);
    expect(screen.getByText('¥600')).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toContain('¥260');
    expect(screen.getByText(/活动门票：JPY 15,000（约¥750，汇率参考日期 2026-07-28）/)).toBeTruthy();
    expect(screen.getByText(/儿童 1 人：¥250（官方优惠）/)).toBeTruthy();
    expect(screen.queryByText(/老人 0 人：/)).toBeNull();
    expect(screen.getByText(/基于地点类别估算/)).toBeTruthy();
  });

  it('summarizes the formal plan and opens the selected day from overview', () => {
    renderWorkspace({
      data: {
        ...data,
        days: structuredDays,
        budget: { currency: 'CNY', budget_total: 3600 },
      },
      document: {
        intent: { days: 2 },
        notes: [{ note_id: 'trip-note', scope: 'trip', content: '午后预留一段机动时间。' }],
      },
    });

    expect(screen.getByText('2 天 · 3 项活动')).toBeTruthy();
    expect(screen.getByText('当前正式方案')).toBeTruthy();
    expect(screen.getByText('午后预留一段机动时间。')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '查看 Day 2 日程' }));

    expect(screen.getByLabelText('Day 2 行程')).toBeTruthy();
    expect(screen.getByText('京杭大运河')).toBeTruthy();
  });

  it('keeps checklist and reservation reminders in their own fixed views', () => {
    renderWorkspace({
      hasDraft: true,
      document: {
        status: 'draft',
        action_items: [
          { action_id: 'pack', kind: 'checklist', text: '准备雨具', status: 'pending' },
          { action_id: 'reserve', kind: 'reservation_reminder', text: '确认博物馆预约', status: 'pending' },
        ],
        friendly_reminders: { items: [{ category: 'weather', content: '出发前关注降雨预报。' }] },
      },
    });

    expect(screen.getByText('发现未应用修改')).toBeTruthy();
    fireEvent.click(screen.getByRole('tab', { name: '准备清单' }));
    expect(screen.getByText('准备雨具')).toBeTruthy();
    expect(screen.queryByText('确认博物馆预约')).toBeNull();
    fireEvent.click(screen.getByRole('tab', { name: '提醒' }));
    expect(screen.getByText('确认博物馆预约')).toBeTruthy();
    expect(screen.getByText('出发前关注降雨预报。')).toBeTruthy();
  });

  it('renders structured days as date tabs with one-day activity details', () => {
    renderWorkspace({ data: { ...data, days: structuredDays } });
    fireEvent.click(screen.getByRole('tab', { name: '日程' }));

    expect(screen.getByRole('tab', { name: '2026-08-01 · Day 1' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'Day 2' })).toBeTruthy();
    expect(screen.getByLabelText('Day 1 行程')).toBeTruthy();
    expect(screen.getByText('09:00-10:30')).toBeTruthy();
    expect(screen.getByText('停留 1小时30分钟')).toBeTruthy();
    expect(screen.getByText('西湖')).toBeTruthy();
    expect(screen.getByText('环湖漫步')).toBeTruthy();
    expect(screen.getByText('自然风光')).toBeTruthy();
    expect(screen.getByText('免费')).toBeTruthy();
    expect(screen.getByText('固定时间')).toBeTruthy();
    expect(screen.getByText('备注')).toBeTruthy();
    expect(screen.getByRole('img', { name: '西湖图片' })).toBeTruthy();
    expect(screen.queryByText('京杭大运河')).toBeNull();

    fireEvent.click(screen.getByRole('tab', { name: 'Day 2' }));

    const dayTwoTimeline = screen.getByLabelText('Day 2 行程');
    expect(dayTwoTimeline.textContent).toContain('京杭大运河');
    expect(dayTwoTimeline.textContent).not.toContain('西湖');
  });

  it('shows route details between adjacent activities', () => {
    renderWorkspace({ data: { ...data, days: structuredDays } });
    fireEvent.click(screen.getByRole('tab', { name: '日程' }));

    const route = screen.getByLabelText('前往下一站：出租车');
    expect(route.textContent).toContain('出租车');
    expect(route.textContent).toContain('2.4 公里');
    expect(route.textContent).toContain('18 分钟');
    expect(route.textContent).not.toContain('¥12');
    expect(route.textContent).not.toContain('可信度');
  });

  it('only displays route cost when the route is live-confirmed', () => {
    const daysWithConfirmedRoute = structuredDays.map((day) => ({
      ...day,
      activities: day.activities.map((activity, index) => (
        index === 0 && activity.route_to_next
          ? {
              ...activity,
              route_to_next: { ...activity.route_to_next, data_type: 'confirmed_live_data' },
            }
          : activity
      )),
    }));
    renderWorkspace({ data: { ...data, days: daysWithConfirmedRoute } });
    fireEvent.click(screen.getByRole('tab', { name: '日程' }));

    expect(screen.getByLabelText('前往下一站：出租车').textContent).toContain('¥12');
  });

  it('marks a missing adjacent route as needing confirmation without drawing a route', () => {
    const daysWithoutRoute = structuredDays.map((day) => ({
      ...day,
      activities: day.activities.map((activity, index) => (
        index === 0 ? { ...activity, route_to_next: null } : activity
      )),
    }));
    renderWorkspace({ data: { ...data, days: daysWithoutRoute } });
    fireEvent.click(screen.getByRole('tab', { name: '日程' }));

    expect(screen.getByLabelText('前往下一站：路线信息待确认')).toBeTruthy();
    expect(screen.getByText('路线信息待确认')).toBeTruthy();
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
    fireEvent.click(screen.getByRole('tab', { name: '日程' }));

    fireEvent.click(screen.getByLabelText('在地图中查看西湖'));

    expect(onSelectGroup).toHaveBeenCalledWith('structured_trip_workspace_day_1');
    expect(onSelectLocation).toHaveBeenCalledWith('west-lake');
    expect(onFocusMap).toHaveBeenCalledTimes(1);
  });

  it('opens an activity detail, focuses its map marker, and restores the originating day', () => {
    const onSelectGroup = vi.fn();
    const onSelectLocation = vi.fn();
    const onFocusMap = vi.fn();
    const onDetailContextChange = vi.fn();
    const { props, rerender } = renderWorkspace({
      data: { ...data, days: structuredDays },
      onSelectGroup,
      onSelectLocation,
      onFocusMap,
      onDetailContextChange,
    });
    fireEvent.click(screen.getByRole('tab', { name: '日程' }));

    fireEvent.click(screen.getByRole('button', { name: '查看环湖漫步详情并定位地图' }));

    expect(screen.getByLabelText('西湖详情')).toBeTruthy();
    expect(onSelectGroup).toHaveBeenCalledWith('structured_trip_workspace_day_1');
    expect(onSelectLocation).toHaveBeenCalledWith('west-lake');
    expect(onFocusMap).toHaveBeenCalledTimes(1);
    expect(onDetailContextChange).toHaveBeenLastCalledWith(expect.objectContaining({
      activityId: 'activity-west-lake',
      dayId: 'day-1',
      locationId: 'west-lake',
      label: '返回环湖漫步',
    }));

    fireEvent.click(screen.getByRole('button', { name: '在地图中查看' }));
    expect(onFocusMap).toHaveBeenCalledTimes(2);

    rerender(<TripWorkspace {...props} returnToDetailRequestId={1} />);

    expect(screen.getByLabelText('Day 1 行程')).toBeTruthy();
    expect(onDetailContextChange).toHaveBeenLastCalledWith(null);
  });

  it('keeps a mobile activity detail in the itinerary until its explicit map action, then restores the card', async () => {
    const onSelectGroup = vi.fn();
    const onSelectLocation = vi.fn();
    const onFocusMap = vi.fn();
    const onDetailContextChange = vi.fn();
    const scrollIntoView = vi.fn();
    const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = scrollIntoView;
    try {
      const { props, rerender } = renderWorkspace({
        data: { ...data, days: structuredDays },
        mobileSimpleEditing: true,
        onSelectGroup,
        onSelectLocation,
        onFocusMap,
        onDetailContextChange,
      });
      fireEvent.click(screen.getByRole('tab', { name: '日程' }));
      fireEvent.click(screen.getByRole('button', { name: '查看环湖漫步详情并定位地图' }));

      expect(screen.getByLabelText('西湖详情')).toBeTruthy();
      expect(document.querySelector('.trip-workspace')?.className).toContain('trip-workspace--mobile-detail');
      expect(screen.getByText('Day 1 · 活动详情')).toBeTruthy();
      expect(onFocusMap).not.toHaveBeenCalled();

      fireEvent.click(screen.getByRole('button', { name: '在地图中查看' }));
      expect(onSelectGroup).toHaveBeenCalledWith('structured_trip_workspace_day_1');
      expect(onSelectLocation).toHaveBeenCalledWith('west-lake');
      expect(onFocusMap).toHaveBeenCalledTimes(1);

      rerender(<TripWorkspace {...props} returnToDetailRequestId={1} />);

      await waitFor(() => expect(screen.getByLabelText('Day 1 行程')).toBeTruthy());
      await waitFor(() => expect(scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'nearest' }));
      expect(onDetailContextChange).toHaveBeenLastCalledWith(null);
      expect(document.activeElement).toBe(screen.getByRole('button', { name: '查看环湖漫步详情并定位地图' }));
    } finally {
      HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
    }
  });

  it('switches to and highlights the matching itinerary card when a map marker is selected', () => {
    const workspaceData = { ...data, days: structuredDays, locations: [currentLocation, historicalLocation] };
    const { props, rerender } = renderWorkspace({ data: workspaceData });

    rerender(<TripWorkspace {...props} data={workspaceData} selectedLocationId="lingyin-temple" />);

    expect(screen.getByLabelText('Day 1 行程')).toBeTruthy();
    expect(screen.getByText('参观古刹')).toBeTruthy();
    expect(document.querySelector('[data-activity-id="activity-lingyin"]')?.className)
      .toContain('trip-activity-card--selected');
  });

  it('renders the approved five fixed workbench tabs', () => {
    renderWorkspace();

    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      '概览',
      '日程',
      '候选',
      '准备清单',
      '提醒',
    ]);
  });

  it('links the itinerary map action to the controlled selection', () => {
    const onSelectGroup = vi.fn();
    const onSelectLocation = vi.fn();
    const onFocusMap = vi.fn();
    const openSpy = vi.spyOn(window, 'open');
    renderWorkspace({ onSelectGroup, onSelectLocation, onFocusMap });
    fireEvent.click(screen.getByRole('tab', { name: '日程' }));

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
    fireEvent.click(screen.getByRole('tab', { name: '日程' }));

    fireEvent.click(screen.getByLabelText('删除西湖'));
    fireEvent.click(screen.getByRole('button', { name: '删除并更新' }));
    fireEvent.click(screen.getByLabelText('撤销上一次行程修改'));

    expect(onEditOperation).toHaveBeenCalledWith('remove_activity', {
      activity_id: 'activity-west-lake',
    });
    expect(onUndoEdit).toHaveBeenCalledTimes(1);
  });

  it('limits mobile editing to same-day ordering, fixed time, notes, and deletion', () => {
    const document = JSON.parse(JSON.stringify(formalFixture));
    const firstDay = document.itinerary.days[0];
    const secondActivity = {
      ...firstDay.activities[0],
      activity_id: 'act_hz_1_mobile_second',
      title: '移动端第二项',
      order: 1,
      place: {
        ...firstDay.activities[0].place,
        poi_id: 'poi_hz_mobile_second',
        name: '移动端第二项',
      },
    };
    firstDay.activities.push(secondActivity);
    const restored = restoreFormalSnapshotState({ current: { revision: 1, document } });
    const onEditOperation = vi.fn();

    render(
      <TripWorkspace
        data={restored!.workspace}
        document={restored!.document}
        onEditOperation={onEditOperation}
        onGetActivityDeleteImpact={vi.fn().mockResolvedValue(null)}
        mobileSimpleEditing
      />,
    );
    fireEvent.click(screen.getByRole('tab', { name: '日程' }));

    expect(screen.queryByLabelText('替换移动端第二项')).toBeNull();
    expect(screen.queryByLabelText('将移动端第二项换到另一天')).toBeNull();
    expect(screen.queryByRole('button', { name: '优化 Day 1 当天路线' })).toBeNull();
    fireEvent.click(screen.getByLabelText('将移动端第二项上移一位'));
    expect(onEditOperation).toHaveBeenCalledWith('move_activity', {
      activity_id: 'act_hz_1_mobile_second', target_day: 1, position: 0,
    });

    fireEvent.click(screen.getByLabelText('编辑移动端第二项的备注或固定状态'));
    expect(screen.getByText('添加活动备注')).toBeTruthy();
    expect(screen.queryByText('提前 15 分钟')).toBeNull();
    expect(screen.queryByText('移入候选')).toBeNull();

    fireEvent.click(screen.getByText('添加活动备注'));
    fireEvent.change(screen.getByLabelText('移动端第二项活动备注'), { target: { value: '预约后电话确认' } });
    fireEvent.click(screen.getByRole('button', { name: '保存备注' }));
    expect(onEditOperation).toHaveBeenCalledWith('update_activity_note', {
      activity_id: 'act_hz_1_mobile_second', content: '预约后电话确认',
    });

    fireEvent.click(screen.getByRole('tab', { name: '候选' }));
    expect(screen.queryByLabelText(`将${document.candidate_pool[0].place.name}排入行程`)).toBeNull();
  });

  it('shows an unapplied-draft state and lets the user discard it deliberately', () => {
    const onDiscardDraft = vi.fn();
    renderWorkspace({
      hasDraft: true,
      onDiscardDraft,
    });

    expect(screen.getByText('发现未应用修改')).not.toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '放弃草稿' }));
    fireEvent.click(screen.getAllByRole('button', { name: '放弃草稿' })[1]);

    expect(onDiscardDraft).toHaveBeenCalledTimes(1);
  });

  it('applies a saveable draft only after the user confirms the batch action', () => {
    const onApplyDraft = vi.fn().mockResolvedValue(true);
    renderWorkspace({
      hasDraft: true,
      draftValidation: { can_apply: true, status: 'ready' },
      onApplyDraft,
    });

    fireEvent.click(screen.getByRole('button', { name: '应用修改' }));
    fireEvent.click(screen.getAllByRole('button', { name: '应用修改' })[1]);

    expect(onApplyDraft).toHaveBeenCalledTimes(1);
  });

  it('offers exactly the single formal-version restore action when no draft is open', () => {
    const onRestorePreviousFormal = vi.fn().mockResolvedValue(true);
    renderWorkspace({
      canRestorePreviousFormal: true,
      onRestorePreviousFormal,
    });

    expect(screen.getByText('当前正式方案')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '恢复上一版本' }));
    fireEvent.click(screen.getAllByRole('button', { name: '恢复上一版本' })[1]);

    expect(onRestorePreviousFormal).toHaveBeenCalledTimes(1);
  });

  it('shows a blocked draft and focuses its first hard error', () => {
    renderWorkspace({
      data: { ...data, days: structuredDays },
      hasDraft: true,
      draftValidation: {
        can_apply: false,
        status: 'blocked',
        hard_errors: [{
          code: 'FIXED_TIME_CONFLICT',
          message: '固定时间“西湖漫步”与当天其他活动冲突。',
          target_id: 'activity-west-lake',
        }],
      },
    });

    expect(screen.getByText('草稿需修正')).toBeTruthy();
    fireEvent.click(screen.getByRole('tab', { name: '提醒' }));
    expect(screen.getByText(/固定时间“西湖漫步”与当天其他活动冲突/)).toBeTruthy();
    expect(screen.getByLabelText('Day 1 行程')).toBeTruthy();
  });

  it('identifies a degraded draft as saveable pending external data', () => {
    renderWorkspace({
      hasDraft: true,
      draftValidation: {
        can_apply: true,
        status: 'degraded',
        soft_warnings: [{ code: 'ROUTE_DATA_PENDING', message: 'Day 1 的真实路线待确认。' }],
      },
    });

    expect(screen.getByText('草稿含待确认信息')).toBeTruthy();
    fireEvent.click(screen.getByRole('tab', { name: '提醒' }));
    expect(screen.getByText('Day 1 的真实路线待确认。')).toBeTruthy();
  });

  it('renders V3 formal transport, lodging and itinerary without treating objects as React children', () => {
    const restored = restoreFormalSnapshotState({
      current: { revision: 1, document: formalFixture },
    });
    expect(restored).not.toBeNull();

    render(<TripWorkspace data={restored!.workspace} document={restored!.document} />);

    expect(screen.getByText('上海虹桥站 → 杭州东站')).not.toBeNull();
    expect(screen.getByText(/08:00-09:00 · ¥100/)).not.toBeNull();
    expect(screen.getByText('杭州契约样本住宿')).not.toBeNull();
    fireEvent.click(screen.getByRole('tab', { name: '日程' }));
    expect(screen.getByText('杭州契约景点一')).not.toBeNull();
  });

  it('emits deterministic V3 callbacks for candidate, 15-minute, fixed-time and note edits', () => {
    const document = JSON.parse(JSON.stringify(formalFixture));
    const restored = restoreFormalSnapshotState({ current: { revision: 1, document } });
    const onEditOperation = vi.fn();
    const activity = document.itinerary.days[0].activities[0];
    const candidate = document.candidate_pool[0];

    render(<TripWorkspace data={restored!.workspace} document={restored!.document} onEditOperation={onEditOperation} />);
    fireEvent.click(screen.getByRole('tab', { name: '日程' }));

    fireEvent.click(screen.getByLabelText(`编辑${activity.place.name}的时间、备注或候选状态`));
    fireEvent.click(screen.getByText('延后 15 分钟'));
    expect(onEditOperation).toHaveBeenCalledWith('shift_activity_time', { activity_id: activity.activity_id, delta_minutes: 15 });

    fireEvent.click(screen.getByLabelText(`编辑${activity.place.name}的时间、备注或候选状态`));
    fireEvent.click(screen.getByText('固定当前时间'));
    expect(onEditOperation).toHaveBeenCalledWith('set_activity_fixed_time', { activity_id: activity.activity_id, fixed_time: true });

    fireEvent.click(screen.getByLabelText(`编辑${activity.place.name}的时间、备注或候选状态`));
    fireEvent.click(screen.getByText('添加活动备注'));
    fireEvent.change(screen.getByLabelText(`${activity.title}活动备注`), { target: { value: '提前到场核验预约' } });
    fireEvent.click(screen.getByRole('button', { name: '保存备注' }));
    expect(onEditOperation).toHaveBeenCalledWith('update_activity_note', {
      activity_id: activity.activity_id,
      content: '提前到场核验预约',
    });

    fireEvent.click(screen.getByLabelText(`编辑${activity.place.name}的时间、备注或候选状态`));
    fireEvent.click(screen.getByText('移入候选'));
    expect(onEditOperation).toHaveBeenCalledWith('move_activity_to_candidate', { activity_id: activity.activity_id });

    fireEvent.click(screen.getByRole('tab', { name: '候选' }));
    fireEvent.click(screen.getByLabelText(`将${candidate.place.name}排入行程`));
    fireEvent.click(screen.getByText('加入 Day 2 · 11:30-13:30'));
    expect(onEditOperation).toHaveBeenCalledWith('insert_candidate', {
      activity_id: candidate.activity_id,
      target_day: 2,
    });
  });

  it('shows server-counted cascade impact before permanently deleting a V3 activity', async () => {
    const document = JSON.parse(JSON.stringify(formalFixture));
    const restored = restoreFormalSnapshotState({ current: { revision: 1, document } });
    const activity = document.itinerary.days[0].activities[0];
    const onEditOperation = vi.fn().mockResolvedValue(true);
    const onGetActivityDeleteImpact = vi.fn().mockResolvedValue({
      activity_id: activity.activity_id,
      activity_title: activity.title,
      notes: 1,
      reservation_reminders: 2,
      checklist_items: 3,
      image_references: 1,
      route_segments: 2,
      map_markers: 1,
    });

    render(
      <TripWorkspace
        data={restored!.workspace}
        document={restored!.document}
        onEditOperation={onEditOperation}
        onGetActivityDeleteImpact={onGetActivityDeleteImpact}
      />,
    );
    fireEvent.click(screen.getByRole('tab', { name: '日程' }));
    fireEvent.click(screen.getByLabelText(`永久删除${activity.place.name}`));

    expect(await screen.findByText(`永久删除“${activity.title}”？`)).toBeTruthy();
    expect(screen.getByText('预约提醒：2 条')).toBeTruthy();
    expect(screen.getByText('准备清单：3 条')).toBeTruthy();
    expect(screen.getByText('待重新核验路线段：2 条')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '永久删除' }));

    await waitFor(() => expect(onEditOperation).toHaveBeenCalledWith('delete_activity', {
      activity_id: activity.activity_id,
      confirmed: true,
    }));
  });

  it('previews and confirms a same-day route optimization without changing fixed activities', async () => {
    const document = JSON.parse(JSON.stringify(formalFixture));
    const restored = restoreFormalSnapshotState({ current: { revision: 1, document } });
    const activity = document.itinerary.days[0].activities[0];
    const onEditOperation = vi.fn().mockResolvedValue(true);
    const onGetDayRouteOptimizationPreview = vi.fn().mockResolvedValue({
      day: 1,
      can_optimize: true,
      current_order: [{ activity_id: activity.activity_id, title: activity.title, fixed_time: false }],
      optimized_order: [
        { activity_id: 'act-near', title: '优先安排的地点', fixed_time: false },
        { activity_id: 'act-fixed', title: '预约活动', fixed_time: true },
      ],
      fixed_activity_count: 1,
      order_changes: [{ activity_id: 'act-near', title: '优先安排的地点', before_order: 2, after_order: 1 }],
      time_changes: [{
        activity_id: 'act-near',
        title: '优先安排的地点',
        before: { start_at: '11:00', end_at: '12:00' },
        after: { start_at: '10:00', end_at: '11:00' },
      }],
      route_segments: 2,
    });

    render(
      <TripWorkspace
        data={restored!.workspace}
        document={restored!.document}
        onEditOperation={onEditOperation}
        onGetDayRouteOptimizationPreview={onGetDayRouteOptimizationPreview}
      />,
    );
    fireEvent.click(screen.getByRole('tab', { name: '日程' }));
    fireEvent.click(screen.getByRole('button', { name: '优化 Day 1 当天路线' }));

    expect(await screen.findByText('优化 Day 1 路线？')).toBeTruthy();
    expect(screen.getByText('预约活动（固定时间）')).toBeTruthy();
    expect(screen.getByText(/将调整 1 项顺序、1 项非固定时间；确认后 2 条真实路线段将重新核验/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '确认优化' }));

    await waitFor(() => expect(onEditOperation).toHaveBeenCalledWith('optimize_day_route', {
      day: 1,
      confirmed: true,
    }));
  });

  it('keeps an infeasible candidate in the pool and explains why it cannot be inserted', () => {
    const document = JSON.parse(JSON.stringify(formalFixture));
    document.candidate_pool[0].insertion_options = [];
    document.candidate_pool[0].insertion_unavailable_reason = 'Day 1: 与营业时间不匹配；Day 2: 预算余量不足';
    const restored = restoreFormalSnapshotState({ current: { revision: 1, document } });

    render(<TripWorkspace data={restored!.workspace} document={restored!.document} onEditOperation={vi.fn()} />);
    fireEvent.click(screen.getByRole('tab', { name: '候选' }));

    expect(screen.getByText('Day 1: 与营业时间不匹配；Day 2: 预算余量不足')).not.toBeNull();
    expect(screen.getByRole('button', { name: `将${document.candidate_pool[0].place.name}排入行程` }).hasAttribute('disabled')).toBe(true);
  });

  it('only offers existing V3 transport and lodging choices as selectable planning options', () => {
    const document = JSON.parse(JSON.stringify(formalFixture));
    const alternativeTransport = { ...document.outbound_transport.options[0], option_id: 'transport-alt', service_number: 'G999' };
    const alternativeLodging = {
      ...document.lodging_plan.options[0],
      lodging_id: 'lodging-alt',
      name: '杭州备选规划住宿',
      place: { ...document.lodging_plan.options[0].place, poi_id: 'poi-lodging-alt', name: '杭州备选规划住宿' },
    };
    document.outbound_transport.options.push(alternativeTransport);
    document.lodging_plan.options.push(alternativeLodging);
    const restored = restoreFormalSnapshotState({ current: { revision: 1, document } });
    const onEditOperation = vi.fn();

    render(<TripWorkspace data={restored!.workspace} document={restored!.document} onEditOperation={onEditOperation} />);

    fireEvent.click(screen.getByRole('button', { name: '设为主方案' }));
    expect(onEditOperation).toHaveBeenCalledWith('select_transport_option', {
      direction: 'outbound',
      option_id: 'transport-alt',
    });

    fireEvent.click(screen.getByRole('button', { name: '设为规划住宿点' }));
    expect(onEditOperation).toHaveBeenCalledWith('select_lodging_option', { lodging_id: 'lodging-alt' });
  });
});
