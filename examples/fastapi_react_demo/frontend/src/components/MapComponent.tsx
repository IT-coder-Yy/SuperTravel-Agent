import React, { useState, useRef, useEffect } from 'react';
import { MapContainer, TileLayer, Marker, Popup, useMap } from 'react-leaflet';
import { List, Avatar, Tag } from 'antd';
import { EnvironmentOutlined } from '@ant-design/icons';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

const TRANSPORT_HUB_DISPLAY_REGEX = /(交通枢纽|火车站|高铁站|动车站|铁路(?:站|枢纽)|城际站|地铁站|轻轨站|客运站|汽车站|公交(?:站|枢纽)|机场|航站楼|(?:东|西|南|北)?站|[\u4e00-\u9fa5A-Za-z0-9]{2,24}(?:东|西|南|北)?站|railway station|train station|airport|terminal|metro station|subway station|bus station|bus terminal|transport hub)/i;

// 修复 leaflet 默认图标问题
delete (L.Icon.Default.prototype as any)._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon-2x.png',
  iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-shadow.png',
});

// 弧线组件 - 使用 useMap hook
const ArcLines: React.FC<{
  connections: Array<{ from: [number, number]; to: [number, number] }>;
}> = ({ connections }) => {
  const map = useMap();
  const arcLinesRef = useRef<L.Polyline[]>([]);

  useEffect(() => {
    if (!map) return;

    console.log('ArcLines: 开始绘制连线，连接数量:', connections.length);

    // 清除之前的连线
    arcLinesRef.current.forEach(line => {
      map.removeLayer(line);
    });
    arcLinesRef.current = [];

    // 计算弧线路径
    const calculateArcPath = (start: [number, number], end: [number, number]): [number, number][] => {
      const [lat1, lng1] = start;
      const [lat2, lng2] = end;

      // 计算中点
      const midLat = (lat1 + lat2) / 2;
      const midLng = (lng1 + lng2) / 2;

      // 计算距离来确定弧线高度
      const distance = Math.sqrt(Math.pow(lat2 - lat1, 2) + Math.pow(lng2 - lng1, 2));
      const arcHeight = distance * 0.3; // 弧线高度为距离的30%

      // 计算垂直于连线的方向
      const perpLat = -(lng2 - lng1);
      const perpLng = lat2 - lat1;
      const perpLength = Math.sqrt(perpLat * perpLat + perpLng * perpLng);

      if (perpLength === 0) return [start, end]; // 防止除零

      // 标准化垂直向量
      const normPerpLat = perpLat / perpLength;
      const normPerpLng = perpLng / perpLength;

      // 计算弧线控制点
      const controlLat = midLat + normPerpLat * arcHeight;
      const controlLng = midLng + normPerpLng * arcHeight;

      // 生成弧线上的点
      const points: [number, number][] = [];
      const segments = 30;

      for (let i = 0; i <= segments; i++) {
        const t = i / segments;
        const t2 = t * t;
        const t3 = 1 - t;
        const t4 = t3 * t3;

        // 二次贝塞尔曲线公式
        const lat = t4 * lat1 + 2 * t3 * t * controlLat + t2 * lat2;
        const lng = t4 * lng1 + 2 * t3 * t * controlLng + t2 * lng2;

        points.push([lat, lng]);
      }

      return points;
    };

    // 添加新的连线
    connections.forEach((connection, index) => {
      console.log(`ArcLines: 绘制连线 ${index + 1}:`, connection.from, '->', connection.to);

      const arcPath = calculateArcPath(connection.from, connection.to);

      const arcLine = L.polyline(arcPath, {
        color: '#1890ff',
        weight: 3,
        opacity: 0.7,
        dashArray: '10,10',
        className: 'arc-line'
      }).addTo(map);

      arcLinesRef.current.push(arcLine);
      console.log(`ArcLines: 连线 ${index + 1} 已添加到地图`);
    });

    console.log('ArcLines: 所有连线绘制完成，总数:', arcLinesRef.current.length);

    // 清理函数
    return () => {
      arcLinesRef.current.forEach(line => {
        map.removeLayer(line);
      });
      arcLinesRef.current = [];
    };
  }, [map, connections]);

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

interface LocationPoint {
  id: string;
  name: string;
  lat: number;
  lng: number;
  description?: string;
  category?: string;
  day?: number | string;
  order?: number | string;
}

const getNormalizedCategory = (location: Pick<LocationPoint, 'name' | 'description' | 'category'>): string => {
  const text = `${location.name || ''} ${location.description || ''} ${location.category || ''}`;
  if (TRANSPORT_HUB_DISPLAY_REGEX.test(text)) {
    return '交通枢纽';
  }

  return location.category || '其他';
};

interface LocationGroup {
  id: string;
  title?: string;
  locations: LocationPoint[];
}

interface MapComponentProps {
  width?: number | string;
  height?: number | string;
  locations?: LocationPoint[];
  locationGroups?: LocationGroup[];
  onLocationAdd?: (location: LocationPoint) => void;
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

// 空的初始位置数组，将根据对话内容动态填充
const initialLocations: LocationPoint[] = [];

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
  locations: externalLocations = [],
  locationGroups: externalLocationGroups = []
}) => {
  const [locations, setLocations] = useState<LocationPoint[]>(initialLocations);
  const [selectedLocation, setSelectedLocation] = useState<LocationPoint | null>(null);
  const [activeGroupId, setActiveGroupId] = useState('');
  const [groupPanelHeight, setGroupPanelHeight] = useState(220);
  const [tileProviderIndex, setTileProviderIndex] = useState(0);
  const mapRef = useRef<any>(null);
  const mapViewportRef = useRef<HTMLDivElement>(null);
  const mapLayoutRef = useRef<HTMLDivElement>(null);
  const groupListContainerRef = useRef<HTMLDivElement>(null);
  const groupSectionRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const prevLocationsRef = useRef<LocationPoint[]>([]);
  const tileErrorCountRef = useRef(0);
  const isGroupPanelResizingRef = useRef(false);
  const currentTileProvider = TILE_PROVIDERS[tileProviderIndex];
  const GROUP_SPLITTER_HEIGHT = 8;

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

  const safeLocationGroups = getSafeLocationGroups(externalLocationGroups);
  const activeGroup = safeLocationGroups.find((group) => group.id === activeGroupId) ||
    (safeLocationGroups.length > 0 ? safeLocationGroups[0] : null);

  // 检查地点数据是否真的发生了变化
  const hasLocationsChanged = (nextLocations: LocationPoint[]) => {
    if (nextLocations.length !== prevLocationsRef.current.length) return true;

    // 检查每个地点的ID是否相同
    for (let i = 0; i < nextLocations.length; i++) {
      if (nextLocations[i]?.id !== prevLocationsRef.current[i]?.id) {
        return true;
      }
    }

    return false;
  };

  const applyLocationsChange = (nextLocations: LocationPoint[]) => {
    if (!hasLocationsChanged(nextLocations)) {
      return;
    }

    console.log('地点数据发生变化:', {
      之前的地点数量: prevLocationsRef.current.length,
      新的地点数量: nextLocations.length,
      新地点: nextLocations.map(loc => loc.name)
    });

    setLocations(nextLocations);
    prevLocationsRef.current = [...nextLocations];

    if (mapRef.current && nextLocations.length > 0) {
      setTimeout(() => {
        try {
          const bounds = L.latLngBounds(
            nextLocations.map(loc => [loc.lat, loc.lng])
          );

          mapRef.current.flyToBounds(bounds, {
            padding: [50, 50],
            duration: 1.5,
            easeLinearity: 0.25
          });
        } catch (error) {
          console.error('地图视图更新失败:', error);
        }
      }, 200);
    } else if (mapRef.current && nextLocations.length === 0) {
      console.log('地点数据为空，重置地图到默认位置');
      mapRef.current.setView([39.9042, 116.4074], 12);
    }
  };

  // 处理地点数据变化
  useEffect(() => {
    if (safeLocationGroups.length > 0) {
      const hasActiveGroup = safeLocationGroups.some((group) => group.id === activeGroupId);
      const nextActiveGroupId = hasActiveGroup ? activeGroupId : safeLocationGroups[0].id;

      if (nextActiveGroupId !== activeGroupId) {
        setActiveGroupId(nextActiveGroupId);
      }

      const nextActiveGroup = safeLocationGroups.find((group) => group.id === nextActiveGroupId);
      applyLocationsChange(nextActiveGroup ? nextActiveGroup.locations : []);
      return;
    }

    if (activeGroupId) {
      setActiveGroupId('');
    }

    const safeExternalLocations = getSafeLocations(externalLocations);
    applyLocationsChange(safeExternalLocations);
  }, [externalLocations, externalLocationGroups, activeGroupId]);

  // 处理地图加载完成
  useEffect(() => {
    if (mapRef.current && locations.length > 0) {
      try {
        const safeLocations = getSafeLocations(locations);
        const bounds = L.latLngBounds(
          safeLocations.map(loc => [loc.lat, loc.lng])
        );
        mapRef.current.flyToBounds(bounds, {
          padding: [50, 50],
          duration: 1.5,
          easeLinearity: 0.25
        });
      } catch (error) {
        console.error('地图初始定位失败:', error);
      }
    }
  }, [locations]);

  // 定位到指定位置
  const flyToLocation = (location: LocationPoint) => {
    if (mapRef.current) {
      mapRef.current.flyTo([location.lat, location.lng], 16);
      setSelectedLocation(location);
    }
  };

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

  // 生成弧线连接的地点对
  const getArcConnections = () => {
    if (locations.length < 2) {
      console.log('getArcConnections: 地点数量不足，无法生成连线');
      return [];
    }

    const connections = [];
    for (let i = 0; i < locations.length - 1; i++) {
      connections.push({
        from: [locations[i].lat, locations[i].lng] as [number, number],
        to: [locations[i + 1].lat, locations[i + 1].lng] as [number, number]
      });
    }

    console.log('getArcConnections: 生成连线数量:', connections.length);
    connections.forEach((conn, idx) => {
      console.log(`连线 ${idx + 1}: (${conn.from[0]}, ${conn.from[1]}) -> (${conn.to[0]}, ${conn.to[1]})`);
    });

    return connections;
  };

  const hasGroupedLocations = safeLocationGroups.length > 0;

  const clampGroupPanelHeight = (nextHeight: number) => {
    const containerHeight = mapLayoutRef.current?.getBoundingClientRect().height || 0;
    const minGroupHeight = 140;
    const minMapHeight = 220;

    if (containerHeight <= 0) {
      return Math.max(minGroupHeight, nextHeight);
    }

    const maxGroupHeight = Math.max(minGroupHeight, containerHeight - minMapHeight - GROUP_SPLITTER_HEIGHT);
    return Math.min(maxGroupHeight, Math.max(minGroupHeight, nextHeight));
  };

  const handleSelectGroup = (group: LocationGroup) => {
    setActiveGroupId(group.id);
    if (group.locations[0]) {
      flyToLocation(group.locations[0]);
    }

    const container = groupListContainerRef.current;
    const section = groupSectionRefs.current[group.id];
    if (!container || !section) {
      return;
    }

    const scrollTop = Math.max(0, section.offsetTop - container.offsetTop - 4);
    container.scrollTo({ top: scrollTop, behavior: 'smooth' });
  };

  const startGroupPanelResize = (event: React.MouseEvent<HTMLDivElement>) => {
    event.preventDefault();
    isGroupPanelResizingRef.current = true;
  };

  useEffect(() => {
    const handleMouseMove = (event: MouseEvent) => {
      if (!isGroupPanelResizingRef.current) {
        return;
      }

      const container = mapLayoutRef.current;
      if (!container) {
        return;
      }

      const rect = container.getBoundingClientRect();
      const nextGroupHeight = rect.bottom - event.clientY;
      setGroupPanelHeight(clampGroupPanelHeight(nextGroupHeight));
    };

    const handleMouseUp = () => {
      if (!isGroupPanelResizingRef.current) {
        return;
      }

      isGroupPanelResizingRef.current = false;
      try {
        mapRef.current?.invalidateSize?.();
      } catch (error) {
        console.error('地图尺寸更新失败:', error);
      }
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);

    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, []);

  useEffect(() => {
    const syncPanelHeight = () => {
      setGroupPanelHeight((prev) => clampGroupPanelHeight(prev));
      try {
        mapRef.current?.invalidateSize?.();
      } catch (error) {
        console.error('地图尺寸同步失败:', error);
      }
    };

    syncPanelHeight();
    window.addEventListener('resize', syncPanelHeight);
    return () => {
      window.removeEventListener('resize', syncPanelHeight);
    };
  }, []);

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

  useEffect(() => {
    try {
      mapRef.current?.invalidateSize?.();
    } catch (error) {
      console.error('地图尺寸刷新失败:', error);
    }
  }, [groupPanelHeight]);

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
        ref={mapLayoutRef}
        style={{
          flex: 1,
          minHeight: 0,
          display: 'flex',
          flexDirection: 'column',
          position: 'relative'
        }}
      >
        {/* 地图容器 */}
        <div
          ref={mapViewportRef}
          style={{
            height: `calc(100% - ${groupPanelHeight + GROUP_SPLITTER_HEIGHT}px)`,
            minHeight: '220px',
            position: 'relative',
            flexShrink: 0
          }}
        >
          <MapContainer
            center={[39.9042, 116.4074]} // 北京天安门
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
                }
              }}
            />

            {/* 地点标记 */}
            {locations.map((location, index) => {
              const normalizedCategory = getNormalizedCategory(location);
              return (
                <Marker
                  key={location.id}
                  position={[location.lat, location.lng]}
                  icon={createColorIcon(location, index + 1)}
                  eventHandlers={{
                    click: () => setSelectedLocation(location),
                  }}
                >
                  <Popup>
                    <div style={{ minWidth: '260px', padding: '16px' }}>
                      <div style={{
                        display: 'flex',
                        alignItems: 'center',
                        marginBottom: '12px'
                      }}>
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
                          flexShrink: 0
                        }}>
                          {index + 1}
                        </div>
                        <div style={{
                          fontWeight: 'bold',
                          fontSize: '16px',
                          color: '#1a1a1a',
                          lineHeight: '1.4'
                        }}>
                          {location.name}
                        </div>
                      </div>

                      <div style={{ marginBottom: '12px' }}>
                        <Tag
                          color={getCategoryColor(normalizedCategory)}
                          style={{
                            fontSize: '11px',
                            padding: '2px 8px',
                            borderRadius: '12px',
                            fontWeight: '500'
                          }}
                        >
                          {getCategoryName(normalizedCategory)}
                        </Tag>
                      </div>

                      {location.description && (
                        <div style={{
                          fontSize: '13px',
                          color: '#666',
                          lineHeight: '1.5',
                          marginBottom: '12px',
                          padding: '8px 12px',
                          backgroundColor: '#f8f9fa',
                          borderRadius: '6px',
                          borderLeft: `3px solid ${getCategoryColor(normalizedCategory)}`
                        }}>
                          {location.description}
                        </div>
                      )}

                      <div style={{
                        fontSize: '11px',
                        color: '#999',
                        borderTop: '1px solid #f0f0f0',
                        paddingTop: '8px',
                        marginTop: '8px'
                      }}>
                        坐标: {location.lat.toFixed(6)}, {location.lng.toFixed(6)}
                      </div>
                    </div>
                  </Popup>
                </Marker>
              );
            })}

            {/* 弧线连接 */}
            <ArcLines connections={getArcConnections()} />
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
              color: '#595959'
            }}>
              当前底图: {currentTileProvider.name}
            </div>
          )}
        </div>

        {/* 地图与问答地点分组之间的可拖拽分割条 */}
        <div
          onMouseDown={startGroupPanelResize}
          style={{
            height: `${GROUP_SPLITTER_HEIGHT}px`,
            cursor: 'row-resize',
            background: 'linear-gradient(to bottom, #e5e7eb, #d1d5db, #e5e7eb)',
            borderTop: '1px solid #d1d5db',
            borderBottom: '1px solid #d1d5db',
            flexShrink: 0,
            zIndex: 5
          }}
        />

        {/* 地点列表 */}
        <div
          ref={groupListContainerRef}
          style={{
            height: `${groupPanelHeight}px`,
            overflowY: 'auto',
            borderTop: '1px solid #f0f0f0',
            background: '#fafafa',
            flexShrink: 0
          }}
        >
          {/* 地点列表标题栏 */}
          <div style={{
            padding: '8px 16px',
            borderBottom: '1px solid #f0f0f0',
            background: '#f8f9fa',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            gap: '8px'
          }}>
            <div style={{
              fontSize: '13px',
              fontWeight: 'bold',
              color: '#333'
            }}>
              {hasGroupedLocations ? `问答地点分组 (${safeLocationGroups.length})` : `行程地点 (${locations.length})`}
            </div>

            {hasGroupedLocations && (
              <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'flex-end',
                gap: '6px',
                flexWrap: 'wrap'
              }}>
                {safeLocationGroups.map((group, index) => {
                  const isActiveGroup = activeGroup?.id === group.id;
                  return (
                    <button
                      key={`group-switch-${group.id}`}
                      onClick={() => handleSelectGroup(group)}
                      style={{
                        border: `1px solid ${isActiveGroup ? '#1677ff' : '#d1d5db'}`,
                        borderRadius: '12px',
                        background: isActiveGroup ? '#e6f4ff' : '#fff',
                        color: isActiveGroup ? '#1677ff' : '#4b5563',
                        fontSize: '11px',
                        fontWeight: 600,
                        padding: '2px 8px',
                        cursor: 'pointer',
                        lineHeight: '16px'
                      }}
                    >
                      第{index + 1}组
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {hasGroupedLocations ? (
            <div>
              {safeLocationGroups.map((group, groupIndex) => {
                const isActiveGroup = activeGroup?.id === group.id;

                return (
                  <div
                    key={group.id}
                    ref={(element) => {
                      groupSectionRefs.current[group.id] = element;
                    }}
                    style={{
                      borderBottom: groupIndex < safeLocationGroups.length - 1 ? '1px solid #f0f0f0' : 'none',
                      background: '#fff'
                    }}
                  >
                    <div style={{
                      padding: '8px 16px',
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      background: isActiveGroup ? '#e6f7ff' : '#fff'
                    }}>
                      <button
                        onClick={() => handleSelectGroup(group)}
                        style={{
                          background: 'transparent',
                          border: 'none',
                          padding: 0,
                          fontSize: '12px',
                          fontWeight: 600,
                          color: '#333',
                          cursor: 'pointer'
                        }}
                      >
                        {(group.title || `第${groupIndex + 1}次提问`)} ({group.locations.length})
                      </button>
                      <Tag
                        color={isActiveGroup ? 'blue' : 'default'}
                        style={{ marginInlineEnd: 0, cursor: isActiveGroup ? 'default' : 'pointer' }}
                        onClick={() => {
                          if (isActiveGroup) return;
                          handleSelectGroup(group);
                        }}
                      >
                        {isActiveGroup ? '当前地图' : '点击切换'}
                      </Tag>
                    </div>

                    <List
                      size="small"
                      dataSource={group.locations}
                      renderItem={(location, index) => {
                        const normalizedCategory = getNormalizedCategory(location);
                        return (
                          <List.Item
                            style={{
                              padding: '8px 16px',
                              cursor: 'pointer',
                              backgroundColor: isActiveGroup && selectedLocation?.id === location.id ? '#e6f7ff' : 'transparent',
                              borderBottom: index < group.locations.length - 1 ? '1px solid #f7f7f7' : 'none'
                            }}
                            onClick={() => {
                              if (!isActiveGroup) {
                                handleSelectGroup(group);
                              }
                              flyToLocation(location);
                            }}
                          >
                            <List.Item.Meta
                              avatar={
                                <Avatar
                                  size="small"
                                  style={{
                                    backgroundColor: getCategoryColor(normalizedCategory),
                                    fontSize: '10px',
                                    fontWeight: 'bold'
                                  }}
                                >
                                  {index + 1}
                                </Avatar>
                              }
                              title={
                                <div style={{ fontSize: '13px' }}>
                                  <span style={{ fontWeight: 'bold', marginRight: '6px' }}>
                                    {location.name}
                                  </span>
                                  <Tag
                                    color={getCategoryColor(normalizedCategory)}
                                    style={{
                                      marginLeft: '8px',
                                      fontSize: '10px',
                                      padding: '0 6px',
                                      lineHeight: '16px'
                                    }}
                                  >
                                    {getCategoryName(normalizedCategory)}
                                  </Tag>
                                </div>
                              }
                              description={
                                <div style={{
                                  fontSize: '11px',
                                  color: '#666',
                                  marginTop: '4px',
                                  lineHeight: '1.4'
                                }}>
                                  {location.description && (
                                    <div style={{ marginBottom: '2px' }}>{location.description}</div>
                                  )}
                                  <div style={{ color: '#999' }}>
                                    {location.lat.toFixed(4)}, {location.lng.toFixed(4)}
                                  </div>
                                </div>
                              }
                            />
                          </List.Item>
                        );
                      }}
                    />
                  </div>
                );
              })}
            </div>
          ) : locations.length === 0 ? (
            <div style={{
              padding: '20px',
              textAlign: 'center',
              color: '#999',
              fontSize: '12px'
            }}>
              <EnvironmentOutlined style={{ fontSize: '24px', marginBottom: '8px', display: 'block' }} />
              暂无地点
              <br />
              <span style={{ fontSize: '11px' }}>
                在聊天中提到地点时会自动显示在地图上
              </span>
            </div>
          ) : (
            <List
              size="small"
              dataSource={locations}
              renderItem={(location, index) => {
                const normalizedCategory = getNormalizedCategory(location);
                return (
                  <List.Item
                    style={{
                      padding: '8px 16px',
                      cursor: 'pointer',
                      backgroundColor: selectedLocation?.id === location.id ? '#e6f7ff' : 'transparent',
                      borderBottom: index < locations.length - 1 ? '1px solid #f0f0f0' : 'none'
                    }}
                    onClick={() => flyToLocation(location)}
                  >
                    <List.Item.Meta
                      avatar={
                        <Avatar
                          size="small"
                          style={{
                            backgroundColor: getCategoryColor(normalizedCategory),
                            fontSize: '10px',
                            fontWeight: 'bold'
                          }}
                        >
                          {index + 1}
                        </Avatar>
                      }
                      title={
                        <div style={{ fontSize: '13px' }}>
                          <span style={{ fontWeight: 'bold', marginRight: '6px' }}>
                            {location.name}
                          </span>
                          <Tag
                            color={getCategoryColor(normalizedCategory)}
                            style={{
                              marginLeft: '8px',
                              fontSize: '10px',
                              padding: '0 6px',
                              lineHeight: '16px'
                            }}
                          >
                            {getCategoryName(normalizedCategory)}
                          </Tag>
                        </div>
                      }
                      description={
                        <div style={{
                          fontSize: '11px',
                          color: '#666',
                          marginTop: '4px',
                          lineHeight: '1.4'
                        }}>
                          {location.description && (
                            <div style={{ marginBottom: '2px' }}>{location.description}</div>
                          )}
                          <div style={{ color: '#999' }}>
                            {location.lat.toFixed(4)}, {location.lng.toFixed(4)}
                          </div>
                        </div>
                      }
                    />
                  </List.Item>
                );
              }}
            />
          )}
        </div>
      </div>
    </div>
  );
};

export default React.memo(MapComponent, areMapPropsEqual);
