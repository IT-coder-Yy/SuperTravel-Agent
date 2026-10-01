import React, { useEffect, useMemo, useRef, useState } from 'react';
import { MapContainer, TileLayer, Marker, Popup, useMap } from 'react-leaflet';
import { EnvironmentOutlined } from '@ant-design/icons';
import L from 'leaflet';
import type { ImageAssetV3, ReviewSummaryV3 } from '../features/travel/state/travelPlannerTypes';
import 'leaflet/dist/leaflet.css';
import MapLocationQuickView from './MapLocationQuickView';
import BaiduMapAdapter from './map/BaiduMapAdapter';
import { syncLeafletViewport } from './map/leafletViewport';
import {
  normalizeDayRoutes,
  routeAvailability,
  routeAvailabilityMessage,
  routeLineCoordinates,
  type DayRouteGeometry,
} from './map/routePresentation';
import {
  clusterMapLocations,
  escapeMapHtml,
  mapAnchorGlyph,
  mapAnchorLabel,
  mapCategoryGlyph,
  mapCategoryLabel,
  mapDayColor,
  type MapAnchorKind,
  type MapLocationCluster,
} from './map/mapMarkerPresentation';

const TRANSPORT_HUB_DISPLAY_REGEX = /(交通枢纽|火车站|高铁站|动车站|铁路(?:站|枢纽)|城际站|地铁站|轻轨站|客运站|汽车站|公交(?:站|枢纽)|机场|航站楼|(?:东|西|南|北)?站|[\u4e00-\u9fa5A-Za-z0-9]{2,24}(?:东|西|南|北)?站|railway station|train station|airport|terminal|metro station|subway station|bus station|bus terminal|transport hub)/i;

// 修复 leaflet 默认图标问题
delete (L.Icon.Default.prototype as any)._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon-2x.png',
  iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-shadow.png',
});

export type { DayRouteGeometry } from './map/routePresentation';

const RouteLines: React.FC<{ routes: DayRouteGeometry[] }> = ({ routes }) => {
  const map = useMap();
  const linesRef = useRef<L.Polyline[]>([]);

  useEffect(() => {
    if (!map) return;

    // 清除之前的连线
    linesRef.current.forEach(line => {
      map.removeLayer(line);
    });
    linesRef.current = [];
    routes.forEach((route) => route.legs.forEach((leg) => {
      if (route.coordinate_system !== 'WGS84') return;
      const points = routeLineCoordinates(route, leg)
        .map(([lng, lat]) => [lat, lng] as [number, number]);
      if (points.length < 2) return;
      const line = L.polyline(points, {
        color: mapDayColor(route.day),
        weight: 3,
        opacity: 0.82,
        className: 'route-line',
      }).addTo(map);
      linesRef.current.push(line);
    }));

    // 清理函数
    return () => {
      linesRef.current.forEach(line => {
        map.removeLayer(line);
      });
      linesRef.current = [];
    };
  }, [map, routes]);

  return null;
};

export interface LocationPoint {
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
  address?: string;
  city?: string;
  rating?: number | null;
  images?: string[];
  image_assets?: ImageAssetV3[];
  summary?: string;
  review_summary?: ReviewSummaryV3 | null;
  suggested_duration_minutes?: number | null;
  opening_hours?: unknown;
  reservation?: unknown;
  price?: unknown;
  suitable_for?: string[];
  unsuitable_for?: string[];
  source?: string;
  sources?: Array<Record<string, unknown>>;
  field_evidence?: Record<string, Record<string, unknown>>;
  updated_at?: string;
}

const numericRouteOrder = (value: unknown, fallback: number): number => {
  const match = String(value ?? '').match(/\d+/);
  const parsed = match ? Number(match[0]) : Number.NaN;
  return Number.isFinite(parsed) ? parsed : fallback;
};

/**
 * 地图上的编号必须与日程路线保持同一顺序：先按天，再按当天活动顺序。
 * 缺少结构化顺序时保留原始列表顺序，避免为了编号虚构路线关系。
 */
export const orderMapLocations = (locations: LocationPoint[]): LocationPoint[] => (
  locations
    .map((location, sourceIndex) => ({ location, sourceIndex }))
    .sort((left, right) => {
      const dayDifference = numericRouteOrder(left.location.day, Number.MAX_SAFE_INTEGER)
        - numericRouteOrder(right.location.day, Number.MAX_SAFE_INTEGER);
      if (dayDifference !== 0) return dayDifference;

      const orderDifference = numericRouteOrder(left.location.order, left.sourceIndex)
        - numericRouteOrder(right.location.order, right.sourceIndex);
      return orderDifference !== 0 ? orderDifference : left.sourceIndex - right.sourceIndex;
    })
    .map(({ location }) => location)
);

