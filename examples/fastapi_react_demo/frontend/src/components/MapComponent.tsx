import React, { useEffect, useMemo, useRef, useState } from 'react';
import { MapContainer, TileLayer, Marker, Popup, useMap } from 'react-leaflet';
import { Tag } from 'antd';
import { EnvironmentOutlined } from '@ant-design/icons';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import PoiDetailContent from './PoiDetailContent';
import BaiduMapAdapter from './map/BaiduMapAdapter';

const TRANSPORT_HUB_DISPLAY_REGEX = /(交通枢纽|火车站|高铁站|动车站|铁路(?:站|枢纽)|城际站|地铁站|轻轨站|客运站|汽车站|公交(?:站|枢纽)|机场|航站楼|(?:东|西|南|北)?站|[\u4e00-\u9fa5A-Za-z0-9]{2,24}(?:东|西|南|北)?站|railway station|train station|airport|terminal|metro station|subway station|bus station|bus terminal|transport hub)/i;

// 修复 leaflet 默认图标问题
delete (L.Icon.Default.prototype as any)._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon-2x.png',
  iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-shadow.png',
});

export interface DayRouteGeometry {
  day: number;
  plan_version: number;
  status: 'ready' | 'partial' | 'unavailable';
  coordinate_system: 'BD09LL' | 'WGS84';
  legs: Array<{
    status: 'ready' | 'unavailable';
    geometry?: { type: 'LineString'; coordinates: number[][] } | null;
  }>;
}

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
    const colors = ['#2563eb', '#0f766e', '#c2410c', '#7c3aed', '#be123c'];
    routes.forEach((route, routeIndex) => route.legs.forEach((leg) => {
      if (leg.status !== 'ready' || leg.geometry?.type !== 'LineString') return;
      const points = leg.geometry.coordinates
        .filter((point) => point.length >= 2 && point.every(Number.isFinite))
        .map(([lng, lat]) => [lat, lng] as [number, number]);
      if (points.length < 2) return;
      const line = L.polyline(points, {
        color: colors[routeIndex % colors.length],
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

// 创建彩色圆形标记图标
const createColorIcon = (location: LocationPoint, number: number): L.DivIcon => {
  const normalizedCategory = getNormalizedCategory(location);
  const color = getCategoryColor(normalizedCategory);
  return L.divIcon({
    html: `
      <div style="
        position: relative;
        display: flex;
        flex-direction: column;
        align-items: center;
        transform: translateX(-50%);
      ">
        <!-- 主标记点 - 根据分类颜色 -->
        <div style="
          background: ${color};
          color: white;
          width: 28px;
          height: 28px;
          border-radius: 50%;
          border: 3px solid white;
          box-shadow: 0 2px 6px ${color}40;
          display: flex;
          align-items: center;
          justify-content: center;
          font-weight: bold;
          font-size: 12px;
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          position: relative;
          z-index: 1000;
        ">
          ${number}
        </div>
        
        <!-- 地点名称标签 -->
        <div style="
          background: rgba(255, 255, 255, 0.95);
          border: 1px solid ${color}30;
          border-radius: 4px;
          padding: 2px 6px;
          margin-top: 4px;
          font-size: 11px;
          font-weight: 500;
          color: #333;
          white-space: nowrap;
          max-width: 120px;
          overflow: hidden;
          text-overflow: ellipsis;
          box-shadow: 0 1px 3px ${color}20;
          font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
          text-align: center;
          position: relative;
          z-index: 999;
        ">
          ${location.name}
        </div>
      </div>
    `,
    className: 'custom-marker',
    iconSize: [28, 60],
    iconAnchor: [14, 28],
    popupAnchor: [0, -28]
  });
};

// 获取分类颜色 - 不同类别使用不同颜色
const getCategoryColor = (category?: string) => {
  const colors: { [key: string]: string } = {
    '景点': '#1890ff',
    '酒店': '#52c41a',
    '餐厅': '#fa8c16',
    '交通': '#722ed1',
    '交通枢纽': '#722ed1',
    '购物': '#eb2f96',
    '娱乐': '#13c2c2',
    'attraction': '#1890ff',
    'hotel': '#52c41a',
    'restaurant': '#fa8c16',
    'transport': '#722ed1',
    'transport hub': '#722ed1',
    'transportation hub': '#722ed1',
    'transport_hub': '#722ed1',
    'shopping': '#eb2f96',
    'entertainment': '#13c2c2',
    'temple': '#1890ff',
    'shrine': '#1890ff',
    'park': '#52c41a',
    'museum': '#1890ff',
    '人文古迹': '#1890ff',
    '自然风光': '#52c41a',
    '文化体验': '#13c2c2',
    '历史建筑': '#1890ff',
    '亲子娱乐': '#eb2f96',
    'other': '#8c8c8c',
    '其他': '#8c8c8c'
  };
  return colors[category || '景点'] || '#1890ff';
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
  address?: string;
  city?: string;
  rating?: number | null;
  images?: string[];
  summary?: string;
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

const getNormalizedCategory = (location: Pick<LocationPoint, 'name' | 'description' | 'category'>): string => {
  const text = `${location.name || ''} ${location.description || ''} ${location.category || ''}`;
  if (TRANSPORT_HUB_DISPLAY_REGEX.test(text)) {
    return '交通枢纽';
  }

  return location.category || '其他';
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
    prev.onSelectLocation === next.onSelectLocation &&
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
  onSelectLocation,
  dayRoutes = [],
}) => {
  const [tileProviderIndex, setTileProviderIndex] = useState(0);
  const mapRef = useRef<L.Map | null>(null);
  const mapViewportRef = useRef<HTMLDivElement>(null);
  const markerRefs = useRef<Record<string, L.Marker>>({});
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
      return safeExternalLocations;
    }

    return safeLocationGroups.find((group) => group.id === activeGroupId)?.locations || [];
  }, [activeGroupId, safeExternalLocations, safeLocationGroups]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) {
      return;
    }

    if (locations.length === 0) {
      map.closePopup();
      map.setView([39.9042, 116.4074], 12);
      return;
    }

    try {
      const bounds = L.latLngBounds(locations.map((location) => [location.lat, location.lng]));
      map.flyToBounds(bounds, {
        padding: [50, 50],
        duration: 1.5,
        easeLinearity: 0.25,
      });
    } catch (error) {
      console.error('地图视图更新失败:', error);
    }
  }, [locations]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) {
      return;
    }

    if (!selectedLocationId) {
      map.closePopup();
      return;
    }

    const selectedLocation = locations.find((location) => location.id === selectedLocationId);
    if (!selectedLocation) {
      map.closePopup();
      return;
    }

    map.flyTo([selectedLocation.lat, selectedLocation.lng], 16);
    markerRefs.current[selectedLocationId]?.openPopup();
  }, [locations, selectedLocationId]);

  // 获取分类中文名称
  const getCategoryName = (category?: string) => {
    const normalized = (category || '').trim().toLowerCase();
    if (
      normalized === 'transport' ||
      normalized === 'transport hub' ||
      normalized === 'transportation hub' ||
      normalized === 'transport_hub' ||
      normalized === '交通'
    ) {
      return '交通枢纽';
    }

    if ((category || '').trim() === '交通枢纽') {
      return '交通枢纽';
    }

    return category || '其他';
  };

  useEffect(() => {
    const target = mapViewportRef.current;
    if (!target || typeof ResizeObserver === 'undefined') {
      return;
    }

    let rafId = 0;
    const observer = new ResizeObserver(() => {
      if (rafId) {
        cancelAnimationFrame(rafId);
      }

      rafId = requestAnimationFrame(() => {
        try {
          mapRef.current?.invalidateSize?.();
        } catch (error) {
          console.error('地图尺寸观察刷新失败:', error);
        }
      });
    });

    observer.observe(target);

    return () => {
      observer.disconnect();
      if (rafId) {
        cancelAnimationFrame(rafId);
      }
    };
  }, []);

  const hasDomesticRoute = dayRoutes.some((route) => route.coordinate_system === 'BD09LL');
  const frontendEnv = (import.meta as ImportMeta & { env?: Record<string, string | undefined> }).env;
  const baiduBrowserKey = String(frontendEnv?.VITE_BAIDU_MAP_AK || '').trim();
  if (hasDomesticRoute && baiduBrowserKey) {
    return <BaiduMapAdapter
      apiKey={baiduBrowserKey}
      locations={locations}
      routes={dayRoutes.filter((route) => route.coordinate_system === 'BD09LL')}
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
        {selectedLocation && <PoiDetailContent location={selectedLocation} />}
      </div>
    );
  }

  const leafletRoutes = dayRoutes.filter((route) => route.coordinate_system === 'WGS84');

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
        ref={mapViewportRef}
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

          {locations.map((location, index) => {
            const normalizedCategory = getNormalizedCategory(location);
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
                icon={createColorIcon(location, index + 1)}
                eventHandlers={{
                  click: () => onSelectLocation(location.id),
                }}
              >
                <Popup minWidth={320} maxWidth={380}>
                  <div style={{ minWidth: '300px', padding: '4px' }}>
                    <div style={{ display: 'none', alignItems: 'center', marginBottom: '12px' }}>
                      <div style={{
                        background: getCategoryColor(normalizedCategory),
                        color: 'white',
                        width: 24,
                        height: 24,
                        borderRadius: '50%',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: '12px',
                        fontWeight: 'bold',
                        marginRight: '10px',
                        flexShrink: 0,
                      }}>
                        {index + 1}
                      </div>
                      <div style={{ fontWeight: 'bold', fontSize: '16px', color: '#1a1a1a', lineHeight: '1.4' }}>
                        {location.name}
                      </div>
                    </div>

                    <div style={{ display: 'none', marginBottom: '12px' }}>
                      <Tag
                        color={getCategoryColor(normalizedCategory)}
                        style={{ fontSize: '11px', padding: '2px 8px', borderRadius: '12px', fontWeight: '500' }}
                      >
                        {getCategoryName(normalizedCategory)}
                      </Tag>
                    </div>

                    {false && location.description && (
                      <div style={{
                        fontSize: '13px',
                        color: '#666',
                        lineHeight: '1.5',
                        marginBottom: '12px',
                        padding: '8px 12px',
                        backgroundColor: '#f8f9fa',
                        border: '1px solid #e5e7eb',
                        borderRadius: '6px',
                      }}>
                        {location.description}
                      </div>
                    )}

                    <div style={{ display: 'none',
                      fontSize: '11px',
                      color: '#767676',
                      borderTop: '1px solid #f0f0f0',
                      paddingTop: '8px',
                      marginTop: '8px',
                    }}>
                      坐标: {location.lat.toFixed(6)}, {location.lng.toFixed(6)}
                    </div>
                    <PoiDetailContent location={location} />
                  </div>
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
        {locations.length > 1 && (leafletRoutes.length === 0 || leafletRoutes.every((route) => route.status === 'unavailable')) && (
          <div className="map-route-unavailable" role="status">{hasDomesticRoute && !baiduBrowserKey ? '百度地图浏览器凭证未配置，当前仅显示日程地点' : '真实路线暂不可用，当前仅显示日程地点'}</div>
        )}
      </div>
    </div>
  );
};

export default React.memo(MapComponent, areMapPropsEqual);
