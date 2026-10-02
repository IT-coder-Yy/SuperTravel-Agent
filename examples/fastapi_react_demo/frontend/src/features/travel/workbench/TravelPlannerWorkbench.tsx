import { useEffect, useMemo, useState } from 'react';
import { CalendarOutlined, LeftOutlined, EditOutlined, EnvironmentOutlined, DownOutlined } from '@ant-design/icons';
import { Button, Drawer, Segmented } from 'antd';
import MapLocationQuickView from '../../../components/MapLocationQuickView';
import MapComponent, { type DayRouteGeometry } from '../../../components/MapComponent';
import TripWorkspace, {
  type TripActivityDeleteImpact,
  type TripDayRouteOptimizationPreview,
  type TripWorkspaceDetailContext,
  type TripDraftValidation,
  type TripWorkspaceState,
} from '../../../components/TripWorkspace';
import { displayableImageAssets, TravelImageWithFallback } from '../conversation/TravelImageAsset';
import type { TravelPlannerLocation } from '../state/travelPlannerReducer';

interface LocationGroup {
  groupId: string;
  title: string;
  locations: TravelPlannerLocation[];
}

type MapLayer = 'overview' | 'day' | 'candidates';

const asRecord = (value: unknown): Record<string, unknown> | null => (
  typeof value === 'object' && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
);

const textValue = (value: unknown): string => (
  typeof value === 'string' ? value.trim() : ''
);

const mapGroupDay = (group: LocationGroup): number | null => {
  const dayMatch = group.title.match(/(?:第\s*(\d+)\s*天|Day\s*(\d+))/i);
  const day = Number(dayMatch?.[1] || dayMatch?.[2]);
  return Number.isInteger(day) && day > 0 ? day : null;
};

const mapFilterLabel = (group: LocationGroup) => {
  const day = mapGroupDay(group);
  return day ? `Day ${day}` : group.title;
};

/** 候选只在拥有稳定活动 ID、POI ID 与有效坐标时进入地图，绝不补造坐标或路线。 */
export const buildCandidateMapLocations = (
  document: Record<string, unknown> | null,
): TravelPlannerLocation[] => {
  const candidates = Array.isArray(document?.candidate_pool) ? document.candidate_pool : [];
  const seenActivityIds = new Set<string>();
  return candidates.flatMap((candidate, index) => {
    const activity = asRecord(candidate);
    const place = asRecord(activity?.place);
    const coordinates = asRecord(place?.coordinates);
    const activityId = textValue(activity?.activity_id);
    const poiId = textValue(place?.poi_id);
    const name = textValue(place?.name);
    const lat = Number(coordinates?.latitude);
    const lng = Number(coordinates?.longitude);
    if (!activityId || !poiId || !name || seenActivityIds.has(activityId)
      || !Number.isFinite(lat) || !Number.isFinite(lng)) return [];
    seenActivityIds.add(activityId);
    const title = textValue(activity?.title);
    const summary = textValue(place?.summary);
    const address = textValue(place?.address);
    return [{
      id: activityId,
      poi_id: poiId,
      name,
      lat,
      lng,
      description: summary || (title && title !== name ? title : '') || address || undefined,
      category: textValue(place?.category) || undefined,
      day: '候选',
      order: index + 1,
    }];
  });
};

export interface TravelPlannerWorkbenchProps {
  sidePanelRef: React.RefObject<HTMLDivElement>;
  hasTripWorkspaceData: boolean;
  sidePanelMapPercent: number;
  effectiveMapLocations: TravelPlannerLocation[];
  candidateMapLocations: TravelPlannerLocation[];
  dayRoutes: DayRouteGeometry[];
  activeMapGroupId: string;
  selectedLocationId: string;
  mapLocationGroups: LocationGroup[];
  workspaceLocationGroups: LocationGroup[];
  tripWorkspace: TripWorkspaceState;
  activeTripDocument: Record<string, unknown> | null;
  hasActiveTripPlan: boolean;
  mobileSimpleEditing?: boolean;
  canUndoEdit: boolean;
  hasDraft?: boolean;
  draftValidation?: TripDraftValidation | null;
  editLoading: boolean;
  onStartResize: (event: React.MouseEvent<HTMLDivElement>) => void;
  onSelectGroup: (groupId: string) => void;
  onSelectLocation: (locationId: string) => void;
  onEditOperation?: (type: string, payload: Record<string, unknown>) => void | boolean | Promise<void | boolean>;
  onGetActivityDeleteImpact?: (activityId: string) => Promise<TripActivityDeleteImpact | null>;
  onGetDayRouteOptimizationPreview?: (day: number) => Promise<TripDayRouteOptimizationPreview | null>;
  onUndoEdit: () => void;
  onDiscardDraft?: () => void;
  onApplyDraft?: () => void | Promise<unknown>;
  canRestorePreviousFormal?: boolean;
  onRestorePreviousFormal?: () => void | Promise<unknown>;
  onFocusMap: () => void;
  onFocusWorkspace: () => void;
  onQuickAction: (prompt: string) => void;
}

