import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Dropdown, Empty, Input, Modal, Popconfirm, Tag, Tabs } from 'antd';
import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  CompassOutlined,
  CalendarOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  DeleteOutlined,
  DollarOutlined,
  EditOutlined,
  EnvironmentOutlined,
  ExclamationCircleOutlined,
  InboxOutlined,
  HolderOutlined,
  LeftOutlined,
  PushpinOutlined,
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
import type { ImageAssetV3, ReviewSummaryV3 } from '../features/travel/state/travelPlannerTypes';
import type { LocationPoint } from './MapComponent';
import type { MapAnchorKind } from './map/mapMarkerPresentation';
import PoiDetailContent from './PoiDetailContent';
import TripWorkspaceEditor, { type WorkspaceEditTarget } from './TripWorkspaceEditor';
import {
  displayableImageAssets,
  legacyUnsplashCoverAsset,
  TravelImageAsset,
  TravelImageWithFallback,
} from '../features/travel/conversation/TravelImageAsset';

export interface TripWorkspaceLocation {
  id: string;
  name: string;
  lat: number;
  lng: number;
  description?: string;
  category?: string;
  day?: number | string;
  order?: number | string;
  poi_id?: string;
  anchor_kind?: MapAnchorKind;
  anchor_label?: string;
  summary?: string;
  image_assets?: ImageAssetV3[];
  review_summary?: ReviewSummaryV3 | null;
  sources?: Array<Record<string, unknown>>;
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
  currency_breakdown?: Array<{
    category: string;
    amount: {
      amount: number;
      currency: string;
      cny_reference_amount?: number | null;
      exchange_rate_as_of?: string | null;
    };
  }>;
  traveler_costs?: Array<{
    traveler_type: 'adult' | 'child' | 'senior';
    count: number;
    estimated_total?: {
      amount: number;
      currency: string;
      cny_reference_amount?: number | null;
    } | null;
    pricing_status: string;
  }>;
  source_label?: string;
  updated_at?: string;
  [key: string]: unknown;
}

export interface TripWorkspaceIssue {
  code?: string;
  message?: string;
  severity?: 'info' | 'warning' | 'error' | string;
  repair_hint?: string;
  target_id?: string | null;
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

export interface TripDraftValidation {
  can_apply?: boolean;
  status?: 'ready' | 'degraded' | 'blocked' | string;
  hard_errors?: TripWorkspaceIssue[];
  soft_warnings?: TripWorkspaceIssue[];
}

export interface TripActivityDeleteImpact {
  activity_id: string;
  activity_title?: string;
  day?: number;
  notes: number;
  reservation_reminders: number;
  checklist_items: number;
  image_references: number;
  route_segments: number;
  map_markers: number;
  total_related_items?: number;
}

export interface TripDayRouteOptimizationPreview {
  day: number;
  can_optimize: boolean;
  reason?: string | null;
  current_order: Array<{ activity_id: string; title: string; fixed_time: boolean }>;
  optimized_order: Array<{ activity_id: string; title: string; fixed_time: boolean }>;
  fixed_activity_count: number;
  order_changes: Array<{ activity_id: string; title: string; before_order: number; after_order: number }>;
  time_changes: Array<{
    activity_id: string;
    title: string;
    before: { start_at?: string | null; end_at?: string | null };
    after: { start_at?: string | null; end_at?: string | null };
  }>;
  route_segments: number;
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
  /** 窄屏仅保留备注、固定时间、同日排序与删除，避免桌面编辑密度压缩到手机。 */
  mobileSimpleEditing?: boolean;
  locationGroups?: TripWorkspaceLocationGroup[];
  activeGroupId?: string;
  selectedLocationId?: string;
  onSelectGroup?: (groupId: string) => void;
  onSelectLocation?: (locationId: string) => void;
  onQuickAction?: (prompt: string) => void;
  onEditOperation?: (type: string, payload: Record<string, unknown>) => void | boolean | Promise<void | boolean>;
  onGetActivityDeleteImpact?: (activityId: string) => Promise<TripActivityDeleteImpact | null>;
  onGetDayRouteOptimizationPreview?: (day: number) => Promise<TripDayRouteOptimizationPreview | null>;
  onUndoEdit?: () => void;
  canUndoEdit?: boolean;
  hasDraft?: boolean;
  draftValidation?: TripDraftValidation | null;
  onDiscardDraft?: () => void;
  onApplyDraft?: () => void | Promise<unknown>;
  canRestorePreviousFormal?: boolean;
  onRestorePreviousFormal?: () => void | Promise<unknown>;
  editLoading?: boolean;
  onFocusMap?: () => void;
  onDetailContextChange?: (context: TripWorkspaceDetailContext | null) => void;
  returnToDetailRequestId?: number;
}

type WorkspaceView = 'overview' | 'timeline' | 'candidates' | 'checklist' | 'reminders';

function SpaceForEditing({ day, note, disabled, onEdit }: {
  day: number; note: string; disabled: boolean; onEdit: (target: WorkspaceEditTarget) => void;
}) {
  return <div className="trip-day-edit-toolbar">
    <Button size="small" disabled={disabled} onClick={() => onEdit({ type: 'add_activity', day, title: `第 ${day} 天新增地点` })}>新增地点</Button>
    <Button size="small" disabled={disabled} onClick={() => onEdit({ type: 'update_day_note', day, content: note, title: `第 ${day} 天备注` })}>编辑当日备注</Button>
    {note && <p>{note}</p>}
  </div>;
}

export interface TripWorkspaceDetailContext {
  activityId: string;
  dayId: string;
  locationId: string;
  label: string;
}

interface ActivityEntry {
  activity: TripActivity;
  day: TripDay;
  index: number;
  location: TripWorkspaceLocation;
}

interface ActivityDetailContext extends TripWorkspaceDetailContext {
  returnView: WorkspaceView;
}

const stringValue = (value: unknown): string => (
  typeof value === 'string' ? value.trim() : ''
);

const formatMoney = (value: unknown, currency = 'CNY') => {
  const amount = Number(value);
  if (value === null || value === undefined || value === '' || !Number.isFinite(amount) || amount < 0) return '待确认';
  const symbol = currency === 'CNY' ? '¥' : `${currency} `;
  return `${symbol}${Math.round(amount).toLocaleString('zh-CN')}`;
};

const moneyDisplay = (value: unknown, fallbackCurrency = 'CNY') => {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const money = value as Record<string, unknown>;
    const currency = typeof money.currency === 'string' ? money.currency : fallbackCurrency;
    const original = formatMoney(money.amount, currency);
    if (currency === 'CNY') return original;
    const cnyReference = Number(money.cny_reference_amount);
    if (!Number.isFinite(cnyReference) || cnyReference < 0) return `${original}（人民币参考待确认）`;
    const exchangeDate = typeof money.exchange_rate_as_of === 'string' ? money.exchange_rate_as_of : '';
    return `${original}（约${formatMoney(cnyReference, 'CNY')}${exchangeDate ? `，汇率参考日期 ${exchangeDate}` : ''}）`;
  }
  return formatMoney(value, fallbackCurrency);
};

