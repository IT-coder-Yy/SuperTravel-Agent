import React, { useCallback, useMemo, useState } from 'react';
import { Button, Dropdown, Empty, Popconfirm, Segmented, Tag, Tabs } from 'antd';
import {
  CompassOutlined,
  CalendarOutlined,
  CheckCircleOutlined,
  DeleteOutlined,
  DollarOutlined,
  EnvironmentOutlined,
  ExclamationCircleOutlined,
  LinkOutlined,
  SwapOutlined,
  TeamOutlined,
  UndoOutlined
} from '@ant-design/icons';
import {
  buildDeleteLocationPrompt,
  buildMoveLocationPrompt,
  buildReplaceLocationPrompt,
  formatTripDay,
  quickActionPrompts,
} from './TripWorkspaceActions';
import type { RouteLeg, TripActivity, TripDay } from './tripViewModel';

export interface TripWorkspaceLocation {
  id: string;
  name: string;
  lat: number;
  lng: number;
  description?: string;
  category?: string;
  day?: number | string;
  order?: number | string;
}

export interface TripWorkspaceSource {
  type?: string;
  title?: string;
  source?: string;
  url?: string;
  snippet?: string;
  data_type?: string;
  updated_at?: string;
  confidence?: number | null;
  related_fields?: string[];
  related_places?: string[];
}

export type TripWorkspaceLocationGroupSource = 'current_plan' | 'historical_answer';

export interface TripWorkspaceLocationGroup {
  groupId: string;
  title: string;
  locations: TripWorkspaceLocation[];
  sourceKind?: TripWorkspaceLocationGroupSource;
  userMessageId?: string;
  userPrompt?: string;
}

export interface TripWorkspaceBudget {
  currency?: string;
  budget_total?: number | null;
  budget_per_person?: number | null;
  people_count?: number | null;
  estimated_total?: number | null;
  confidence?: string;
  data_type?: string;
  known_total?: number | null;
  unknown_count?: number | null;
  unknown_items?: string[];
  categories?: Record<string, number>;
  over_budget?: boolean;
  overrun_amount?: number | null;
  source_label?: string;
  updated_at?: string;
  [key: string]: unknown;
}

export interface TripWorkspaceIssue {
  code?: string;
  message?: string;
  severity?: 'info' | 'warning' | 'error' | string;
  repair_hint?: string;
}

export interface TripWorkspaceValidation {
  valid?: boolean;
  issues?: TripWorkspaceIssue[];
}

export interface TripWorkspaceRepair {
  repaired?: boolean;
  attempted_issue_codes?: string[];
  resolved_issue_codes?: string[];
  remaining_issue_codes?: string[];
  notes?: string[];
  remaining_validation?: TripWorkspaceValidation | null;
}

export interface TripWorkspaceState {
  days?: TripDay[];
  locations: TripWorkspaceLocation[];
  sources: TripWorkspaceSource[];
  budget: TripWorkspaceBudget | null;
  validation: TripWorkspaceValidation | null;
  repair: TripWorkspaceRepair | null;
}

export interface TripWorkspaceProps {
  data: TripWorkspaceState;
  document?: Record<string, any> | null;
  locationGroups?: TripWorkspaceLocationGroup[];
  activeGroupId?: string;
  selectedLocationId?: string;
  onSelectGroup?: (groupId: string) => void;
  onSelectLocation?: (locationId: string) => void;
  onQuickAction?: (prompt: string) => void;
  onEditOperation?: (type: string, payload: Record<string, unknown>) => void;
  onUndoEdit?: () => void;
  canUndoEdit?: boolean;
  editLoading?: boolean;
  onFocusMap?: () => void;
}

const ALL_GROUPS_VALUE = '__all__';

const getLocationGroupSource = (
  group: TripWorkspaceLocationGroup,
): TripWorkspaceLocationGroupSource => {
  if (group.sourceKind) return group.sourceKind;
  return group.groupId.startsWith('structured_trip_workspace')
    ? 'current_plan'
    : 'historical_answer';
};