const TravelPlannerWorkbench = ({
  sidePanelRef,
  hasTripWorkspaceData,
  effectiveMapLocations,
  candidateMapLocations,
  dayRoutes,
  activeMapGroupId,
  selectedLocationId,
  mapLocationGroups,
  workspaceLocationGroups,
  tripWorkspace,
  activeTripDocument,
  hasActiveTripPlan,
  mobileSimpleEditing = false,
  canUndoEdit,
  hasDraft = false,
  draftValidation = null,
  editLoading,
  onSelectGroup,
  onSelectLocation,
  onEditOperation,
  onGetActivityDeleteImpact,
  onGetDayRouteOptimizationPreview,
  onUndoEdit,
  onDiscardDraft,
  onApplyDraft,
  canRestorePreviousFormal = false,
  onRestorePreviousFormal,
  onFocusMap,
  onFocusWorkspace,
  onQuickAction,
}: TravelPlannerWorkbenchProps) => {
  const [mapReturnContext, setMapReturnContext] = useState<TripWorkspaceDetailContext | null>(null);
  const [returnToDetailRequestId, setReturnToDetailRequestId] = useState(0);
  const [editorOpen, setEditorOpen] = useState(false);
  const [cardCollapsed, setCardCollapsed] = useState(false);
  const [summaryDay, setSummaryDay] = useState<number | null>(null);
  const [mapLayer, setMapLayer] = useState<MapLayer>('overview');
  useEffect(() => {
    const place = effectiveMapLocations.find(item => item.id === selectedLocationId);
    const day = Number(String(place?.day ?? '').match(/\d+/)?.[0]);
    if (day) setSummaryDay(day);
  }, [selectedLocationId, effectiveMapLocations]);
  const dayGroups = useMemo(
    () => mapLocationGroups.filter((group) => mapGroupDay(group) !== null),
    [mapLocationGroups],
  );
  const activeDayGroup = useMemo(
    () => dayGroups.find((group) => group.groupId === activeMapGroupId) || dayGroups[0] || null,
    [activeMapGroupId, dayGroups],
  );
  const activeDay = activeDayGroup ? mapGroupDay(activeDayGroup) : null;
  useEffect(() => {
    if ((mapLayer === 'candidates' && candidateMapLocations.length === 0)
      || (mapLayer === 'day' && !activeDayGroup)) {
      setMapLayer('overview');
    }
  }, [activeDayGroup, candidateMapLocations.length, mapLayer]);
  const mapLayerData = useMemo(() => {
    if (mapLayer === 'candidates') {
      return { locations: candidateMapLocations, routes: [], activeGroupId: '' };
    }
    if (mapLayer === 'day' && activeDayGroup && activeDay !== null) {
      return {
        locations: activeDayGroup.locations,
        routes: dayRoutes.filter((route) => route.day === activeDay),
        activeGroupId: activeDayGroup.groupId,
      };
    }
    return { locations: effectiveMapLocations, routes: dayRoutes, activeGroupId: '' };
  }, [activeDay, activeDayGroup, candidateMapLocations, dayRoutes, effectiveMapLocations, mapLayer]);

  const returnToActivity = () => {
    if (!mapReturnContext) return;
    setEditorOpen(true);
    onFocusWorkspace();
    setReturnToDetailRequestId((value) => value + 1);
  };

  const selectMapLayer = (value: string | number) => {
    const nextLayer = value as MapLayer;
    setMapLayer(nextLayer);
    onSelectLocation('');
    if (nextLayer === 'day' && activeDayGroup) {
      onSelectGroup(activeDayGroup.groupId);
      return;
    }
    onSelectGroup('');
  };

  const days = tripWorkspace.days || [];
  const currentDay = days.find(day => day.day === (mapLayer === 'day' ? activeDay : summaryDay)) || days[0];
  const visibleLocations = mapLayer === 'candidates' ? candidateMapLocations
    : currentDay ? effectiveMapLocations.filter(place => Number(place.day) === currentDay.day)
    : activeDayGroup?.locations || effectiveMapLocations;
  const items = mapLayer !== 'candidates' && currentDay?.activities.length
    ? currentDay.activities.map(activity => {
      const location = effectiveMapLocations.find(place => {
        const placeDay = Number(String(place.day ?? '').match(/\d+/)?.[0]);
        if (placeDay && placeDay !== activity.day) return false;
        return place.id === activity.id
          || Boolean(activity.place?.poi_id && (place.poi_id === activity.place.poi_id || place.id === activity.place.poi_id))
          || place.name === activity.place?.name;
      });
      return { id: activity.id, locationId: location?.id, name: activity.place?.name || activity.title,
        time: activity.start_time, note: activity.notes[0] || activity.title, images: [...(tripWorkspace.locations.find(place => place.id === location?.id)?.image_assets || []), ...(activity.images || [])] };
    })
    : visibleLocations.map(place => ({ id: place.id, locationId: place.id, name: place.name,
      time: null, note: place.description || '', images: tripWorkspace.locations.find(item => item.id === place.id)?.image_assets }));
  const images = displayableImageAssets(items.flatMap(item => Array.isArray(item.images) ? item.images : []), 3);
  const intent = asRecord(activeTripDocument?.intent);
  const overview = asRecord(activeTripDocument?.destination_overview);
  const destination = textValue(intent?.destination) || textValue(overview?.name_zh);
  const selectedPlace = mapLayerData.locations.find(place => place.id === selectedLocationId);
  const selectDay = (day: number) => {
    setSummaryDay(day);
    onSelectLocation('');
    const group = dayGroups.find(item => mapGroupDay(item) === day);
    if (group) { setMapLayer('day'); onSelectGroup(group.groupId); }
    else { setMapLayer('overview'); onSelectGroup(''); }
  };
  return (
    <div className="trip-side-panel c1-workbench" ref={sidePanelRef}>
      <div className="trip-map-panel c1-map-surface">
        <MapComponent showPopups={false} width="100%" height="100%" locations={mapLayerData.locations} dayRoutes={mapLayerData.routes}
          activeGroupId={mapLayerData.activeGroupId} selectedLocationId={selectedLocationId}
          locationGroups={mapLocationGroups.map(group => ({ id: group.groupId, title: group.title, locations: group.locations }))}
          onSelectLocation={onSelectLocation} />
      </div>
      <div className="c1-map-wash" />
      {selectedPlace && <div className="c1-map-detail"><MapLocationQuickView location={selectedPlace} onClose={() => onSelectLocation('')} /></div>}
      <div className="c1-destination-copy">
        <span>{destination || '我的旅程'} · TRAVEL JOURNAL</span>
        <h1>在风景之间，<br />留一点空白。</h1>
        <p>{days.length ? `${days.length} 天旅程，把日子过慢一些。` : '好好计划，也给惊喜留些余地。'}</p>
      </div>
      {(effectiveMapLocations.length > 0 || candidateMapLocations.length > 0) && <div className="trip-map-layer-control c1-map-tabs" aria-label="地图图层">
        <Segmented size="small" value={mapLayer} options={[
          { label: '总览', value: 'overview' }, { label: '单日', value: 'day', disabled: dayGroups.length === 0 },
          { label: '候选', value: 'candidates', disabled: candidateMapLocations.length === 0 },
        ]} onChange={selectMapLayer} />
        {mapLayer === 'day' && activeDayGroup && <Segmented size="small" aria-label="选择地图日期" value={activeDayGroup.groupId}
          options={dayGroups.map(group => ({ label: mapFilterLabel(group), value: group.groupId }))}
          onChange={value => { onSelectLocation(''); onSelectGroup(String(value)); }} />}
      </div>}
      {hasTripWorkspaceData ? <section className={`c1-itinerary ${cardCollapsed ? 'is-collapsed' : ''}`} aria-label="每日行程">
        <div className="c1-itinerary-heading"><span><CalendarOutlined /> 每日行程{hasDraft && <b className="c1-draft-label">草稿待确认</b>}</span>
          <Button type="text" size="small" aria-label={cardCollapsed ? '展开每日行程' : '收起每日行程'} aria-expanded={!cardCollapsed} icon={<DownOutlined rotate={cardCollapsed ? 180 : 0} />} onClick={() => setCardCollapsed(!cardCollapsed)} />
        </div>
        {!cardCollapsed && <>
          {images.length > 0 && <div className="c1-itinerary-cover"><TravelImageWithFallback key={currentDay?.id || mapLayer} images={images} imageLabel={items[0]?.name || '行程图片'} /></div>}
          <div className="c1-itinerary-intro"><span>{mapLayer === 'candidates' ? '尚未排入行程' : currentDay?.date || '慢慢走，好好看'}</span>
            <h2>{mapLayer === 'candidates' ? '沿途，还有这些选择' : currentDay?.theme || destination || '沿途的风景'}</h2>
          </div>
          {days.length > 0 && mapLayer !== 'candidates' && <div className="c1-day-strip" aria-label="每日行程日期">
            {days.map(day => <button key={day.id} className={currentDay?.day === day.day ? 'is-active' : ''} aria-pressed={currentDay?.day === day.day} onClick={() => selectDay(day.day)}>第 {day.day} 天</button>)}
          </div>}
          <ol className="c1-stop-list">
            {items.map((item, index) => <li key={item.id}>
              <button className={item.locationId && selectedLocationId === item.locationId ? 'is-active' : ''}
                aria-pressed={Boolean(item.locationId && selectedLocationId === item.locationId)}
                onClick={() => { if (item.locationId) { onSelectLocation(item.locationId); onFocusMap(); } else setEditorOpen(true); }}>
                <span className="c1-stop-number">{index + 1}</span><span className="c1-stop-copy"><strong>{item.name}</strong><small>{item.time || '时间待安排'}{!item.locationId && ' · 位置待确认'}{item.note && ` · ${item.note}`}</small></span><EnvironmentOutlined />
              </button>
            </li>)}
            {items.length === 0 && <li className="c1-empty">这一天还没有安排地点，可在编辑行程中添加。</li>}
          </ol>
          <footer className="c1-itinerary-footer"><Button icon={<EditOutlined />} block onClick={() => setEditorOpen(true)}>编辑行程</Button><span>预算、预约、备选地点与行前清单</span></footer>
        </>}
      </section> : <div className="c1-map-empty" role="status"><CalendarOutlined /><strong>旅程正在展开</strong><span>行程生成后会在这里按日期整理。</span></div>}
      {mapReturnContext && <Button className="trip-map-return" size="small" icon={<LeftOutlined />} onClick={returnToActivity}>{mapReturnContext.label}</Button>}
      <Drawer title="行程详情与编辑" open={editorOpen} width="min(760px, 100vw)" onClose={() => setEditorOpen(false)} rootClassName="c1-editor-drawer">
        <div className="trip-workspace-shell">
          <TripWorkspace
            data={tripWorkspace}
            document={activeTripDocument}
            locationGroups={workspaceLocationGroups.map((group) => ({
              groupId: group.groupId,
              title: group.title,
              locations: group.locations,
              sourceKind: group.groupId.startsWith('structured_trip_workspace')
                ? 'current_plan'
                : 'historical_answer',
            }))}
            activeGroupId={activeMapGroupId}
            selectedLocationId={selectedLocationId}
            onSelectGroup={onSelectGroup}
            onSelectLocation={onSelectLocation}
            onEditOperation={hasActiveTripPlan ? onEditOperation : undefined}
            onGetActivityDeleteImpact={hasActiveTripPlan ? onGetActivityDeleteImpact : undefined}
            onGetDayRouteOptimizationPreview={hasActiveTripPlan && !mobileSimpleEditing ? onGetDayRouteOptimizationPreview : undefined}
            onUndoEdit={onUndoEdit}
            canUndoEdit={canUndoEdit}
            hasDraft={hasDraft}
            draftValidation={draftValidation}
            onDiscardDraft={onDiscardDraft}
            onApplyDraft={onApplyDraft}
            canRestorePreviousFormal={canRestorePreviousFormal}
            onRestorePreviousFormal={onRestorePreviousFormal}
            editLoading={editLoading}
            onFocusMap={() => { setEditorOpen(false); onFocusMap(); }}
            onDetailContextChange={setMapReturnContext}
            returnToDetailRequestId={returnToDetailRequestId}
            onQuickAction={mobileSimpleEditing ? undefined : (prompt) => { setEditorOpen(false); onQuickAction(prompt); }}
            mobileSimpleEditing={mobileSimpleEditing}
          />
        </div>
      </Drawer>
    </div>
  );
};
export default TravelPlannerWorkbench;
