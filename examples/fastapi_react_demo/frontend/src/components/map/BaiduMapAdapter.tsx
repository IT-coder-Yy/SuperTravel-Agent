import React, { useEffect, useRef, useState } from 'react';
import MapLocationQuickView from '../MapLocationQuickView';
import type { DayRouteGeometry, LocationPoint } from '../MapComponent';
import { baiduRoamingStyle } from './baiduMapStyle';
import { convertBaiduScene } from './baiduCoordinates';
import {
  clusterMapLocations,
  escapeMapHtml,
  mapAnchorGlyph,
  mapAnchorLabel,
  mapCategoryGlyph,
  mapCategoryLabel,
  mapDayColor,
} from './mapMarkerPresentation';
import {
  routeAvailability,
  routeAvailabilityMessage,
  routeLineCoordinates,
} from './routePresentation';

declare global {
  interface Window { BMapGL?: any; __superTravelBaiduMapReady?: () => void; }
}

interface Props {
  apiKey: string;
  locations: LocationPoint[];
  routes: DayRouteGeometry[];
  selectedLocationId?: string;
  onSelectLocation: (locationId: string) => void;
  coordinateSystem?: 'BD09LL' | 'WGS84';
  showPopups?: boolean;
}

let loader: Promise<void> | null = null;
let loaderScript: HTMLScriptElement | null = null;

const resetBaiduLoader = () => {
  loader = null;
  loaderScript?.remove();
  loaderScript = null;
  delete window.__superTravelBaiduMapReady;
};

const createBaiduLocationIcon = (BMapGL: any, location: LocationPoint) => {
  const dayColor = mapDayColor(location.day);
  const category = location.category || '其他';
  const isAnchor = Boolean(location.anchor_kind);
  const glyph = escapeMapHtml(
    isAnchor ? mapAnchorGlyph(location.anchor_kind) : mapCategoryGlyph(category),
  );
  const shape = isAnchor
    ? `<rect x="4" y="4" width="26" height="26" rx="7" fill="#ffffff" stroke="${dayColor}" stroke-width="3"/>`
    : `<circle cx="17" cy="17" r="14" fill="#ffffff" stroke="${dayColor}" stroke-width="3"/>`;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="34" height="34" viewBox="0 0 34 34">
    ${shape}
    <text x="17" y="22" text-anchor="middle" font-family="system-ui, sans-serif" font-size="15" font-weight="800" fill="#243247">${glyph}</text>
  </svg>`;
  return new BMapGL.Icon(
    `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`,
    new BMapGL.Size(34, 34),
    { anchor: new BMapGL.Size(17, 17) },
  );
};

const createBaiduClusterIcon = (BMapGL: any, count: number, day: LocationPoint['day']) => {
  const dayColor = mapDayColor(day);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="42" height="42" viewBox="0 0 42 42">
    <circle cx="21" cy="21" r="18" fill="#243247" stroke="${dayColor}" stroke-width="3"/>
    <text x="21" y="26" text-anchor="middle" font-family="system-ui, sans-serif" font-size="13" font-weight="800" fill="#ffffff">${count}</text>
  </svg>`;
  return new BMapGL.Icon(
    `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`,
    new BMapGL.Size(42, 42),
    { anchor: new BMapGL.Size(21, 21) },
  );
};

const createBaiduLocationLabel = (BMapGL: any, location: LocationPoint, visible: boolean) => {
  const label = new BMapGL.Label(location.anchor_label || location.name, {
    offset: new BMapGL.Size(18, -12),
  });
  label.setStyle({
    display: visible ? 'block' : 'none',
    maxWidth: '144px',
    overflow: 'hidden',
    padding: '3px 7px',
    border: `1px solid ${mapDayColor(location.day)}47`,
    borderRadius: '5px',
    backgroundColor: 'rgba(255, 255, 255, 0.94)',
    boxShadow: '0 2px 7px rgba(26, 47, 63, 0.18)',
    color: '#243247',
    fontSize: '12px',
    fontWeight: '650',
    lineHeight: '16px',
    whiteSpace: 'nowrap',
  });
  return label;
};