const transportTimeDisplay = (value: unknown) => {
  if (typeof value === 'string' && value.trim()) return value;
  if (!value || typeof value !== 'object' || Array.isArray(value)) return '待确认';
  const time = value as Record<string, unknown>;
  for (const key of ['display_text', 'local_iso', 'beijing_iso', 'utc']) {
    if (typeof time[key] === 'string' && time[key].trim()) return time[key] as string;
  }
  return '待确认';
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

const formatDurationMinutes = (duration: number) => {
  if (!Number.isFinite(duration) || duration <= 0) return null;
  const normalizedDuration = Math.round(duration);
  const hours = Math.floor(normalizedDuration / 60);
  const minutes = normalizedDuration % 60;
  return `${hours > 0 ? `${hours}小时` : ''}${minutes > 0 ? `${minutes}分钟` : ''}`;
};

const formatStayDuration = (
  startTime: string | null,
  endTime: string | null,
  durationMinutes?: number | null,
) => {
  const explicitDuration = durationMinutes === null || durationMinutes === undefined
    ? null
    : formatDurationMinutes(durationMinutes);
  if (explicitDuration) return `停留 ${explicitDuration}`;
  const start = parseTimeMinutes(startTime);
  let end = parseTimeMinutes(endTime);
  if (start === null || end === null) return null;
  if (end < start) end += 24 * 60;
  const duration = end - start;
  const formattedDuration = formatDurationMinutes(duration);
  return formattedDuration ? `停留 ${formattedDuration}` : null;
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

const activityCategoryLabels: Record<string, string> = {
  attraction: '景点',
  scenic: '景点',
  restaurant: '吃喝',
  food: '吃喝',
  dining: '吃喝',
  hotel: '住宿',
  lodging: '住宿',
  accommodation: '住宿',
  transport: '交通',
  shopping: '购物',
  free_time: '自由活动',
  leisure: '自由活动',
};

const formatActivityCategory = (category?: string) => {
  const normalizedCategory = category?.trim();
  if (!normalizedCategory) return null;
  return activityCategoryLabels[normalizedCategory.toLowerCase()] || normalizedCategory;
};

const severityColor = (severity?: string) => {
  if (severity === 'error') return 'red';
  if (severity === 'warning') return 'orange';
  return 'blue';
};

const formatDayTabLabel = (day: TripDay) => (
  day.date ? `${day.date} · Day ${day.day}` : `Day ${day.day}`
);

const renderRouteLeg = (route: RouteLeg | null, currency: string) => {
  if (!route || !route.mode.trim()) {
    return (
      <div className="trip-route-leg trip-route-leg--pending" aria-label="前往下一站：路线信息待确认">
        <SwapOutlined aria-hidden="true" />
        <span>路线信息待确认</span>
      </div>
    );
  }
  const distance = formatDistance(route.distance_meters);
  const cost = route.data_type === 'confirmed_live_data'
    ? formatActivityCost(route.estimated_cost, currency)
    : null;
  return (
    <div
      className="trip-route-leg"
      aria-label={`前往下一站：${formatRouteMode(route.mode)}`}
    >
      <SwapOutlined aria-hidden="true" />
      <strong>{formatRouteMode(route.mode)}</strong>
      {distance && <span>{distance}</span>}
      {route.duration_minutes !== null && <span>{Math.round(route.duration_minutes)} 分钟</span>}
      {cost && <span>{cost}</span>}
    </div>
  );
};

const TripWorkspace: React.FC<TripWorkspaceProps> = ({
  data,
  document: providedDocument,
  mobileSimpleEditing = false,
  locationGroups = [],
  selectedLocationId = '',
  onSelectGroup,
  onSelectLocation,
  onQuickAction,
  onEditOperation,
  onGetActivityDeleteImpact,
  onGetDayRouteOptimizationPreview,
  onUndoEdit,
  canUndoEdit = false,
  hasDraft = false,
  draftValidation = null,
  onDiscardDraft,
  onApplyDraft,
  canRestorePreviousFormal = false,
  onRestorePreviousFormal,
  editLoading = false,
  onFocusMap,
  onDetailContextChange,
  returnToDetailRequestId = 0,
}) => {
  const document = providedDocument || {};
  const coverImage = legacyUnsplashCoverAsset(
    document.destination_overview?.cover_image,
    document.destination_overview?.name_zh || '目的地封面',
  );
  const [activeWorkspaceView, setActiveWorkspaceView] = useState<WorkspaceView>('overview');
  const [activeDayId, setActiveDayId] = useState<string>();
  const [detailContext, setDetailContext] = useState<ActivityDetailContext | null>(null);
  const [editingActivityNoteId, setEditingActivityNoteId] = useState<string | null>(null);
  const [editingActivityNoteText, setEditingActivityNoteText] = useState('');
  const [editTarget, setEditTarget] = useState<WorkspaceEditTarget | null>(null);
  const [draggedItem, setDraggedItem] = useState<{ id: string; candidate: boolean } | null>(null);
  const [deleteConfirmation, setDeleteConfirmation] = useState<TripActivityDeleteImpact | null>(null);
  const [isDeleteImpactLoading, setIsDeleteImpactLoading] = useState(false);
  const [isDeletingActivity, setIsDeletingActivity] = useState(false);
  const [routeOptimizationPreview, setRouteOptimizationPreview] = useState<TripDayRouteOptimizationPreview | null>(null);
  const [isRouteOptimizationLoading, setIsRouteOptimizationLoading] = useState(false);
  const [isOptimizingRoute, setIsOptimizingRoute] = useState(false);
  const activityCardRefs = useRef<Record<string, HTMLElement | null>>({});
  const handledReturnRequestId = useRef(0);
  const tripDays = useMemo(
    () => [...(data.days || [])].sort((left, right) => left.day - right.day),
    [data.days],
  );
  const effectiveActiveDayId = tripDays.some((day) => day.id === activeDayId)
    ? activeDayId
    : tripDays[0]?.id;
  const isV3Document = document.schema_version === '3.0';
  const canDrag = isV3Document && !mobileSimpleEditing && Boolean(onEditOperation) && !editLoading;
  const startActivityDrag = useCallback((event: React.DragEvent, id: string, candidate: boolean) => {
    event.stopPropagation();
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('application/x-trip-activity', JSON.stringify({ id, candidate }));
    setDraggedItem({ id, candidate });
  }, []);
  const dropActivity = (event: React.DragEvent, day: number, position: number) => {
    event.preventDefault(); event.stopPropagation();
    if (!canDrag) return;
    let item: { id: string; candidate: boolean };
    try {
      item = JSON.parse(event.dataTransfer.getData('application/x-trip-activity'));
    } catch { return; }
    if (!item || typeof item.id !== 'string' || typeof item.candidate !== 'boolean') return;
    void onEditOperation?.(item.candidate ? 'insert_candidate' : 'move_activity', {
      activity_id: item.id, target_day: day, position,
    });
    setDraggedItem(null);
  };

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

  const selectLocationOnMap = useCallback((
    location: TripWorkspaceLocation,
    knownGroupId?: string,
  ) => {
    const matchingGroup = knownGroupId
      ? locationGroups.find((group) => group.groupId === knownGroupId)
      : locationGroups.find((group) => group.locations.some((candidate) => candidate.id === location.id));

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
    const placePoiId = stringValue(activity.place?.poi_id) || stringValue(activity.place?.id);
    const matchedLocation = data.locations.find((location) => (
      (placePoiId && (location.id === placePoiId || location.poi_id === placePoiId))
      || location.id === activity.id
      || location.name.trim().toLocaleLowerCase() === placeName.trim().toLocaleLowerCase()
    ));
    return matchedLocation || {
      id: placePoiId || activity.id,
      name: placeName,
      lat: typeof activity.place?.lat === 'number' ? activity.place.lat : Number.NaN,
      lng: typeof activity.place?.lng === 'number' ? activity.place.lng : Number.NaN,
      description: activity.title || undefined,
      category: activity.place?.category,
      day: activity.day,
      order: index + 1,
    };
  }, [data.locations]);

  const activityEntries = useMemo<ActivityEntry[]>(() => (
    tripDays.flatMap((day) => day.activities.map((activity, index) => ({
      activity,
      day,
      index,
      location: getActivityLocation(activity, index),
    })))
  ), [getActivityLocation, tripDays]);

  const selectedActivityEntry = useMemo(() => (
    selectedLocationId
      ? activityEntries.find(({ activity, location }) => (
        location.id === selectedLocationId
        || location.poi_id === selectedLocationId
        || activity.id === selectedLocationId
        || stringValue(activity.place?.poi_id) === selectedLocationId
        || stringValue(activity.place?.id) === selectedLocationId
      )) || null
      : null
  ), [activityEntries, selectedLocationId]);

  const detailEntry = useMemo(() => (
    detailContext
      ? activityEntries.find(({ activity }) => activity.id === detailContext.activityId) || null
      : null
  ), [activityEntries, detailContext]);

  const scrollToActivity = useCallback((activityId: string) => {
    const scroll = () => {
      const card = activityCardRefs.current[activityId];
      card?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' });
      card?.querySelector<HTMLButtonElement>('.trip-activity-card-open')?.focus({ preventScroll: true });
    };
    if (typeof window === 'undefined' || typeof window.requestAnimationFrame !== 'function') {
      scroll();
      return;
    }
    window.requestAnimationFrame(() => window.requestAnimationFrame(scroll));
  }, []);

  const buildDetailLocation = useCallback((entry: ActivityEntry): LocationPoint => {
    const placeSummary = stringValue(entry.activity.place?.summary);
    const activityImages = displayableImageAssets(entry.activity.images, 3);
    return {
      ...entry.location,
      poi_id: entry.location.poi_id || entry.location.id,
      description: entry.location.description || entry.activity.title || undefined,
      category: entry.location.category || entry.activity.place?.category,
      summary: entry.location.summary || placeSummary || entry.location.description,
      image_assets: entry.location.image_assets?.length ? entry.location.image_assets : activityImages,
      suggested_duration_minutes: entry.activity.duration_minutes ?? undefined,
    };
  }, []);

  useEffect(() => {
    if (!selectedActivityEntry) return;
    if (detailContext && detailContext.activityId !== selectedActivityEntry.activity.id) {
      setDetailContext(null);
      onDetailContextChange?.(null);
    }
    setActiveWorkspaceView('timeline');
    setActiveDayId(selectedActivityEntry.day.id);
    scrollToActivity(selectedActivityEntry.activity.id);
  }, [detailContext, onDetailContextChange, scrollToActivity, selectedActivityEntry]);

  const openActivityDetail = useCallback((entry: ActivityEntry) => {
    const activityName = entry.activity.title || entry.location.name;
    const nextContext: ActivityDetailContext = {
      activityId: entry.activity.id,
      dayId: entry.day.id,
      locationId: entry.location.id,
      label: `返回${activityName}`,
      returnView: activeWorkspaceView,
    };
    setDetailContext(nextContext);
    onDetailContextChange?.(nextContext);
    setActiveWorkspaceView('timeline');
    setActiveDayId(entry.day.id);
    if (!mobileSimpleEditing) selectLocationOnMap(entry.location);
  }, [activeWorkspaceView, mobileSimpleEditing, onDetailContextChange, selectLocationOnMap]);

  const closeActivityDetail = useCallback(() => {
    if (!detailContext) return;
    setDetailContext(null);
    onDetailContextChange?.(null);
    setActiveWorkspaceView(detailContext.returnView);
    setActiveDayId(detailContext.dayId);
    scrollToActivity(detailContext.activityId);
  }, [detailContext, onDetailContextChange, scrollToActivity]);

  useEffect(() => {
    if (returnToDetailRequestId <= handledReturnRequestId.current) return;
    handledReturnRequestId.current = returnToDetailRequestId;
    if (!detailContext) return;
    closeActivityDetail();
  }, [closeActivityDetail, detailContext, returnToDetailRequestId]);

  const openActivityNoteEditor = useCallback((activity: TripActivity) => {
    setEditingActivityNoteId(activity.id);
    setEditingActivityNoteText(activity.notes.join('；'));
  }, []);

  const requestPermanentActivityDelete = useCallback(async (activityId: string) => {
    if (!onGetActivityDeleteImpact || isDeleteImpactLoading || editLoading) return;
    setIsDeleteImpactLoading(true);
    try {
      const impact = await onGetActivityDeleteImpact(activityId);
      if (impact) setDeleteConfirmation(impact);
    } finally {
      setIsDeleteImpactLoading(false);
    }
  }, [editLoading, isDeleteImpactLoading, onGetActivityDeleteImpact]);

  const confirmPermanentActivityDelete = useCallback(async () => {
    if (!deleteConfirmation || !onEditOperation || isDeletingActivity) return;
    setIsDeletingActivity(true);
    try {
      const succeeded = await onEditOperation('delete_activity', {
        activity_id: deleteConfirmation.activity_id,
        confirmed: true,
      });
      if (succeeded !== false) setDeleteConfirmation(null);
    } finally {
      setIsDeletingActivity(false);
    }
  }, [deleteConfirmation, isDeletingActivity, onEditOperation]);

  const requestDayRouteOptimization = useCallback(async (day: number) => {
    if (!onGetDayRouteOptimizationPreview || isRouteOptimizationLoading || editLoading) return;
    setIsRouteOptimizationLoading(true);
    try {
      const preview = await onGetDayRouteOptimizationPreview(day);
      if (preview) setRouteOptimizationPreview(preview);
    } finally {
      setIsRouteOptimizationLoading(false);
    }
  }, [editLoading, isRouteOptimizationLoading, onGetDayRouteOptimizationPreview]);

  const confirmDayRouteOptimization = useCallback(async () => {
    if (!routeOptimizationPreview?.can_optimize || !onEditOperation || isOptimizingRoute) return;
    setIsOptimizingRoute(true);
    try {
      const succeeded = await onEditOperation('optimize_day_route', {
        day: routeOptimizationPreview.day,
        confirmed: true,
      });
      if (succeeded !== false) setRouteOptimizationPreview(null);
    } finally {
      setIsOptimizingRoute(false);
    }
  }, [isOptimizingRoute, onEditOperation, routeOptimizationPreview]);

  const renderLocationActions = useCallback((
    location: TripWorkspaceLocation,
    activity?: TripActivity,
    activityIndex?: number,
    day?: TripDay,
  ) => {
    const activityId = activity?.id;
    const canAdjustTime = Boolean(activity?.start_time && activity?.end_time);
    const canEditV3Activity = Boolean(isV3Document && activityId && onEditOperation);
    const canMoveEarlier = canEditV3Activity && Number.isInteger(activityIndex) && activityIndex! > 0;
    const canMoveLater = canEditV3Activity
      && Number.isInteger(activityIndex)
      && Boolean(day)
      && activityIndex! < day!.activities.length - 1;
    return (
      <div className="trip-location-actions">
        {activity && canDrag && <Button size="small" type="text" icon={<HolderOutlined />} draggable
          onDragStart={(event) => startActivityDrag(event, activity.id, false)}
          onDragEnd={() => setDraggedItem(null)}
          aria-label={`拖动${location.name}调整顺序`} title="拖动调整顺序；也可在编辑菜单中上移或下移" />}
        <Button
          size="small"
          type="text"
          icon={<EnvironmentOutlined />}
          onClick={() => selectLocationOnMap(location)}
          disabled={!Number.isFinite(location.lat) || !Number.isFinite(location.lng)}
          aria-label={`在地图中查看${location.name}`}
          title="在地图中查看"
        />
        {!mobileSimpleEditing && <>
          <Button
            size="small"
            type="text"
            icon={<SwapOutlined />}
            disabled={!onQuickAction && !canEditV3Activity}
            onClick={() => canEditV3Activity
              ? setEditTarget({ type: 'replace_activity', activityId, title: `替换${location.name}` })
              : sendAction(buildReplaceLocationPrompt(location))}
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
        </>}
        {activity && mobileSimpleEditing && isV3Document && (
          <>
            <Button
              size="small"
              type="text"
              icon={<ArrowUpOutlined />}
              disabled={!canMoveEarlier || editLoading}
              onClick={() => onEditOperation?.('move_activity', {
                activity_id: activityId!, target_day: day!.day, position: activityIndex! - 1,
              })}
              aria-label={`将${location.name}上移一位`}
              title="同日上移"
            />
            <Button
              size="small"
              type="text"
              icon={<ArrowDownOutlined />}
              disabled={!canMoveLater || editLoading}
              onClick={() => onEditOperation?.('move_activity', {
                activity_id: activityId!, target_day: day!.day, position: activityIndex! + 1,
              })}
              aria-label={`将${location.name}下移一位`}
              title="同日下移"
            />
          </>
        )}
        {activity && (
          <Dropdown
            disabled={!canEditV3Activity}
            trigger={['click']}
            menu={{
              items: mobileSimpleEditing ? [
                { key: 'fixed', label: activity.fixed_time ? '取消固定时间' : '固定当前时间', icon: <PushpinOutlined />, disabled: !activity.start_time },
                { key: 'note', label: activity.notes.length > 0 ? '编辑活动备注' : '添加活动备注' },
              ] : [
                { key: 'time', label: '设置起止时间', icon: <ClockCircleOutlined /> },
                { key: 'up', label: '同日上移', disabled: !canMoveEarlier },
                { key: 'down', label: '同日下移', disabled: !canMoveLater },
                { key: 'earlier', label: '提前 15 分钟', icon: <ClockCircleOutlined />, disabled: !canAdjustTime },
                { key: 'later', label: '延后 15 分钟', icon: <ClockCircleOutlined />, disabled: !canAdjustTime },
                { key: 'fixed', label: activity.fixed_time ? '取消固定时间' : '固定当前时间', icon: <PushpinOutlined />, disabled: !activity.start_time },
                { key: 'note', label: activity.notes.length > 0 ? '编辑活动备注' : '添加活动备注' },
                { key: 'candidate', label: '移入候选', icon: <InboxOutlined />, danger: true },
              ],
              onClick: ({ key }) => {
                if (!onEditOperation || !activityId) return;
                if (key === 'time') {
                  setEditTarget({ type: 'update_activity_time', activityId, start: activity.start_time || undefined, end: activity.end_time || undefined, title: `${location.name}起止时间` });
                } else if (key === 'up' || key === 'down') {
                  onEditOperation('move_activity', { activity_id: activityId, target_day: day!.day, position: activityIndex! + (key === 'up' ? -1 : 1) });
                } else if (key === 'earlier' || key === 'later') {
                  onEditOperation('shift_activity_time', { activity_id: activityId, delta_minutes: key === 'earlier' ? -15 : 15 });
                } else if (key === 'fixed') {
                  onEditOperation('set_activity_fixed_time', { activity_id: activityId, fixed_time: !activity.fixed_time });
                } else if (key === 'note') {
                  openActivityNoteEditor(activity);
                } else if (key === 'candidate') {
                  onEditOperation('move_activity_to_candidate', { activity_id: activityId });
                }
              },
            }}
          >
            <Button
              size="small"
              type="text"
              icon={<EditOutlined />}
              disabled={!canEditV3Activity}
              aria-label={mobileSimpleEditing ? `编辑${location.name}的备注或固定状态` : `编辑${location.name}的时间、备注或候选状态`}
              title={mobileSimpleEditing ? '编辑备注或固定状态' : '编辑时间、备注或候选状态'}
            />
          </Dropdown>
        )}
        {isV3Document && activity && (
          <Button
            size="small"
            type="text"
            danger
            icon={<DeleteOutlined />}
            disabled={!canEditV3Activity || !onGetActivityDeleteImpact || editLoading || isDeleteImpactLoading}
            loading={isDeleteImpactLoading}
            onClick={() => { void requestPermanentActivityDelete(activityId!); }}
            aria-label={`永久删除${location.name}`}
            title="永久删除活动"
          />
        )}
        {!isV3Document && (
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
        )}
      </div>
    );
  }, [
    buildMoveMenuItems,
    canDrag,
    startActivityDrag,
    editLoading,
    isDeleteImpactLoading,
    isV3Document,
    mobileSimpleEditing,
    onEditOperation,
    onGetActivityDeleteImpact,
    onQuickAction,
    openActivityNoteEditor,
    requestPermanentActivityDelete,
    selectLocationOnMap,
    sendAction,
  ]);

  const issues = data.validation?.issues || [];
  const draftHardErrors = draftValidation?.hard_errors || [];
  const draftSoftWarnings = draftValidation?.soft_warnings || [];
  const visibleIssues = Array.from(
    new Map(
      [
        ...issues,
        ...draftHardErrors.map((issue) => ({ ...issue, severity: 'error' })),
        ...draftSoftWarnings.map((issue) => ({ ...issue, severity: 'warning' })),
      ].map((issue, index) => [
        `${issue.code || 'issue'}:${issue.target_id || ''}:${issue.message || index}`,
        issue,
      ]),
    ).values(),
  );
  const currency = data.budget?.currency || 'CNY';
  const budgetCurrencyDetails = Array.isArray(data.budget?.currency_breakdown)
    ? data.budget.currency_breakdown
    : [];
  const resolvedIssueCount = data.repair?.resolved_issue_codes?.length || 0;
  const remainingIssueCount = Math.max(
    data.repair?.remaining_issue_codes?.length || 0,
    data.repair?.remaining_validation?.issues?.length || 0,
    data.repair ? issues.length : 0,
  );
  const hasAutomaticRepair = Boolean(data.repair?.repaired || resolvedIssueCount > 0);
  const needsConfirmation = remainingIssueCount > 0
    || Boolean(document.validation?.degraded)
    || draftSoftWarnings.length > 0;
  const hasRepairStatus = hasAutomaticRepair || needsConfirmation;
  const transportSummary = (section: Record<string, any> | undefined, direction: 'outbound' | 'return') => {
    if (!section?.options?.length) return <p>{section?.status_reason || '暂无可靠实时数据'}</p>;
    const selected = section.options.find((option: Record<string, any>) => option.option_id === section.selected_option_id) || section.options[0];
    const alternatives = section.options.filter((option: Record<string, any>) => option !== selected).slice(0, 2);
    const renderOption = (option: Record<string, any>) => (
      <div className="trip-product-summary-row" key={option.option_id}>
        <strong>{({ train: '火车', flight: '航班', intercity_bus: '长途巴士' } as Record<string, string>)[option.mode] || '交通方案'} {option.service_number || ''}</strong>
        <span>{option.departure_station || option.departure_place || '待确认'} → {option.arrival_station || option.arrival_place || '待确认'}</span>
        <small>{transportTimeDisplay(option.departure_time)}-{transportTimeDisplay(option.arrival_time)} · {moneyDisplay(option.price, option.currency || 'CNY')} · {({ available: '有余票', limited: '余票紧张', unavailable: '无余票', unknown: '余票待确认' } as Record<string, string>)[option.availability] || '余票待确认'}</small>
        {isV3Document && !mobileSimpleEditing && onEditOperation && (
          <div className="trip-product-row-action">
            {section.selected_option_id === option.option_id && <Tag color="green">规划采用</Tag>}
            <Button
              size="small"
              type={section.selected_option_id === option.option_id ? 'text' : 'default'}
              disabled={editLoading || section.status !== 'ready' || section.selected_option_id === option.option_id}
              onClick={() => onEditOperation('select_transport_option', { direction, option_id: option.option_id })}
            >
              {section.selected_option_id === option.option_id ? '当前主方案' : '设为主方案'}
            </Button>
          </div>
        )}
      </div>
    );
    return <>{renderOption(selected)}{alternatives.length > 0 && <details><summary>其他交通方案（{alternatives.length}）</summary>{alternatives.map(renderOption)}</details>}</>;
  };
  const lodgingOptions = document?.hotel_recommendations?.recommendations
    || document?.lodging_plan?.options
    || [];
  const flexibleDates = document?.intent?.date_mode === 'flexible'
    || /日期(?:暂时|暂|还)?(?:未确定|没确定|没定)/.test(String(document?.intent?.date_range || ''));
  const actionItems = Array.isArray(document.action_items) ? document.action_items : [];
  const checklistItems = actionItems.filter((item: Record<string, any>) => item.kind !== 'reservation_reminder');
  const reservationReminderItems = actionItems.filter((item: Record<string, any>) => item.kind === 'reservation_reminder');
  const reminderItems = [
    ...(Array.isArray(document?.friendly_reminders?.items) ? document.friendly_reminders.items : []),
    ...reservationReminderItems.map((item: Record<string, any>) => ({
      content: item.text,
      category: item.kind,
      status: item.status,
    })),
  ];
  const visibleReminderItems = flexibleDates
    ? reminderItems.filter((item: Record<string, any>) => !['reservation', 'weather', 'reservation_reminder'].includes(item.category || item.kind))
    : reminderItems;
  const candidatePool = Array.isArray(document.candidate_pool) ? document.candidate_pool : [];
  const tripNotes = Array.isArray(document.notes)
    ? document.notes.filter((note: Record<string, any>) => note.scope === 'trip' && typeof note.content === 'string' && note.content.trim())
    : [];
  const activityCount = tripDays.reduce((total, day) => total + day.activities.length, 0);
  const pendingChecklistCount = checklistItems.filter((item: Record<string, any>) => item.status !== 'completed').length;
  const unresolvedIssueCount = visibleIssues.filter((issue) => issue.severity === 'warning' || issue.severity === 'error').length;
  const documentStatus = typeof document.status === 'string' ? document.status : '';
  const workspaceState = editLoading
    ? { label: '正在更新', color: 'blue', detail: '正在处理本次修改，请稍候。' }
    : hasDraft && draftHardErrors.length > 0
      ? { label: '草稿需修正', color: 'red', detail: `发现 ${draftHardErrors.length} 项会阻止应用的问题，已定位到首项。` }
      : hasDraft && (draftValidation?.status === 'degraded' || document.validation?.degraded || documentStatus === 'degraded')
        ? { label: '草稿含待确认信息', color: 'orange', detail: '草稿已保留；外部信息待确认时可按降级版本保存。' }
    : hasDraft
      ? { label: '发现未应用修改', color: 'orange', detail: '可继续编辑，或放弃草稿并回到当前正式方案。' }
      : canRestorePreviousFormal
        ? { label: '当前正式方案', color: 'green', detail: '已保留唯一上一正式版本，可按需恢复。' }
      : canUndoEdit
        ? { label: '最近修改可撤销', color: 'gold', detail: '可使用右上角按钮撤销最近一次修改。' }
        : { label: '当前正式方案', color: 'green', detail: '工作台正在展示当前正式版本。' };

  const openDayTimeline = (dayId: string) => {
    setActiveDayId(dayId);
    setActiveWorkspaceView('timeline');
  };

  useEffect(() => {
    if (!hasDraft || draftHardErrors.length === 0) return;
    const targetId = draftHardErrors[0]?.target_id;
    const target = targetId
      ? activityEntries.find((entry) => entry.activity.id === targetId)
      : null;
    setActiveWorkspaceView('timeline');
    if (!target) return;
    setActiveDayId(target.day.id);
    scrollToActivity(target.activity.id);
  }, [activityEntries, draftHardErrors, hasDraft, scrollToActivity]);

  return (
    <section className={`trip-workspace${mobileSimpleEditing && detailEntry ? ' trip-workspace--mobile-detail' : ''}`}>
      {editTarget && onEditOperation && <TripWorkspaceEditor target={editTarget}
        destination={document.intent?.destination || ''} onClose={() => setEditTarget(null)} onSave={onEditOperation} />}
      <Modal
        open={Boolean(deleteConfirmation)}
        title={`永久删除“${deleteConfirmation?.activity_title || '该活动'}”？`}
        okText="永久删除"
        cancelText="保留活动"
        okButtonProps={{ danger: true }}
        confirmLoading={isDeletingActivity}
        maskClosable={!isDeletingActivity}
        keyboard={!isDeletingActivity}
        onCancel={() => { if (!isDeletingActivity) setDeleteConfirmation(null); }}
        onOk={() => { void confirmPermanentActivityDelete(); }}
      >
        <p>此操作不可恢复。若要保留活动资料，请使用“移入候选”。</p>
        <ul>
          <li>活动备注：{deleteConfirmation?.notes || 0} 条</li>
          <li>预约提醒：{deleteConfirmation?.reservation_reminders || 0} 条</li>
          <li>准备清单：{deleteConfirmation?.checklist_items || 0} 条</li>
          <li>图片引用：{deleteConfirmation?.image_references || 0} 条</li>
          <li>待重新核验路线段：{deleteConfirmation?.route_segments || 0} 条</li>
          <li>地图标记：{deleteConfirmation?.map_markers || 0} 个</li>
        </ul>
      </Modal>
      <Modal
        open={Boolean(routeOptimizationPreview)}
        title={routeOptimizationPreview?.can_optimize
          ? `优化 Day ${routeOptimizationPreview.day} 路线？`
          : `Day ${routeOptimizationPreview?.day || ''} 暂无需优化`}
        okText={routeOptimizationPreview?.can_optimize ? '确认优化' : '关闭'}
        cancelText="保留当前路线"
        confirmLoading={isOptimizingRoute}
        maskClosable={!isOptimizingRoute}
        keyboard={!isOptimizingRoute}
        onCancel={() => { if (!isOptimizingRoute) setRouteOptimizationPreview(null); }}
        onOk={() => {
          if (routeOptimizationPreview?.can_optimize) void confirmDayRouteOptimization();
          else setRouteOptimizationPreview(null);
        }}
      >
        {routeOptimizationPreview?.can_optimize ? (
          <>
            <p>保持当天地点与日期不变；{routeOptimizationPreview.fixed_activity_count > 0 ? ` ${routeOptimizationPreview.fixed_activity_count} 项固定时间活动不会移动。` : '不移动任何日期。'}</p>
            <p>优化后顺序：</p>
            <ol>
              {routeOptimizationPreview.optimized_order.map((activity) => (
                <li key={activity.activity_id}>{activity.title}{activity.fixed_time ? '（固定时间）' : ''}</li>
              ))}
            </ol>
            <p>
              将调整 {routeOptimizationPreview.order_changes.length} 项顺序、{routeOptimizationPreview.time_changes.length} 项非固定时间；
              {routeOptimizationPreview.route_segments > 0
                ? `确认后 ${routeOptimizationPreview.route_segments} 条真实路线段将重新核验。`
                : '确认后当天路线将重新核验。'}
            </p>
          </>
        ) : <p>{routeOptimizationPreview?.reason || '当前路线无需调整。'}</p>}
      </Modal>
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
          {(hasDraft || documentStatus === 'draft') && onDiscardDraft && (
            <Popconfirm
              title="放弃未应用修改？"
              description="草稿会被删除，且不能恢复。"
              okText="放弃草稿"
              cancelText="继续编辑"
              onConfirm={onDiscardDraft}
              disabled={editLoading}
            >
              <Button size="small" type="text" danger disabled={editLoading}>放弃草稿</Button>
            </Popconfirm>
          )}
          {hasDraft && onApplyDraft && (
            <Popconfirm
              title="应用全部修改？"
              description="会整体校验草稿并保存为新正式版本；当前正式方案保留为唯一可恢复的上一版本。"
              okText="应用修改"
              cancelText="继续编辑"
              onConfirm={() => onApplyDraft()}
              disabled={editLoading || draftValidation?.can_apply === false}
            >
              <Button
                size="small"
                type="primary"
                loading={editLoading}
                aria-label="应用修改"
                aria-busy={editLoading}
                disabled={editLoading || draftValidation?.can_apply === false}
              >
                应用修改
              </Button>
            </Popconfirm>
          )}
          {!hasDraft && canRestorePreviousFormal && onRestorePreviousFormal && (
            <Popconfirm
              title="恢复上一正式版本？"
              description="将创建新正式版本并移除当前版本，恢复后不能重做。"
              okText="恢复上一版本"
              cancelText="保留当前版本"
              onConfirm={() => onRestorePreviousFormal()}
              disabled={editLoading}
            >
              <Button size="small" disabled={editLoading}>恢复上一版本</Button>
            </Popconfirm>
          )}
          <span>{data.locations.length} 地点</span>
          <span>{data.sources.length} 来源</span>
          <span>{issues.length} 校验</span>
        </div>
      </div>

      {!mobileSimpleEditing && <div className="trip-workspace-actions">
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
      </div>}

      {detailEntry ? (
        <section
          className={`trip-workspace-detail${mobileSimpleEditing ? ' trip-workspace-detail--mobile' : ''}`}
          aria-label={`${detailEntry.location.name}详情`}
        >
          <div className="trip-workspace-detail-actions">
            <Button
              size="small"
              type="text"
              icon={<LeftOutlined />}
              onClick={closeActivityDetail}
              aria-label={`返回 Day ${detailEntry.day.day} 日程`}
            >
              {mobileSimpleEditing ? '返回日程' : `返回 Day ${detailEntry.day.day} 日程`}
            </Button>
            {mobileSimpleEditing && (
              <span className="trip-workspace-detail-context">Day {detailEntry.day.day} · 活动详情</span>
            )}
            <Button
              size="small"
              type="text"
              icon={<EnvironmentOutlined />}
              onClick={() => selectLocationOnMap(detailEntry.location)}
              disabled={!Number.isFinite(detailEntry.location.lat) || !Number.isFinite(detailEntry.location.lng)}
              aria-label="在地图中查看"
            >
              {mobileSimpleEditing ? '地图' : '在地图中查看'}
            </Button>
          </div>
          <PoiDetailContent location={buildDetailLocation(detailEntry)} />
        </section>
      ) : (
      <Tabs
        size="small"
        className="trip-workspace-tabs"
        activeKey={activeWorkspaceView}
        onChange={(view) => setActiveWorkspaceView(view as WorkspaceView)}
        items={[
          {
            key: 'overview',
            label: '概览',
            children: <div className="trip-product-overview">
              {flexibleDates && (
                <div className="trip-flexible-date-note">
                  当前为日期待定参考方案。日程按 Day 1 至 Day N 展示；确定日期后再补充具体班次、票价、余票、天气和时效性预约信息。
                </div>
              )}
              <section className="trip-workspace-overview-summary" aria-labelledby="trip-workspace-summary-heading">
                <h4 id="trip-workspace-summary-heading">当前方案</h4>
                <dl className="trip-workspace-summary-facts">
                  <div><dt>行程</dt><dd>{tripDays.length || document.intent?.days || 0} 天 · {activityCount} 项活动</dd></div>
                  <div><dt>预算</dt><dd>{data.budget ? formatMoney(data.budget.budget_total, currency) : '待确认'}</dd></div>
                  <div><dt>提醒</dt><dd>{visibleReminderItems.length + unresolvedIssueCount} 项待关注</dd></div>
                </dl>
              <div className="trip-workspace-edit-state" role={draftHardErrors.length > 0 ? 'alert' : 'status'}>
                  <Tag color={workspaceState.color}>{workspaceState.label}</Tag>
                  {hasDraft && workspaceState.label !== '发现未应用修改' && <Tag color="orange">发现未应用修改</Tag>}
                  <span>{workspaceState.detail}</span>
                </div>
              </section>
              <section aria-labelledby="trip-workspace-day-summary-heading">
                <h4 id="trip-workspace-day-summary-heading">每日安排</h4>
                {tripDays.length > 0 ? (
                  <ul className="trip-workspace-day-summary">
                    {tripDays.map((day) => (
                      <li key={day.id}>
                        <button type="button" onClick={() => openDayTimeline(day.id)} aria-label={`查看 Day ${day.day} 日程`}>
                          <strong>Day {day.day}</strong>
                          <span>{day.theme || '主题待补充'} · {day.activities.length} 项活动</span>
                          {formatActivityCost(day.estimated_cost, currency) && <small>当日约 {formatActivityCost(day.estimated_cost, currency)}</small>}
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : <p>分日行程尚未生成。</p>}
              </section>
              <section aria-labelledby="trip-workspace-notes-heading">
                <h4 id="trip-workspace-notes-heading">整体备注</h4>
                {isV3Document && onEditOperation && <Button size="small" disabled={editLoading} onClick={() => setEditTarget({ type: 'update_trip_note', title: '编辑整体备注', content: tripNotes.map((note: Record<string, any>) => note.content).join('\n') })}>编辑整体备注</Button>}
                {tripNotes.length > 0
                  ? <ul className="trip-workspace-notes">{tripNotes.map((note: Record<string, any>) => <li key={note.note_id}>{note.content}</li>)}</ul>
                  : <p>暂无整体备注。</p>}
              </section>
              <section>
                <h4>目的地</h4>
                {coverImage && <TravelImageAsset image={coverImage} className="trip-cover-image" imageLabel={`${document.destination_overview?.name_zh || '目的地'}封面`} />}
                <strong>{document.destination_overview?.name_zh || '目的地待确认'}</strong>
                <p>{document.destination_overview?.area_overview || document.destination_overview?.summary || document.destination_overview?.status_reason || '正式方案生成后将展示目的地简介。'}</p>
              </section>
              {!flexibleDates && <section><h4>去程交通</h4>{transportSummary(document.outbound_transport, 'outbound')}</section>}
              <section>
                <h4>酒店推荐</h4>
                {lodgingOptions.length ? lodgingOptions.slice(0, 3).map((hotel: Record<string, any>) => {
                  const lodgingId = hotel.hotel_id || hotel.lodging_id;
                  const isPlanningLodging = document.lodging_plan?.planning_lodging_id === lodgingId;
                  return (
                    <div className="trip-product-summary-row" key={lodgingId}>
                      <strong>{hotel.name}</strong>
                      <span>{hotel.area || '区域待确认'}{flexibleDates ? '' : ` · 每晚 ${moneyDisplay(hotel.nightly_price, hotel.currency || 'CNY')}`}</span>
                      <small>来源：{(hotel.source_refs || [hotel.source_reference_id]).map((id: string) => document.sources?.find((source: Record<string, any>) => source.source_id === id)?.title).filter(Boolean).join('、') || '暂无可靠来源'} · 更新：{hotel.updated_at || '待确认'}</small>
                      {isV3Document && !mobileSimpleEditing && onEditOperation && lodgingId && (
                        <div className="trip-product-row-action">
                          {isPlanningLodging && <Tag color="green">规划住宿点</Tag>}
                          <Button
                            size="small"
                            type={isPlanningLodging ? 'text' : 'default'}
                            disabled={editLoading || isPlanningLodging}
                            onClick={() => onEditOperation('select_lodging_option', { lodging_id: lodgingId })}
                          >
                            {isPlanningLodging ? '当前规划住宿点' : '设为规划住宿点'}
                          </Button>
                        </div>
                      )}
                    </div>
                  );
                }) : <p>{document.hotel_recommendations?.status_reason || document.lodging_plan?.status_reason || '暂无可靠实时数据'}</p>}
              </section>
              {!flexibleDates && <section><h4>返程交通</h4>{transportSummary(document.return_transport, 'return')}</section>}
              <section>
                <h4>预算概览</h4>
                {data.budget ? <>
                  <div className="trip-budget-grid">
                    <div><span>总预算</span><strong>{formatMoney(data.budget.budget_total, currency)}</strong></div>
                    <div><span>可统计费用</span><strong>{formatMoney(data.budget.known_total ?? data.budget.estimated_total, currency)}</strong></div>
                    <div><span>住宿</span><strong>{formatMoney(data.budget.categories?.accommodation ?? data.budget.categories?.lodging, currency)}</strong></div>
                    <div><span>交通</span><strong>{formatMoney(data.budget.categories?.transport, currency)}</strong></div>
                  </div>
                  {budgetCurrencyDetails.length > 0 && (
                    <ul className="trip-budget-source">
                      {budgetCurrencyDetails.map((entry, index) => (
                        <li key={`${entry.category}-${index}`}>{({ activities: '活动门票', food: '餐饮', lodging: '住宿', transport: '交通', shopping: '购物', other: '其他' } as Record<string, string>)[entry.category] || '其他费用'}：{moneyDisplay(entry.amount, currency)}</li>
                      ))}
                    </ul>
                  )}
                  {data.budget.traveler_costs?.length ? (
                    <p className="trip-budget-source">
                      {data.budget.traveler_costs.filter((item) => item.count > 0).map((item) => {
                        const labels = { adult: '成人', child: '儿童', senior: '老人' };
                        return `${labels[item.traveler_type]} ${item.count} 人：${moneyDisplay(item.estimated_total, currency)}（${item.pricing_status === 'official_discount_verified' ? '官方优惠' : item.pricing_status === 'adult_price_assumed' ? '优惠待确认，按成人价' : '标准价'}）`;
                      }).join('；')}
                    </p>
                  ) : null}
                  {data.budget.over_budget && <div className="trip-budget-warning" role="alert"><ExclamationCircleOutlined /> 预计超出预算 {formatMoney(data.budget.overrun_amount, currency)}</div>}
                  <p className="trip-budget-source">{data.budget.source_label || '金额为规划估算，待对应来源确认'}</p>
                </> : <p>正式方案生成后将展示预算拆分。</p>}
              </section>
            </div>,
          },
          {
            key: 'timeline',
            label: '日程',
            children: tripDays.length > 0 ? (
              <Tabs
                size="small"
                className="trip-day-tabs"
                activeKey={effectiveActiveDayId}
                onChange={setActiveDayId}
                items={tripDays.map((day) => ({
                  key: day.id,
                  label: <span onDragEnter={() => { if (draggedItem) setActiveDayId(day.id); }}>{formatDayTabLabel(day)}</span>,
                  children: (
                    <div className="trip-timeline" aria-label={`Day ${day.day} 行程`}>
                      {isV3Document && !mobileSimpleEditing && onEditOperation && <SpaceForEditing
                        day={day.day} disabled={editLoading}
                        note={(document.notes || []).filter((note: Record<string, any>) => note.scope === 'day' && (note.target_id === String(day.day) || note.target_id === day.id)).map((note: Record<string, any>) => note.content).join('\n')}
                        onEdit={setEditTarget} />}
                      {canDrag && candidatePool.length > 0 && <details className="trip-candidate-tray">
                        <summary>候选地点（{candidatePool.length}）· 可拖到日程空档</summary>
                        {candidatePool.map((candidate: Record<string, any>) => <span key={candidate.activity_id} draggable
                          onDragStart={(event) => startActivityDrag(event, candidate.activity_id, true)}
                          onDragEnd={() => setDraggedItem(null)} className="trip-candidate-drag-item">{candidate.title || candidate.place?.name}</span>)}
                      </details>}
                      <div className="trip-day-group">
                        {(day.theme || day.estimated_cost !== null || isV3Document) && (
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
                            {isV3Document && !mobileSimpleEditing && (
                              <Button
                                size="small"
                                icon={<CompassOutlined />}
                                loading={isRouteOptimizationLoading}
                                disabled={!onEditOperation || !onGetDayRouteOptimizationPreview || editLoading || isRouteOptimizationLoading}
                                onClick={() => { void requestDayRouteOptimization(day.day); }}
                                aria-label={`优化 Day ${day.day} 当天路线`}
                              >
                                优化当天路线
                              </Button>
                            )}
                          </div>
                        )}
                        {day.activities.length > 0 ? day.activities.map((activity, index) => {
                          const location = getActivityLocation(activity, index);
                          const entry: ActivityEntry = { activity, day, index, location };
                          const placeName = activity.place?.name || activity.title || `活动 ${index + 1}`;
                          const activityName = activity.title || placeName;
                          const stayDuration = formatStayDuration(
                            activity.start_time,
                            activity.end_time,
                            activity.duration_minutes,
                          );
                          const activityCost = formatActivityCost(activity.estimated_cost, currency);
                          const category = formatActivityCategory(activity.place?.category);
                          const activityImages = displayableImageAssets(activity.images, 3);
                          const timeRange = activity.start_time
                            ? `${activity.start_time}${activity.end_time ? `-${activity.end_time}` : ''}`
                            : null;
                          return (
                            <React.Fragment key={activity.id}>
                              <article
                                onDragOver={(event) => { if (canDrag && draggedItem) event.preventDefault(); }}
                                onDrop={(event) => dropActivity(event, day.day, index)}
                                ref={(node) => { activityCardRefs.current[activity.id] = node; }}
                                data-activity-id={activity.id}
                                className={`trip-activity-card${activityImages.length ? ' trip-activity-card--with-image' : ''}${selectedActivityEntry?.activity.id === activity.id ? ' trip-activity-card--selected' : ''}`}
                              >
                                {activityImages.length > 0 && (
                                  <TravelImageWithFallback
                                    images={activityImages}
                                    className="trip-activity-card-image"
                                    imageLabel={`${placeName}图片`}
                                  />
                                )}
                                <button
                                  type="button"
                                  className="trip-activity-card-open"
                                  onClick={() => openActivityDetail(entry)}
                                  aria-label={`查看${activityName}详情并定位地图`}
                                >
                                  <span className="trip-activity-card-content">
                                    <span className="trip-activity-card-heading">
                                      <span className="trip-activity-card-sequence" aria-label={`第 ${index + 1} 项活动`}>
                                        {String(index + 1).padStart(2, '0')}
                                      </span>
                                      <span>
                                        <strong>{activityName}</strong>
                                        {activity.title && activity.title !== placeName && (
                                          <span className="trip-activity-card-place">{placeName}</span>
                                        )}
                                      </span>
                                    </span>
                                    <span className="trip-activity-card-meta">
                                      {timeRange && <small>{timeRange}</small>}
                                      {stayDuration && <small>{stayDuration}</small>}
                                      {category && <Tag>{category}</Tag>}
                                      {activityCost && <Tag color="green">{activityCost}</Tag>}
                                      {activity.fixed_time && <Tag color="magenta">固定时间</Tag>}
                                    </span>
                                    {activity.notes.length > 0 && (
                                      <span className="trip-activity-card-note">
                                        <span>备注</span>{activity.notes.join('；')}
                                      </span>
                                    )}
                                  </span>
                                </button>
                                {renderLocationActions(location, activity, index, day)}
                                {editingActivityNoteId === activity.id && (
                                  <form
                                    className="trip-activity-note-editor"
                                    onSubmit={async (event) => {
                                      event.preventDefault();
                                      const succeeded = await onEditOperation?.('update_activity_note', {
                                        activity_id: activity.id,
                                        content: editingActivityNoteText,
                                      });
                                      if (succeeded !== false) setEditingActivityNoteId(null);
                                    }}
                                  >
                                    <Input
                                      aria-label={`${activityName}活动备注`}
                                      value={editingActivityNoteText}
                                      maxLength={500}
                                      placeholder="补充集合时间、预约或个人提醒"
                                      onChange={(event) => setEditingActivityNoteText(event.target.value)}
                                    />
                                    <Button htmlType="submit" size="small" type="primary" loading={editLoading}>保存备注</Button>
                                    <Button size="small" type="text" onClick={() => setEditingActivityNoteId(null)}>取消</Button>
                                  </form>
                                )}
                              </article>
                              {index < day.activities.length - 1 && renderRouteLeg(activity.route_to_next, currency)}
                            </React.Fragment>
                          );
                        }) : (
                          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={(() => {
                            const formalDay = document.itinerary?.days?.find((item: any) => item.day === day.day);
                            const anchors = (formalDay?.anchors || []).map((anchor: any) => anchor.display_label).filter(Boolean);
                            return anchors.length
                              ? `当天未安排游览活动；接驳：${anchors.join(' → ')}。`
                              : '当天暂无活动';
                          })()} />
                        )}
                        {canDrag && draggedItem && <div className="trip-drop-slot" onDragOver={(event) => event.preventDefault()}
                          onDrop={(event) => dropActivity(event, day.day, day.activities.length)}>拖到这里加入当天末尾</div>}
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
            key: 'candidates',
            label: '候选',
            children: <div className="trip-workspace-view-section">
              <div className="trip-workspace-view-heading"><h4>候选地点</h4><span>未排期 · 不连接正式路线</span></div>
              {candidatePool.length > 0 ? (
                <div className="trip-candidate-list">
                  {candidatePool.map((candidate: Record<string, any>, index: number) => {
                    const place = candidate.place && typeof candidate.place === 'object' ? candidate.place : {};
                    const name = place.name || candidate.title || `候选地点 ${index + 1}`;
                    const candidateId = candidate.activity_id || candidate.id;
                    const insertionOptions = Array.isArray(candidate.insertion_options)
                      ? candidate.insertion_options.filter((option: Record<string, any>) => (
                        Number.isInteger(option?.day)
                        && typeof option?.start_at === 'string'
                        && typeof option?.end_at === 'string'
                      ))
                      : [];
                    const candidateMenuItems = insertionOptions.map((option: Record<string, any>) => ({
                      key: String(option.day),
                      label: `加入 Day ${option.day} · ${option.start_at}-${option.end_at}`,
                    }));
                    const insertionUnavailableReason = typeof candidate.insertion_unavailable_reason === 'string'
                      ? candidate.insertion_unavailable_reason.trim()
                      : '';
                    return <div className="trip-candidate-row" key={candidateId || `${name}-${index}`}>
                      <div>
                        <strong>{name}</strong>
                        <span>{formatActivityCategory(place.category || candidate.kind) || '类型待确认'}</span>
                        {candidate.title && candidate.title !== name && <p>{candidate.title}</p>}
                        {insertionOptions.length > 0 && <p className="trip-candidate-slot-hint">可排入：{candidateMenuItems.map((item) => item.label).join('；')}</p>}
                        {insertionOptions.length === 0 && <p className="trip-candidate-slot-hint trip-candidate-slot-hint--blocked">{insertionUnavailableReason || '可行空档待重新核验，暂不自动排入。'}</p>}
                      </div>
                      {isV3Document && !mobileSimpleEditing && onEditOperation && candidateId && (
                        <Dropdown
                          trigger={['click']}
                          disabled={editLoading || candidateMenuItems.length === 0}
                          menu={{
                            items: candidateMenuItems,
                            onClick: ({ key }) => onEditOperation('insert_candidate', {
                              activity_id: candidateId,
                              target_day: Number(key),
                            }),
                          }}
                        >
                          <Button
                            size="small"
                            icon={<CalendarOutlined />}
                            disabled={editLoading || candidateMenuItems.length === 0}
                            aria-label={`将${name}排入行程`}
                            title={candidateMenuItems.length === 0 ? (insertionUnavailableReason || '暂无可行时间空档') : '选择已核验的可行时间空档'}
                          >
                            排入行程
                          </Button>
                        </Dropdown>
                      )}
                    </div>;
                  })}
                </div>
              ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前正式方案没有可展示的候选地点" />}
            </div>,
          },
          {
            key: 'checklist',
            label: '准备清单',
            children: <div className="trip-workspace-view-section">
              <div className="trip-workspace-view-heading"><h4>准备清单</h4><span>{pendingChecklistCount} 项待处理</span></div>
              {checklistItems.length > 0 ? (
                <div className="trip-checklist-list">
                  {checklistItems.map((item: Record<string, any>, index: number) => <div className="trip-checklist-row" key={item.action_id || `${item.text}-${index}`}><span>{item.text || '待确认事项'}</span><Tag color={item.status === 'completed' ? 'green' : item.status === 'overdue' ? 'red' : 'blue'}>{item.status === 'completed' ? '已完成' : item.status === 'overdue' ? '已逾期' : '待处理'}</Tag></div>)}
                </div>
              ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有待准备事项" />}
            </div>,
          },
          {
            key: 'reminders',
            label: '提醒',
            children: <div className="trip-workspace-view-section trip-validation-panel">
              <div className="trip-workspace-view-heading"><h4>提醒与风险</h4><span>{visibleReminderItems.length + visibleIssues.length} 项信息</span></div>
              <div className="trip-validation-status">
                {hasAutomaticRepair && <Tag color="green" icon={<CheckCircleOutlined />}>已自动修复{resolvedIssueCount > 0 ? ` ${resolvedIssueCount} 项` : ''}</Tag>}
                {draftHardErrors.length > 0 && <Tag color="red" icon={<ExclamationCircleOutlined />}>应用前需修正 {draftHardErrors.length} 项</Tag>}
                {needsConfirmation && <Tag color="orange" icon={<ExclamationCircleOutlined />}>仍需确认 {Math.max(remainingIssueCount, draftSoftWarnings.length)} 项</Tag>}
                {!hasRepairStatus && (data.validation?.valid === false ? <Tag color="red" icon={<ExclamationCircleOutlined />}>需修正</Tag> : <Tag color="green" icon={<CheckCircleOutlined />}>可继续规划</Tag>)}
              </div>
              {visibleReminderItems.length > 0 && <div className="trip-reminder-list">{visibleReminderItems.map((item: Record<string, any>, index: number) => <p key={`${item.category || item.kind}-${index}`}>{item.content || item.text}</p>)}</div>}
              {visibleIssues.length > 0 ? <div className="trip-issue-list">{visibleIssues.map((issue, index) => <div className="trip-issue-row" key={`${issue.code}-${issue.target_id || index}`}><Tag color={severityColor(issue.severity)}>{issue.severity || 'info'}</Tag><div><strong>{issue.message || issue.code || '校验问题'}</strong>{issue.repair_hint && <span>{issue.repair_hint}</span>}</div></div>)}</div> : visibleReminderItems.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无出行提醒或校验问题" />}
            </div>,
          },
        ]}
      />
      )}
    </section>
  );
};

export default TripWorkspace;