const getLocationGroupSourceLabel = (group: TripWorkspaceLocationGroup) => (
  getLocationGroupSource(group) === 'current_plan' ? '当前方案' : '历史回答'
);

const getLocationGroupFilterLabel = (group: TripWorkspaceLocationGroup) => {
  const dayMatch = group.title.match(/(?:第\s*(\d+)\s*天|Day\s*(\d+))/i);
  const dayNumber = dayMatch?.[1] || dayMatch?.[2];
  return dayNumber ? `Day ${dayNumber}` : group.title;
};

const formatMoney = (value: unknown, currency = 'CNY') => {
  const amount = Number(value);
  if (value === null || value === undefined || value === '' || !Number.isFinite(amount) || amount < 0) return '待确认';
  const symbol = currency === 'CNY' ? '¥' : `${currency} `;
  return `${symbol}${Math.round(amount).toLocaleString('zh-CN')}`;
};

const formatActivityCost = (value: number | null, currency = 'CNY') => {
  if (value === null || !Number.isFinite(value)) return null;
  if (value === 0) return '免费';
  return formatMoney(value, currency);
};

const parseTimeMinutes = (value: string | null) => {
  if (!value) return null;
  const match = value.match(/^(\d{1,2}):(\d{2})/);
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) return null;
  return hours * 60 + minutes;
};

const formatStayDuration = (startTime: string | null, endTime: string | null) => {
  const start = parseTimeMinutes(startTime);
  let end = parseTimeMinutes(endTime);
  if (start === null || end === null) return null;
  if (end < start) end += 24 * 60;
  const duration = end - start;
  if (duration <= 0) return null;
  const hours = Math.floor(duration / 60);
  const minutes = duration % 60;
  return `停留 ${hours > 0 ? `${hours}小时` : ''}${minutes > 0 ? `${minutes}分钟` : ''}`;
};

const formatDistance = (meters: number | null) => {
  if (meters === null || !Number.isFinite(meters) || meters < 0) return null;
  return meters >= 1000
    ? `${(meters / 1000).toFixed(meters >= 10000 ? 0 : 1)} 公里`
    : `${Math.round(meters)} 米`;
};

const routeModeLabels: Record<string, string> = {
  walk: '步行',
  walking: '步行',
  bicycle: '骑行',
  cycling: '骑行',
  drive: '驾车',
  driving: '驾车',
  taxi: '出租车',
  public_transit: '公共交通',
  transit: '公共交通',
  subway: '地铁',
  bus: '公交',
  train: '火车',
  flight: '飞机',
};

const formatRouteMode = (mode: string) => routeModeLabels[mode.toLowerCase()] || mode || '交通方式待定';

const severityColor = (severity?: string) => {
  if (severity === 'error') return 'red';
  if (severity === 'warning') return 'orange';
  return 'blue';
};

const dataTypePresentation: Record<string, { label: string; color: string }> = {
  confirmed_live_data: { label: '实时确认', color: 'green' },
  reference_data: { label: '参考资料', color: 'blue' },
  estimated_data: { label: '规划估算', color: 'gold' },
};

const renderDataTypeTag = (dataType?: string | null) => {
  const presentation = dataType ? dataTypePresentation[dataType] : undefined;
  if (!dataType) return null;
  return presentation
    ? <Tag color={presentation.color}>{presentation.label}</Tag>
    : <Tag>{dataType}</Tag>;
};

const getDataTypeLabel = (dataType?: string | null) => (
  dataType ? dataTypePresentation[dataType]?.label || dataType : null
);

const formatDayTabLabel = (day: TripDay) => (
  day.date ? `${day.date} · Day ${day.day}` : `Day ${day.day}`
);