const loadBaiduMap = (apiKey: string) => {
  if (window.BMapGL) return Promise.resolve();
  if (loader) return loader;
  loader = new Promise<void>((resolve, reject) => {
    const callbackName = '__superTravelBaiduMapReady';
    const timer = window.setTimeout(() => fail(new Error('BAIDU_MAP_SCRIPT_TIMEOUT')), 20000);
    const fail = (error: Error) => {
      window.clearTimeout(timer);
      resetBaiduLoader();
      reject(error);
    };
    window[callbackName] = () => {
      if (window.BMapGL) {
        window.clearTimeout(timer);
        resolve();
        return;
      }
      fail(new Error('BAIDU_MAP_RUNTIME_UNAVAILABLE'));
    };
    const script = document.createElement('script');
    script.src = `https://api.map.baidu.com/api?v=1.0&type=webgl&ak=${encodeURIComponent(apiKey)}&callback=${callbackName}`;
    script.async = true;
    script.onerror = () => fail(new Error('BAIDU_MAP_SCRIPT_FAILED'));
    loaderScript = script;
    document.head.appendChild(script);
  });
  return loader;
};

const BaiduMapCanvas: React.FC<Props> = ({ apiKey, locations, routes, selectedLocationId, onSelectLocation, showPopups = true }) => {
  const hostRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<any>(null);
  const markerRefs = useRef<Record<string, any>>({});
  const markerLabelsRef = useRef<Record<string, any>>({});
  const markerOverlaysRef = useRef<any[]>([]);
  const onSelectLocationRef = useRef(onSelectLocation);
  const selectedLocationIdRef = useRef(selectedLocationId);
  const [mapInstance, setMapInstance] = useState<any>(null);
  const [baseMapReady, setBaseMapReady] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [retryToken, setRetryToken] = useState(0);
  const selected = locations.find((location) => location.id === selectedLocationId);
  const routeStatus = routeAvailability(routes);

  useEffect(() => {
    onSelectLocationRef.current = onSelectLocation;
  }, [onSelectLocation]);

  useEffect(() => {
    selectedLocationIdRef.current = selectedLocationId;
    Object.entries(markerLabelsRef.current).forEach(([locationId, label]) => {
      label?.setStyle?.({ display: locationId === selectedLocationId ? 'block' : 'none' });
    });
  }, [selectedLocationId]);

  useEffect(() => {
    let cancelled = false;
    let map: any = null;
    setLoadFailed(false);
    setBaseMapReady(false);
    loadBaiduMap(apiKey).then(() => {
      if (cancelled || !hostRef.current || !window.BMapGL) return;
      map = new window.BMapGL.Map(hostRef.current, {
        enableRotate: false, enableTilt: false,
        displayOptions: { poi: true, poiIcon: false, poiText: true, building: false, indoor: false, overlay: true },
      });
      map.centerAndZoom(new window.BMapGL.Point(116.404, 39.915), 11);
      // 在 SDK 首次加载底图后应用，避免初始默认主题覆盖自定义样式。
      const applyStyle = () => {
        if (cancelled) return;
        setBaseMapReady(true);
        map.removeEventListener('tilesloaded', applyStyle);
        map.setMapStyleV2({ styleJson: baiduRoamingStyle });
      };
      map.addEventListener('tilesloaded', applyStyle);
      map.setMapStyleV2({ styleJson: baiduRoamingStyle });
      map.enableScrollWheelZoom(true);
      mapRef.current = map;
      setMapInstance(map);
    }).catch(() => {
      if (!cancelled) setLoadFailed(true);
    });
    return () => {
      cancelled = true;
      if (mapRef.current === map) mapRef.current = null;
      map?.destroy?.();
    };
  }, [apiKey, retryToken]);

  // Keep the SDK map alive while a restored document, draft, or selection changes.
  // Destroying it here races Baidu's asynchronous marker-module initialization.
  useEffect(() => {
    if (!mapInstance || mapInstance !== mapRef.current || !window.BMapGL) return;
    const map = mapInstance;
    const BMapGL = window.BMapGL;
    let cancelled = false;
    const routeOverlays: any[] = [];
    markerRefs.current = {};
    markerLabelsRef.current = {};
    markerOverlaysRef.current = [];
    const removeMarkers = () => {
      markerOverlaysRef.current.forEach((overlay) => map.removeOverlay?.(overlay));
      markerOverlaysRef.current = [];
      markerRefs.current = {};
      markerLabelsRef.current = {};
    };
    const renderMarkers = () => {
      if (cancelled) return;
      removeMarkers();
      const zoom = Number(map.getZoom?.()) || 12;
      clusterMapLocations(locations, zoom).forEach((item) => {
        if (item.kind === 'cluster') {
          const point = new BMapGL.Point(item.lng, item.lat);
          const marker = new BMapGL.Marker(point, {
            icon: createBaiduClusterIcon(BMapGL, item.locations.length, item.locations[0]?.day),
            title: `聚合标记，包含 ${item.locations.length} 个地点`,
          });
          marker.addEventListener('click', () => {
            map.centerAndZoom(point, Math.min(zoom + 2, 16));
          });
          map.addOverlay(marker);
          markerOverlaysRef.current.push(marker);
          return;
        }

        const location = item.location;
        const point = new BMapGL.Point(location.lng, location.lat);
        const marker = new BMapGL.Marker(point, {
          icon: createBaiduLocationIcon(BMapGL, location),
          title: location.anchor_label || `${location.name} · ${location.anchor_kind
            ? mapAnchorLabel(location.anchor_kind)
            : mapCategoryLabel(location.category)}`,
        });
        const label = createBaiduLocationLabel(
          BMapGL,
          location,
          location.id === selectedLocationIdRef.current,
        );
        marker.setLabel?.(label);
        marker.addEventListener('mouseover', () => label.setStyle?.({ display: 'block' }));
        marker.addEventListener('mouseout', () => {
          if (location.id !== selectedLocationIdRef.current) label.setStyle?.({ display: 'none' });
        });
        marker.addEventListener('click', () => onSelectLocationRef.current(location.id));
        map.addOverlay(marker);
        markerRefs.current[location.id] = marker;
        markerLabelsRef.current[location.id] = label;
        markerOverlaysRef.current.push(marker);
      });
    };
    renderMarkers();
    map.addEventListener?.('zoomend', renderMarkers);

    routes.forEach((route) => route.legs.forEach((leg) => {
      if (route.coordinate_system !== 'BD09LL') return;
      const path = routeLineCoordinates(route, leg).map(([lng, lat]) => new BMapGL.Point(lng, lat));
      if (path.length >= 2) {
        const line = new BMapGL.Polyline(path, {
          strokeColor: mapDayColor(route.day), strokeWeight: 4, strokeOpacity: 0.82,
        });
        map.addOverlay(line);
        routeOverlays.push(line);
      }
    }));
    return () => {
      cancelled = true;
      // The instance cleanup may have already destroyed its SDK panes.
      if (mapRef.current === map) {
        map.removeEventListener?.('zoomend', renderMarkers);
        removeMarkers();
        routeOverlays.forEach((overlay) => map.removeOverlay?.(overlay));
      }
    };
  }, [mapInstance, locations, routes]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !mapInstance || mapInstance !== mapRef.current || !window.BMapGL) return;
    const syncViewport = () => {
      if (!host.clientWidth || !host.clientHeight || mapInstance !== mapRef.current) return;
      const BMapGL = window.BMapGL;
      if (selected) {
        mapInstance.centerAndZoom(new BMapGL.Point(selected.lng, selected.lat), 16);
      } else if (locations.length) {
        const c1Desktop = host.closest('.c1-workbench') && window.matchMedia('(min-width: 769px)').matches;
        mapInstance.setViewport(locations.map(({ lng, lat }) => new BMapGL.Point(lng, lat)), {
          margins: c1Desktop ? [90, 60, 210, Math.min(370, host.clientWidth * 0.45)] : [50, 40, 90, 40],
        });
      }
    };
    syncViewport();
    // SDK 的画布尺寸更新晚于 DOM ResizeObserver；以其 resize 事件再拟合一次。
    mapInstance.addEventListener('resize', syncViewport);
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(syncViewport);
    observer?.observe(host);
    return () => {
      observer?.disconnect();
      if (mapInstance === mapRef.current) mapInstance.removeEventListener('resize', syncViewport);
    };
  }, [selected, locations, mapInstance]);

  return <div className="baidu-map-adapter">
    <div ref={hostRef} className="baidu-map-host" />
    {!baseMapReady && !loadFailed && <div className="baidu-map-loading" role="status">正在载入百度底图…</div>}
    {loadFailed && <div className="map-route-unavailable" role="status">
      <span>百度地图未能加载。请确认 VITE_BAIDU_MAP_AK 为 WebGL JavaScript API 的浏览器端 AK，并在百度地图控制台放行 http://127.0.0.1:8001 与 http://localhost:8001。</span>
      <button type="button" onClick={() => { resetBaiduLoader(); setRetryToken((value) => value + 1); }}>重新加载地图</button>
    </div>}
    {!loadFailed && locations.length > 1 && routeStatus !== 'ready' && (
      <div className="map-route-unavailable" role="status">{routeAvailabilityMessage(routeStatus)}</div>
    )}
    <div className="baidu-map-zoom" aria-label="地图缩放">
      <button type="button" aria-label="放大地图" onClick={() => mapRef.current?.zoomIn()}>＋</button>
      <button type="button" aria-label="缩小地图" onClick={() => mapRef.current?.zoomOut()}>−</button>
    </div>
    {showPopups && selected && <MapLocationQuickView location={selected} onClose={() => onSelectLocation('')} />}
  </div>;
};