const getNormalizedCategory = (location: Pick<LocationPoint, 'name' | 'description' | 'category'>): string => {
  const text = `${location.name || ''} ${location.description || ''} ${location.category || ''}`;
  if (TRANSPORT_HUB_DISPLAY_REGEX.test(text)) {
    return '交通枢纽';
  }

  return location.category || '其他';
};

const createLocationIcon = (location: LocationPoint, isSelected: boolean): L.DivIcon => {
  const category = getNormalizedCategory(location);
  const dayColor = mapDayColor(location.day);
  const isAnchor = Boolean(location.anchor_kind);
  const label = location.anchor_label || location.name;
  const safeName = escapeMapHtml(label);
  const safeMarkerLabel = escapeMapHtml(
    isAnchor ? mapAnchorLabel(location.anchor_kind) : mapCategoryLabel(category),
  );
  const markerGlyph = isAnchor ? mapAnchorGlyph(location.anchor_kind) : mapCategoryGlyph(category);
  const classNames = [
    'travel-map-marker',
    isSelected ? 'travel-map-marker--selected' : '',
    isAnchor ? 'travel-map-marker--anchor' : '',
  ].filter(Boolean).join(' ');

  return L.divIcon({
    html: `<div class="${classNames}" role="img" aria-label="${safeName}，${safeMarkerLabel}">
      <span class="travel-map-marker-pin" style="--marker-day-color:${dayColor}">${escapeMapHtml(markerGlyph)}</span>
      <span class="travel-map-marker-label">${safeName}</span>
    </div>`,
    className: 'custom-marker',
    iconSize: [34, 34],
    iconAnchor: [17, 17],
    popupAnchor: [0, -22],
  });
};

const createClusterIcon = (cluster: MapLocationCluster): L.DivIcon => {
  const dayColor = mapDayColor(cluster.locations[0]?.day);
  const count = cluster.locations.length;
  return L.divIcon({
    html: `<div class="travel-map-cluster" style="--marker-day-color:${dayColor}" role="img" aria-label="聚合标记，包含 ${count} 个地点">${count}</div>`,
    className: 'custom-marker',
    iconSize: [42, 42],
    iconAnchor: [21, 21],
  });
};

const MapZoomObserver: React.FC<{ onZoomChange: (zoom: number) => void }> = ({ onZoomChange }) => {
  const map = useMap();

  useEffect(() => {
    const updateZoom = () => {
      const zoom = map?.getZoom?.();
      if (Number.isFinite(zoom)) onZoomChange(zoom);
    };
    updateZoom();
    map?.on?.('zoomend', updateZoom);
    return () => {
      map?.off?.('zoomend', updateZoom);
    };
  }, [map, onZoomChange]);

  return null;
};

const MapViewportSync: React.FC<{
  locations: LocationPoint[];
  selectedLocationId?: string;
  zoom: number;
  markerRefs: React.MutableRefObject<Record<string, L.Marker>>;
}> = ({ locations, selectedLocationId, zoom, markerRefs }) => {
  const map = useMap();
  const pendingPopup = useRef<string | null>(null);
  const openPendingPopup = () => {
    const id = pendingPopup.current;
    const marker = id ? markerRefs.current[id] : null;
    if (marker && map.getContainer().clientWidth > 0 && map.getContainer().clientHeight > 0) {
      marker.openPopup();
      if (!marker.isPopupOpen || marker.isPopupOpen()) pendingPopup.current = null;
    }
  };
  useEffect(() => {
    pendingPopup.current = selectedLocationId || null;
    let popupFrame: number | undefined;
    const sync = () => {
      if (!syncLeafletViewport(map, locations, selectedLocationId)) return;
      if (popupFrame !== undefined) cancelAnimationFrame(popupFrame);
      // React Leaflet 在兄弟组件的 effect 中绑定 Popup，等待本轮提交完成后再打开。
      if (selectedLocationId) popupFrame = requestAnimationFrame(openPendingPopup);
      else map.closePopup();
    };
    sync();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => sync());
    observer?.observe(map.getContainer());
    return () => {
      observer?.disconnect();
      if (popupFrame !== undefined) cancelAnimationFrame(popupFrame);
      // MapContainer 卸载时负责停止动画；此处的清理可能晚于 map.remove()。
    };
  }, [map, locations, selectedLocationId, markerRefs]);
  useEffect(() => {
    // 聚合展开后只补开未挂载的弹窗，不重置用户手动选择的缩放级别。
    if (!pendingPopup.current) return;
    const frame = requestAnimationFrame(openPendingPopup);
    return () => cancelAnimationFrame(frame);
  }, [map, zoom, markerRefs]);
  return null;
};