const renderRouteLeg = (route: RouteLeg, currency: string) => {
  const distance = formatDistance(route.distance_meters);
  const cost = formatActivityCost(route.estimated_cost, currency);
  const credibility = getDataTypeLabel(route.data_type);
  return (
    <div
      className="trip-route-leg"
      aria-label={`前往下一站：${formatRouteMode(route.mode)}`}
      style={{
        display: 'flex',
        alignItems: 'center',
        flexWrap: 'wrap',
        gap: 6,
        marginLeft: 11,
        padding: '2px 0 2px 22px',
        borderLeft: '2px solid var(--travel-border)',
        color: 'var(--travel-muted)',
        fontSize: 12,
      }}
    >
      <SwapOutlined aria-hidden="true" />
      <strong style={{ color: 'var(--travel-ink)', fontSize: 12 }}>{formatRouteMode(route.mode)}</strong>
      {distance && <span>{distance}</span>}
      {route.duration_minutes !== null && <span>{Math.round(route.duration_minutes)} 分钟</span>}
      {cost && <span>{cost}</span>}
      {credibility && <span>可信度：{credibility}</span>}
    </div>
  );
};

const TripWorkspace: React.FC<TripWorkspaceProps> = ({
  data,
  document,
  locationGroups = [],
  activeGroupId,
  selectedLocationId,
  onSelectGroup,
  onSelectLocation,
  onQuickAction,
  onEditOperation,
  onUndoEdit,
  canUndoEdit = false,
  editLoading = false,
  onFocusMap,
}) => {
  const [activeDayId, setActiveDayId] = useState<string>();
  const tripDays = useMemo(
    () => [...(data.days || [])].sort((left, right) => left.day - right.day),
    [data.days],
  );
  const effectiveActiveDayId = tripDays.some((day) => day.id === activeDayId)
    ? activeDayId
    : tripDays[0]?.id;

  const groupedLocations = useMemo(() => {
    const groups = new Map<string, TripWorkspaceLocation[]>();
    data.locations.forEach((location) => {
      const key = formatTripDay(location.day);
      groups.set(key, [...(groups.get(key) || []), location]);
    });
    return Array.from(groups.entries()).map(([day, locations]) => ({
      day,
      locations: [...locations].sort((left, right) => {
        const leftOrder = Number(left.order);
        const rightOrder = Number(right.order);
        return (Number.isFinite(leftOrder) ? leftOrder : 999) - (Number.isFinite(rightOrder) ? rightOrder : 999);
      }),
    }));
  }, [data.locations]);

  const sendAction = useCallback((prompt: string) => {
    if (!onQuickAction) return;
    onQuickAction(prompt);
  }, [onQuickAction]);

  const activeLocationGroup = useMemo(
    () => locationGroups.find((group) => group.groupId === activeGroupId),
    [activeGroupId, locationGroups],
  );

  const visibleLocationGroups = activeLocationGroup ? [activeLocationGroup] : locationGroups;

  const groupFilterOptions = useMemo(() => {
    const baseLabels = locationGroups.map(getLocationGroupFilterLabel);
    return [
      { label: '全部', value: ALL_GROUPS_VALUE },
      ...locationGroups.map((group, index) => {
        const baseLabel = baseLabels[index];
        const hasDuplicateLabel = baseLabels.filter((label) => label === baseLabel).length > 1;
        return {
          label: hasDuplicateLabel
            ? `${baseLabel} · ${getLocationGroupSourceLabel(group)}`
            : baseLabel,
          value: group.groupId,
        };
      }),
    ];
  }, [locationGroups]);

  const selectLocationOnMap = useCallback((
    location: TripWorkspaceLocation,
    knownGroupId?: string,
  ) => {
    const matchingGroup = knownGroupId
      ? locationGroups.find((group) => group.groupId === knownGroupId)
      : locationGroups.find((group) => (
          getLocationGroupSource(group) === 'current_plan'
          && group.locations.some((candidate) => candidate.id === location.id)
        )) || locationGroups.find((group) => (
          group.locations.some((candidate) => candidate.id === location.id)
        ));

    if (matchingGroup) {
      onSelectGroup?.(matchingGroup.groupId);
    }
    onSelectLocation?.(location.id);
    onFocusMap?.();
  }, [locationGroups, onFocusMap, onSelectGroup, onSelectLocation]);

  const buildMoveMenuItems = useCallback((location: TripWorkspaceLocation) => {
    const sourceDay = formatTripDay(location.day);
    const targetDays = tripDays.length > 0
      ? tripDays.map((day) => formatTripDay(day.day))
      : groupedLocations.map((group) => group.day);
    return [
      { key: '__auto__', label: '智能选择合适日期' },
      ...targetDays
        .filter((day) => day !== sourceDay && day !== '未分组')
        .map((day) => ({ key: day, label: `移到${day}` })),
    ];
  }, [groupedLocations, tripDays]);

  const getActivityLocation = useCallback((activity: TripActivity, index: number): TripWorkspaceLocation => {
    const placeName = activity.place?.name || activity.title || `活动 ${index + 1}`;
    const matchedLocation = data.locations.find((location) => (
      location.id === activity.id
      || location.name.trim().toLocaleLowerCase() === placeName.trim().toLocaleLowerCase()
    ));
    return matchedLocation || {
      id: activity.id,
      name: placeName,
      lat: typeof activity.place?.lat === 'number' ? activity.place.lat : Number.NaN,
      lng: typeof activity.place?.lng === 'number' ? activity.place.lng : Number.NaN,
      description: activity.title || undefined,
      category: activity.place?.category,
      day: activity.day,
      order: index + 1,
    };
  }, [data.locations]);

  const renderLocationActions = useCallback((location: TripWorkspaceLocation, activityId?: string) => (
    <div className="trip-location-actions">
      <Button
        size="small"
        type="text"
        icon={<EnvironmentOutlined />}
        onClick={() => selectLocationOnMap(location)}
        disabled={!Number.isFinite(location.lat) || !Number.isFinite(location.lng)}
        aria-label={`在地图中查看${location.name}`}
        title="在地图中查看"
      />
      <Button
        size="small"
        type="text"
        icon={<SwapOutlined />}
        disabled={!onQuickAction}
        onClick={() => sendAction(buildReplaceLocationPrompt(location))}
        aria-label={`替换${location.name}`}
        title="替换地点"
      />
      <Dropdown
        disabled={!onQuickAction && !onEditOperation}
        trigger={['click']}
        menu={{
          items: buildMoveMenuItems(location),
          onClick: ({ key }) => {
            const targetDay = Number(String(key).match(/\d+/)?.[0]);
            if (activityId && onEditOperation && key !== '__auto__' && Number.isInteger(targetDay)) {
              onEditOperation('move_activity', { activity_id: activityId, target_day: targetDay });
              return;
            }
            sendAction(buildMoveLocationPrompt(location, key === '__auto__' ? undefined : key));
          },
        }}
      >
        <Button
          size="small"
          type="text"
          icon={<CalendarOutlined />}
          disabled={!onQuickAction && !onEditOperation}
          aria-label={`将${location.name}换到另一天`}
          title="换到另一天"
        />
      </Dropdown>
      <Popconfirm
        title={`删除“${location.name}”？`}
        description="将发送指令并重新平衡当天行程。"
        okText="删除并更新"
        cancelText="取消"
        disabled={!onQuickAction && !onEditOperation}
        onConfirm={() => {
          if (activityId && onEditOperation) {
            onEditOperation('remove_activity', { activity_id: activityId });
            return;
          }
          sendAction(buildDeleteLocationPrompt(location));
        }}
      >
        <Button
          size="small"
          type="text"
          danger
          icon={<DeleteOutlined />}
          disabled={!onQuickAction && !onEditOperation}
          aria-label={`删除${location.name}`}
          title="删除地点"
        />
      </Popconfirm>
    </div>
  ), [buildMoveMenuItems, onEditOperation, onQuickAction, selectLocationOnMap, sendAction]);

  const issues = data.validation?.issues || [];
  const currency = data.budget?.currency || 'CNY';
  const resolvedIssueCount = data.repair?.resolved_issue_codes?.length || 0;
  const remainingIssueCount = Math.max(
    data.repair?.remaining_issue_codes?.length || 0,
    data.repair?.remaining_validation?.issues?.length || 0,
    data.repair ? issues.length : 0,
  );
  const hasAutomaticRepair = Boolean(data.repair?.repaired || resolvedIssueCount > 0);
  const needsConfirmation = remainingIssueCount > 0;
  const hasRepairStatus = hasAutomaticRepair || needsConfirmation;
  const transportSummary = (section: Record<string, any> | undefined) => {
    if (!section?.options?.length) return <p>{section?.status_reason || '暂无可靠实时数据'}</p>;
    return section.options.slice(0, 3).map((option: Record<string, any>) => (
      <div className="trip-product-summary-row" key={option.option_id}>
        <strong>{option.mode} {option.service_number || ''}</strong>
        <span>{option.departure_station || '待确认'} → {option.arrival_station || '待确认'}</span>
        <small>{option.departure_time || '待确认'}-{option.arrival_time || '待确认'} · {formatMoney(option.price, option.currency || 'CNY')} · {option.availability || 'unknown'}</small>
      </div>
    ));
  };

  return (
    <section className="trip-workspace">
      <div className="trip-workspace-header">
        <div>
          <h3>旅行工作台</h3>
          <span className="trip-workspace-subtitle">结构化行程</span>
        </div>
        <div className="trip-workspace-stats">
          <Button
            size="small"
            type="text"
            icon={<UndoOutlined />}
            disabled={!canUndoEdit || editLoading}
            loading={editLoading}
            onClick={onUndoEdit}
            aria-label="撤销上一次行程修改"
            title="撤销上一次行程修改"
          />
          <span>{data.locations.length} 地点</span>
          <span>{data.sources.length} 来源</span>
          <span>{issues.length} 校验</span>
        </div>
      </div>

      <div className="trip-workspace-actions">
        <Button size="small" disabled={!onQuickAction} icon={<CalendarOutlined />} onClick={() => sendAction(quickActionPrompts.slow_down)}>
          放慢节奏
        </Button>
        <Button size="small" disabled={!onQuickAction} icon={<DollarOutlined />} onClick={() => sendAction(quickActionPrompts.lower_budget)}>
          降低预算
        </Button>
        <Button size="small" disabled={!onQuickAction} icon={<TeamOutlined />} onClick={() => sendAction(quickActionPrompts.family_friendly)}>
          增加亲子友好
        </Button>
        <Button size="small" disabled={!onQuickAction} icon={<CompassOutlined />} onClick={() => sendAction(quickActionPrompts.hidden_gems)}>
          增加小众路线
        </Button>
        <Button size="small" disabled={!onQuickAction} icon={<SwapOutlined />} onClick={() => sendAction(quickActionPrompts.reduce_commute)}>
          减少通勤
        </Button>
      </div>

      <Tabs
        size="small"
        className="trip-workspace-tabs"
        items={[
          ...(document ? [{
            key: 'overview',
            label: '概览',
            children: <div className="trip-product-overview">
              <section><h4>1. 目的地介绍</h4>{document.destination_overview?.cover_image && <figure className="trip-cover-image" tabIndex={0}><img src={document.destination_overview.cover_image.url} alt={document.destination_overview.cover_image.alt || document.destination_overview.name_zh || '目的地'} /><figcaption><a href={document.destination_overview.cover_image.photographer_url} target="_blank" rel="noreferrer">{document.destination_overview.cover_image.photographer_name}</a> / <a href={document.destination_overview.cover_image.unsplash_url} target="_blank" rel="noreferrer">Unsplash</a></figcaption></figure>}<strong>{document.destination_overview?.name_zh || '待确认'}</strong><p>{document.destination_overview?.area_overview || document.destination_overview?.status_reason || '暂无可靠目的地介绍'}</p></section>
              <section><h4>2. 去程交通</h4>{transportSummary(document.outbound_transport)}</section>
              <section><h4>3. 酒店推荐</h4>{document.hotel_recommendations?.recommendations?.length ? document.hotel_recommendations.recommendations.slice(0, 3).map((hotel: Record<string, any>) => <div className="trip-product-summary-row" key={hotel.hotel_id}><strong>{hotel.name}</strong><span>{hotel.area} · 每晚 {formatMoney(hotel.nightly_price, hotel.currency || 'CNY')}</span><small>来源：{hotel.source_reference_id || '暂无可靠来源'} · 更新：{hotel.updated_at || '待确认'}</small></div>) : <p>{document.hotel_recommendations?.status_reason || '暂无可靠实时数据'}</p>}</section>
              <section><h4>5. 返程交通</h4>{transportSummary(document.return_transport)}</section>
              <section><h4>6. 友情提醒</h4>{document.friendly_reminders?.items?.length ? document.friendly_reminders.items.map((item: Record<string, any>, index: number) => <p key={`${item.category}-${index}`}>{item.content}</p>) : <p>{document.friendly_reminders?.status_reason || '暂无可靠实时数据'}</p>}</section>
              <section><h4>7. 地图提醒</h4><p>{document.map_guidance?.status_reason || '真实路线待确认'}</p></section>
              <section><h4>8. 下载与分享</h4><p>{document.delivery?.markdown_filename || '文件名待确认'} · {document.delivery?.share_status || 'private'}</p></section>
            </div>,
          }] : []),
          {
            key: 'timeline',
            label: '行程',
            children: tripDays.length > 0 ? (
              <Tabs
                size="small"
                className="trip-day-tabs"
                activeKey={effectiveActiveDayId}
                onChange={setActiveDayId}
                items={tripDays.map((day) => ({
                  key: day.id,
                  label: formatDayTabLabel(day),
                  children: (
                    <div className="trip-timeline" aria-label={`Day ${day.day} 行程`}>
                      <div className="trip-day-group">
                        {(day.theme || day.estimated_cost !== null) && (
                          <div
                            className="trip-day-title"
                            style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}
                          >
                            <span>{day.theme || `Day ${day.day}`}</span>
                            {formatActivityCost(day.estimated_cost, currency) && (
                              <Tag style={{ marginInlineEnd: 0 }}>
                                当日约 {formatActivityCost(day.estimated_cost, currency)}
                              </Tag>
                            )}
                          </div>
                        )}
                        {day.activities.length > 0 ? day.activities.map((activity, index) => {
                          const location = getActivityLocation(activity, index);
                          const placeName = activity.place?.name || activity.title || `活动 ${index + 1}`;
                          const stayDuration = formatStayDuration(activity.start_time, activity.end_time);
                          const activityCost = formatActivityCost(activity.estimated_cost, currency);
                          const activityDataType = activity.data_type || activity.place?.data_type;
                          const timeRange = activity.start_time
                            ? `${activity.start_time}${activity.end_time ? `–${activity.end_time}` : ''}`
                            : null;
                          return (
                            <React.Fragment key={activity.id}>
                              <div className="trip-location-row">
                                <div className="trip-location-index">{index + 1}</div>
                                <div className="trip-location-main">
                                  <strong>{placeName}</strong>
                                  {activity.title && activity.title !== placeName && <span>{activity.title}</span>}
                                  {activity.notes.length > 0 && <span>{activity.notes.join('；')}</span>}
                                  <div className="trip-location-meta">
                                    {timeRange && <small>{timeRange}</small>}
                                    {stayDuration && <small>{stayDuration}</small>}
                                    {activity.place?.category && <Tag>{activity.place.category}</Tag>}
                                    {activityCost && <Tag color="green">{activityCost}</Tag>}
                                    {renderDataTypeTag(activityDataType)}
                                  </div>
                                </div>
                                {renderLocationActions(location, activity.id)}
                              </div>
                              {index < day.activities.length - 1
                                && activity.route_to_next
                                && renderRouteLeg(activity.route_to_next, currency)}
                            </React.Fragment>
                          );
                        }) : (
                          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当天暂无活动" />
                        )}
                      </div>
                    </div>
                  ),
                }))}
              />
            ) : groupedLocations.length > 0 ? (
              <div className="trip-timeline">
                {groupedLocations.map((group) => (
                  <div className="trip-day-group" key={group.day}>
                    <div className="trip-day-title">{group.day}</div>
                    {group.locations.map((location, index) => (
                      <div className="trip-location-row" key={location.id}>
                        <div className="trip-location-index">{index + 1}</div>
                        <div className="trip-location-main">
                          <strong>{location.name}</strong>
                          <span>{location.description || '暂无补充说明'}</span>
                          <div className="trip-location-meta">
                            {location.category && <Tag>{location.category}</Tag>}
                            <small>{location.lat.toFixed(4)}, {location.lng.toFixed(4)}</small>
                          </div>
                        </div>
                        {renderLocationActions(location)}
                      </div>
                    ))}
                  </div>
                ))}
              </div>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无结构化地点" />
            ),
          },
          {
            key: 'map-groups',
            label: '地图分组',
            children: locationGroups.length > 0 ? (
              <div className="trip-map-groups-panel">
                <Segmented
                  block
                  aria-label="地图分组筛选"
                  options={groupFilterOptions}
                  value={activeLocationGroup?.groupId || ALL_GROUPS_VALUE}
                  disabled={!onSelectGroup}
                  onChange={(value) => {
                    onSelectGroup?.(value === ALL_GROUPS_VALUE ? '' : String(value));
                  }}
                />
                <div className="trip-timeline" style={{ marginTop: 8 }}>
                  {visibleLocationGroups.map((group) => {
                    const sourceKind = getLocationGroupSource(group);
                    return (
                      <div className="trip-day-group" key={group.groupId}>
                        <div
                          className="trip-day-title"
                          style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}
                        >
                          <span>{group.title}</span>
                          <span style={{ display: 'flex', alignItems: 'center', gap: 4, flexWrap: 'wrap' }}>
                            <Tag color={sourceKind === 'current_plan' ? 'green' : 'default'} style={{ marginInlineEnd: 0 }}>
                              {getLocationGroupSourceLabel(group)}
                            </Tag>
                            <Tag style={{ marginInlineEnd: 0 }}>{group.locations.length} 个地点</Tag>
                          </span>
                        </div>
                        {group.locations.length > 0 ? group.locations.map((location, index) => {
                          const isSelected = selectedLocationId === location.id;
                          return (
                            <button
                              type="button"
                              className="trip-location-row trip-map-group-location-row"
                              key={`${group.groupId}-${location.id}`}
                              aria-pressed={isSelected}
                              onClick={() => selectLocationOnMap(location, group.groupId)}
                              style={{
                                width: '100%',
                                font: 'inherit',
                                textAlign: 'left',
                                cursor: onSelectLocation ? 'pointer' : 'default',
                                borderColor: isSelected ? 'var(--travel-primary)' : undefined,
                                background: isSelected
                                  ? 'color-mix(in oklch, var(--travel-primary) 10%, white 90%)'
                                  : undefined,
                              }}
                            >
                              <span className="trip-location-index">{index + 1}</span>
                              <span className="trip-location-main">
                                <strong>{location.name}</strong>
                                <span>{location.description || '暂无补充说明'}</span>
                                <span className="trip-location-meta">
                                  {location.category && <Tag>{location.category}</Tag>}
                                  {location.day !== undefined && <small>{formatTripDay(location.day)}</small>}
                                </span>
                              </span>
                            </button>
                          );
                        }) : (
                          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该分组暂无地点" />
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无地图分组" />
            ),
          },
          {
            key: 'budget',
            label: '预算',
            children: data.budget ? (
              <>
                {data.budget.data_type && (
                  <div style={{ marginBottom: 8 }}>{renderDataTypeTag(data.budget.data_type)}</div>
                )}
                <div className="trip-budget-grid">
                  <div><span>总预算</span><strong>{formatMoney(data.budget.budget_total, currency)}</strong></div>
                  <div><span>人均预算</span><strong>{formatMoney(data.budget.budget_per_person, currency)}</strong></div>
                  <div><span>人数</span><strong>{data.budget.people_count || '未明确'}</strong></div>
                  <div><span>可统计费用</span><strong>{formatMoney(data.budget.known_total ?? data.budget.estimated_total, currency)}</strong></div>
                  <div><span>交通</span><strong>{formatMoney(data.budget.categories?.transport, currency)}</strong></div>
                  <div><span>住宿</span><strong>{formatMoney(data.budget.categories?.accommodation, currency)}</strong></div>
                  <div><span>餐饮</span><strong>{formatMoney(data.budget.categories?.food, currency)}</strong></div>
                  <div><span>门票</span><strong>{formatMoney(data.budget.categories?.tickets, currency)}</strong></div>
                  <div><span>其他</span><strong>{formatMoney(data.budget.categories?.other, currency)}</strong></div>
                  <div><span>未知费用</span><strong>{data.budget.unknown_count || 0} 项</strong></div>
                </div>
                {data.budget.over_budget && (
                  <div className="trip-budget-warning" role="alert">
                    <ExclamationCircleOutlined /> 预计超出预算 {formatMoney(data.budget.overrun_amount, currency)}
                  </div>
                )}
                <p className="trip-budget-source">
                  {data.budget.source_label || '金额为规划估算，待对应来源确认'}
                  {data.budget.updated_at ? ` · 更新：${new Date(data.budget.updated_at).toLocaleString('zh-CN', { hour12: false })}` : ''}
                </p>
                {Boolean(data.budget.unknown_items?.length) && <p className="trip-budget-source">待确认：{data.budget.unknown_items?.join('、')}</p>}
              </>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无预算拆分" />
            ),
          },
          {
            key: 'sources',
            label: '来源',
            children: data.sources.length > 0 ? (
              <div className="trip-source-list">
                {data.sources.map((source, index) => (
                  <div className="trip-source-row" key={`${source.type}-${source.title}-${index}`}>
                    <div>
                      <strong>{source.title || `来源 ${index + 1}`}</strong>
                      <span>{source.source || source.type || 'reference'}</span>
                      {renderDataTypeTag(source.data_type)}
                    </div>
                    <p>{source.snippet || '暂无摘要'}</p>
                    <div className="trip-source-meta">
                      <span>类型：{source.type || '参考资料'}</span>
                      <span>更新时间：{source.updated_at ? new Date(source.updated_at).toLocaleString('zh-CN', { hour12: false }) : '待确认'}</span>
                      <span>可信度：{source.confidence === null || source.confidence === undefined ? '待确认' : `${Math.round(source.confidence * 100)}%`}</span>
                    </div>
                    <p>关联字段：{source.related_fields?.length ? source.related_fields.join('、') : '待确认'}</p>
                    <p>关联地点：{source.related_places?.length ? source.related_places.join('、') : '待确认'}</p>
                    {source.url && (
                      <>
                        <a href={source.url} target="_blank" rel="noreferrer">
                          <LinkOutlined /> 打开来源
                        </a>
                        <small className="trip-source-url">{source.url}</small>
                      </>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可靠来源" />
            ),
          },
          {
            key: 'validation',
            label: '校验',
            children: (
              <div className="trip-validation-panel">
                <div className="trip-validation-status">
                  {hasAutomaticRepair && (
                    <Tag color="green" icon={<CheckCircleOutlined />}>
                      已自动修复{resolvedIssueCount > 0 ? ` ${resolvedIssueCount} 项` : ''}
                    </Tag>
                  )}
                  {needsConfirmation && (
                    <Tag color="orange" icon={<ExclamationCircleOutlined />}>
                      仍需确认 {remainingIssueCount} 项
                    </Tag>
                  )}
                  {!hasRepairStatus && (
                    data.validation?.valid === false ? (
                      <Tag color="red" icon={<ExclamationCircleOutlined />}>需修正</Tag>
                    ) : (
                      <Tag color="green" icon={<CheckCircleOutlined />}>可继续规划</Tag>
                    )
                  )}
                </div>
                {issues.length > 0 ? (
                  <div className="trip-issue-list">
                    {issues.map((issue, index) => (
                      <div className="trip-issue-row" key={`${issue.code}-${index}`}>
                        <Tag color={severityColor(issue.severity)}>{issue.severity || 'info'}</Tag>
                        <div>
                          <strong>{issue.message || issue.code || '校验问题'}</strong>
                          {issue.repair_hint && <span>{issue.repair_hint}</span>}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无校验问题" />
                )}
              </div>
            ),
          },
        ]}
      />
    </section>
  );
};

export default TripWorkspace;