const BaiduMapAdapter: React.FC<Props> = (props) => {
  const { apiKey, locations, routes, coordinateSystem = 'BD09LL' } = props;
  const cache = useRef(new Map<string, { lng: number; lat: number }>());
  const [scene, setScene] = useState<{
    sourceLocations: LocationPoint[]; sourceRoutes: DayRouteGeometry[];
    locations: LocationPoint[]; routes: DayRouteGeometry[];
  } | null>(null);
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    if (coordinateSystem !== 'WGS84') return;
    let cancelled = false;
    setFailed(false);
    loadBaiduMap(apiKey)
      .then(() => convertBaiduScene(window.BMapGL, locations, routes, cache.current, () => cancelled))
      .then((converted) => {
        if (!cancelled) setScene({ ...converted, sourceLocations: locations, sourceRoutes: routes });
      }).catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [apiKey, locations, routes, coordinateSystem, retry]);
  if (coordinateSystem === 'BD09LL') return <BaiduMapCanvas {...props} />;
  const ready = scene?.sourceLocations === locations && scene?.sourceRoutes === routes;
  return <div className="baidu-map-adapter">
    {scene && <BaiduMapCanvas {...props} locations={ready ? scene.locations : []} routes={ready ? scene.routes : []} />}
    {(!ready || failed) && <div className="map-route-unavailable" role="status">
      {failed ? '百度地图或坐标转换服务暂不可用，尚未显示地点。' : '正在载入百度地图…'}
      {failed && <button type="button" onClick={() => setRetry((value) => value + 1)}>重试</button>}
    </div>}
  </div>;
};

export default BaiduMapAdapter;