export interface LocationGroup {
  id: string;
  title?: string;
  locations: LocationPoint[];
}

export interface MapComponentProps {
  width?: number | string;
  height?: number | string;
  locations: LocationPoint[];
  locationGroups?: LocationGroup[];
  activeGroupId?: string;
  selectedLocationId?: string;
  baiduMapApiKey?: string;
  onSelectLocation: (locationId: string) => void;
  dayRoutes?: DayRouteGeometry[];
}

const areLocationArraysEqual = (prev: LocationPoint[] = [], next: LocationPoint[] = []) => {
  if (prev === next) return true;
  if (prev.length !== next.length) return false;

  for (let i = 0; i < prev.length; i++) {
    const a = prev[i];
    const b = next[i];
    if (!a || !b) return false;
    if (a.id !== b.id) return false;
    if (a.name !== b.name) return false;
    if (Number(a.lat) !== Number(b.lat)) return false;
    if (Number(a.lng) !== Number(b.lng)) return false;
    if ((a.description || '') !== (b.description || '')) return false;
    if ((a.category || '') !== (b.category || '')) return false;
    if ((a.anchor_kind || '') !== (b.anchor_kind || '')) return false;
    if ((a.anchor_label || '') !== (b.anchor_label || '')) return false;
    if (String(a.day ?? '') !== String(b.day ?? '')) return false;
    if (String(a.order ?? '') !== String(b.order ?? '')) return false;
    if ((a.summary || '') !== (b.summary || '')) return false;
    if (JSON.stringify(a.review_summary ?? null) !== JSON.stringify(b.review_summary ?? null)) return false;
    if (JSON.stringify(a.image_assets ?? []) !== JSON.stringify(b.image_assets ?? [])) return false;
  }

  return true;
};

const areLocationGroupsEqual = (prev: LocationGroup[] = [], next: LocationGroup[] = []) => {
  if (prev === next) return true;
  if (prev.length !== next.length) return false;

  for (let i = 0; i < prev.length; i++) {
    const left = prev[i];
    const right = next[i];
    if (!left || !right) return false;
    if (left.id !== right.id) return false;
    if ((left.title || '') !== (right.title || '')) return false;
    if (!areLocationArraysEqual(left.locations || [], right.locations || [])) return false;
  }

  return true;
};

const areMapPropsEqual = (prev: MapComponentProps, next: MapComponentProps) => {
  return prev.width === next.width &&
    prev.height === next.height &&
    prev.activeGroupId === next.activeGroupId &&
    prev.selectedLocationId === next.selectedLocationId &&
    prev.baiduMapApiKey === next.baiduMapApiKey &&
    prev.onSelectLocation === next.onSelectLocation &&
    JSON.stringify(prev.dayRoutes || []) === JSON.stringify(next.dayRoutes || []) &&
    areLocationArraysEqual(prev.locations || [], next.locations || []) &&
    areLocationGroupsEqual(prev.locationGroups || [], next.locationGroups || []);
};

interface TileProvider {
  id: string;
  name: string;
  attribution: string;
  url: string;
  subdomains?: string | string[];
  maxZoom?: number;
}

const TILE_PROVIDERS: TileProvider[] = [
  {
    id: 'osm',
    name: 'OpenStreetMap',
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
    subdomains: 'abc',
    maxZoom: 19,
  },
  {
    id: 'carto',
    name: 'Carto Light',
    attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
    url: 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
    subdomains: 'abcd',
    maxZoom: 20,
  },
  {
    id: 'osm-fr',
    name: 'OpenStreetMap FR',
    attribution: '&copy; OpenStreetMap contributors',
    url: 'https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png',
    subdomains: 'abc',
    maxZoom: 19,
  },
  {
    id: 'amap',
    name: '高德底图',
    attribution: '&copy; 高德地图',
    url: 'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
    subdomains: ['1', '2', '3', '4'],
    maxZoom: 18,
  },
];

