import { useEffect, useMemo, useState } from 'react';
import { CalendarOutlined, LeftOutlined } from '@ant-design/icons';
import { Button, Segmented } from 'antd';
import MapComponent, { type DayRouteGeometry } from '../../../components/MapComponent';
import TripWorkspace, {
  type TripActivityDeleteImpact,
  type TripDayRouteOptimizationPreview,
  type TripWorkspaceDetailContext,
  type TripDraftValidation,
  type TripWorkspaceState,
} from '../../../components/TripWorkspace';
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
  sidePanelMapPercent,
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
  onStartResize,
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
  const [mapLayer, setMapLayer] = useState<MapLayer>('overview');
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

  return (
  <div className="trip-side-panel" ref={sidePanelRef}>
    <div
      className={hasTripWorkspaceData ? 'trip-map-panel trip-map-panel-with-workspace' : 'trip-map-panel'}
      style={hasTripWorkspaceData
        ? { flex: `0 0 calc(${sidePanelMapPercent}% - 4px)` }
        : { flex: '1 1 100%' }}
    >
      {(effectiveMapLocations.length > 0 || candidateMapLocations.length > 0) && (
        <div className="trip-map-layer-control" aria-label="地图图层">
          <span>地图图层</span>
          <Segmented
            size="small"
            value={mapLayer}
            options={[
              { label: '总览', value: 'overview' },
              { label: '单日', value: 'day', disabled: dayGroups.length === 0 },
              { label: '候选', value: 'candidates', disabled: candidateMapLocations.length === 0 },
            ]}
            onChange={selectMapLayer}
          />
          {mapLayer === 'day' && activeDayGroup && (
            <Segmented
              size="small"
              className="trip-map-day-picker"
              aria-label="选择地图日期"
              value={activeDayGroup.groupId}
              options={dayGroups.map((group) => ({ label: mapFilterLabel(group), value: group.groupId }))}
              onChange={(value) => {
                onSelectLocation('');
                onSelectGroup(String(value));
              }}
            />
          )}
        </div>
      )}
      <MapComponent
        width="100%"
        height="100%"
        locations={mapLayerData.locations}
        dayRoutes={mapLayerData.routes}
        activeGroupId={mapLayerData.activeGroupId}
        selectedLocationId={selectedLocationId}
        locationGroups={mapLocationGroups.map((group) => ({
          id: group.groupId,
          title: group.title,
          locations: group.locations,
        }))}
        onSelectLocation={onSelectLocation}
      />
      {mapReturnContext && (
        <Button
          size="small"
          className="trip-map-return"
          icon={<LeftOutlined />}
          onClick={returnToActivity}
        >
          {mapReturnContext.label}
        </Button>
      )}
    </div>
    {!hasTripWorkspaceData && (
      <div className="trip-mobile-empty" role="status">
        <CalendarOutlined />
        <span>行程生成后会在这里按日期整理</span>
      </div>
    )}
    {hasTripWorkspaceData && (
      <>
        <div
          className="trip-side-horizontal-resizer"
          onMouseDown={onStartResize}
          role="separator"
          aria-orientation="horizontal"
          aria-label="调整地图与行程工作台高度"
          title="拖动调整地图与行程工作台高度"
        />
        <div
          className="trip-workspace-shell"
          style={{ flex: `0 0 calc(${100 - sidePanelMapPercent}% - 4px)` }}
        >
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
            onFocusMap={onFocusMap}
            onDetailContextChange={setMapReturnContext}
            returnToDetailRequestId={returnToDetailRequestId}
            onQuickAction={mobileSimpleEditing ? undefined : onQuickAction}
            mobileSimpleEditing={mobileSimpleEditing}
          />
        </div>
      </>
    )}
  </div>
  );
};

export default TravelPlannerWorkbench;