const MapComponent: React.FC<MapComponentProps> = ({
  width = '100%',
  height = '100%',
  locations: externalLocations,
  locationGroups: externalLocationGroups = [],
  activeGroupId,
  selectedLocationId,
  baiduMapApiKey,
  onSelectLocation,
  dayRoutes = [],
}) => {
  const [tileProviderIndex, setTileProviderIndex] = useState(0);
  const [mapZoom, setMapZoom] = useState(16);
  const mapRef = useRef<L.Map | null>(null);
  const markerRefs = useRef<Record<string, L.Marker>>({});
  const coordinateSystemRef = useRef<'BD09LL' | 'WGS84' | null>(null);
  const tileErrorCountRef = useRef(0);
  const currentTileProvider = TILE_PROVIDERS[tileProviderIndex];

  const switchToNextTileProvider = () => {
    setTileProviderIndex((prev) => {
      if (prev >= TILE_PROVIDERS.length - 1) {
        return prev;
      }
      const next = prev + 1;
      console.warn(`地图底图切换: ${TILE_PROVIDERS[prev].name} -> ${TILE_PROVIDERS[next].name}`);
      return next;
    });
  };
  const getSafeLocations = (list: LocationPoint[]) => {
    return (list || []).filter((loc) => (
      !!loc &&
      typeof loc.name === 'string' &&
      loc.name.trim().length > 0 &&
      Number.isFinite(Number(loc.lat)) &&
      Number.isFinite(Number(loc.lng))
    )).map((loc) => ({
      ...loc,
      lat: Number(loc.lat),
      lng: Number(loc.lng)
    }));
  };

  const getSafeLocationGroups = (groups: LocationGroup[]) => {
    return (groups || []).map((group, index) => ({
      id: group?.id || `group_${index + 1}`,
      title: group?.title || `第${index + 1}次提问`,
      locations: getSafeLocations(group?.locations || [])
    })).filter((group) => group.locations.length > 0);
  };

  const safeExternalLocations = useMemo(
    () => getSafeLocations(externalLocations),
    [externalLocations]
  );
  const safeLocationGroups = useMemo(
    () => getSafeLocationGroups(externalLocationGroups),
    [externalLocationGroups]
  );
  const locations = useMemo(() => {
    if (!activeGroupId || safeLocationGroups.length === 0) {
      return orderMapLocations(safeExternalLocations);
    }

    return orderMapLocations(safeLocationGroups.find((group) => group.id === activeGroupId)?.locations || []);
  }, [activeGroupId, safeExternalLocations, safeLocationGroups]);
  const markerDisplayItems = useMemo(
    () => clusterMapLocations(locations, mapZoom),
    [locations, mapZoom],
  );
  const normalizedDayRoutes = useMemo(() => normalizeDayRoutes(dayRoutes), [dayRoutes]);

  const domesticRoutes = normalizedDayRoutes.filter((route) => route.coordinate_system === 'BD09LL');
  // 恢复中的空路线、候选图层都不代表坐标系变化。保留已确定的底图，
  // 避免把 BD-09 地点交给 Leaflet，并避免销毁仍有异步请求的百度实例。
  if (domesticRoutes.length > 0) coordinateSystemRef.current = 'BD09LL';
  else if (normalizedDayRoutes.some((route) => route.coordinate_system === 'WGS84')) coordinateSystemRef.current = 'WGS84';
  const hasDomesticRoute = coordinateSystemRef.current === 'BD09LL';
  const frontendEnv = (import.meta as ImportMeta & { env?: Record<string, string | undefined> }).env;
  const baiduBrowserKey = String(
    baiduMapApiKey !== undefined ? baiduMapApiKey : (frontendEnv?.VITE_BAIDU_MAP_AK || '')
  ).trim();
  if (hasDomesticRoute && baiduBrowserKey) {
    return <BaiduMapAdapter
      apiKey={baiduBrowserKey}
      locations={locations}
      routes={domesticRoutes}
      selectedLocationId={selectedLocationId}
      onSelectLocation={onSelectLocation}
    />;
  }
  if (hasDomesticRoute) {
    const selectedLocation = locations.find((location) => location.id === selectedLocationId);
    return (
      <div className="domestic-map-credential-state" style={{ width, height }} role="status">
        <EnvironmentOutlined />
        <strong>国内地图暂不可显示</strong>
        <span>百度地图浏览器凭证未配置。为避免 BD-09 与国外底图混绘，当前仅列出日程地点。</span>
        <div className="domestic-map-location-list">
          {locations.map((location) => (
            <button type="button" key={location.id} onClick={() => onSelectLocation?.(location.id)}>
              {location.name}
            </button>
          ))}
        </div>
        {selectedLocation && <MapLocationQuickView location={selectedLocation} onClose={() => onSelectLocation?.('')} />}
      </div>
    );
  }

  const leafletRoutes = normalizedDayRoutes.filter((route) => route.coordinate_system === 'WGS84');
  const leafletRouteAvailability = routeAvailability(leafletRoutes);

  return (
    <div style={{
      width,
      height,
      display: 'flex',
      flexDirection: 'column',
      background: '#fff',
      borderRadius: '8px',
      overflow: 'hidden',
      boxShadow: '0 2px 8px rgba(0, 0, 0, 0.1)',
      position: 'relative'
    }}>
      {/* 添加自定义CSS样式 */}
      <style>{`
        .custom-marker {
          background: transparent !important;
          border: none !important;
        }
        .arc-line {
          animation: arcFlow 3s linear infinite;
        }
        @media (prefers-reduced-motion: reduce) {
          .arc-line {
            animation: none;
          }
        }
        @keyframes arcFlow {
          0% {
            stroke-dashoffset: 0;
          }
          100% {
            stroke-dashoffset: 40;
          }
        }
        .leaflet-popup-content-wrapper {
          border-radius: 8px;
          box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }
        .leaflet-popup-content {
          margin: 0;
          padding: 0;
        }
        .leaflet-popup-tip {
          background: white;
        }
      `}</style>

      <div
        style={{
          flex: 1,
          minHeight: 0,
          position: 'relative'
        }}
      >
        <MapContainer
          center={[39.9042, 116.4074]}
          zoom={12}
          style={{ width: '100%', height: '100%' }}
          ref={mapRef}
        >
          <MapViewportSync locations={locations} selectedLocationId={selectedLocationId} zoom={mapZoom} markerRefs={markerRefs} />
          <MapZoomObserver onZoomChange={setMapZoom} />
          <TileLayer
            key={currentTileProvider.id}
            attribution={currentTileProvider.attribution}
            url={currentTileProvider.url}
            subdomains={currentTileProvider.subdomains}
            maxZoom={currentTileProvider.maxZoom}
            eventHandlers={{
              load: () => {
                tileErrorCountRef.current = 0;
              },
              tileerror: () => {
                tileErrorCountRef.current += 1;
                if (tileErrorCountRef.current >= 3) {
                  tileErrorCountRef.current = 0;
                  switchToNextTileProvider();
                }
              },
            }}
          />

          {markerDisplayItems.map((item) => {
            if (item.kind === 'cluster') {
              return (
                <Marker
                  key={item.id}
                  position={[item.lat, item.lng]}
                  icon={createClusterIcon(item)}
                  keyboard
                  title={`聚合标记，包含 ${item.locations.length} 个地点`}
                  eventHandlers={{
                    click: () => mapRef.current?.flyTo(
                      [item.lat, item.lng],
                      Math.min(mapZoom + 2, 16),
                    ),
                  }}
                />
              );
            }

            const location = item.location;
            return (
              <Marker
                key={location.id}
                ref={(marker) => {
                  if (marker) {
                    markerRefs.current[location.id] = marker;
                  } else {
                    delete markerRefs.current[location.id];
                  }
                }}
                position={[location.lat, location.lng]}
                icon={createLocationIcon(location, selectedLocationId === location.id)}
                keyboard
                title={location.anchor_label || `${location.name} · ${location.anchor_kind
                  ? mapAnchorLabel(location.anchor_kind)
                  : mapCategoryLabel(getNormalizedCategory(location))}`}
                eventHandlers={{
                  click: () => onSelectLocation(location.id),
                }}
              >
                <Popup minWidth={260} maxWidth={300}>
                  <MapLocationQuickView location={location} onClose={() => onSelectLocation('')} />
                </Popup>
              </Marker>
            );
          })}

          <RouteLines routes={leafletRoutes} />
        </MapContainer>

        {tileProviderIndex > 0 && (
          <div style={{
            position: 'absolute',
            top: '10px',
            right: '10px',
            zIndex: 1000,
            background: 'rgba(255,255,255,0.95)',
            border: '1px solid #d9d9d9',
            borderRadius: '6px',
            padding: '4px 8px',
            fontSize: '12px',
            color: '#595959',
          }}>
            当前底图: {currentTileProvider.name}
          </div>
        )}
        {locations.length > 1 && leafletRouteAvailability !== 'ready' && (
          <div className="map-route-unavailable" role="status">{routeAvailabilityMessage(leafletRouteAvailability)}</div>
        )}
      </div>
    </div>
  );
};

export default React.memo(MapComponent, areMapPropsEqual);
