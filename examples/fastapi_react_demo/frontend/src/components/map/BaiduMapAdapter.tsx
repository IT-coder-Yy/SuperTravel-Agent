import React, { useEffect, useRef, useState } from 'react';
import MapLocationQuickView from '../MapLocationQuickView';
import type { DayRouteGeometry, LocationPoint } from '../MapComponent';
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
    const fail = (error: Error) => {
      resetBaiduLoader();
      reject(error);
    };
    window[callbackName] = () => {
      if (window.BMapGL) {
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

const BaiduMapAdapter: React.FC<Props> = ({ apiKey, locations, routes, selectedLocationId, onSelectLocation }) => {
  const hostRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<any>(null);
  const markerRefs = useRef<Record<string, any>>({});
  const markerLabelsRef = useRef<Record<string, any>>({});
  const markerOverlaysRef = useRef<any[]>([]);
  const onSelectLocationRef = useRef(onSelectLocation);
  const selectedLocationIdRef = useRef(selectedLocationId);
  const [mapInstance, setMapInstance] = useState<any>(null);
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
    loadBaiduMap(apiKey).then(() => {
      if (cancelled || !hostRef.current || !window.BMapGL) return;
      map = new window.BMapGL.Map(hostRef.current);
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
    const points = locations.map((location) => new BMapGL.Point(location.lng, location.lat));
    if (points.length > 0) map.setViewport(points, { margins: [36, 36, 36, 36] });
    else map.centerAndZoom(new BMapGL.Point(116.4074, 39.9042), 11);

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
    if (!selected || !mapRef.current || !window.BMapGL) return;
    mapRef.current.setZoom?.(16);
    mapRef.current.panTo(new window.BMapGL.Point(selected.lng, selected.lat));
  }, [selected, mapInstance]);

  return <div className="baidu-map-adapter">
    <div ref={hostRef} className="baidu-map-host" />
    {loadFailed && <div className="map-route-unavailable" role="status">
      <span>百度地图未能加载。请确认 VITE_BAIDU_MAP_AK 为 WebGL JavaScript API 的浏览器端 AK，并在百度地图控制台放行 http://127.0.0.1:8001 与 http://localhost:8001。</span>
      <button type="button" onClick={() => { resetBaiduLoader(); setRetryToken((value) => value + 1); }}>重新加载地图</button>
    </div>}
    {!loadFailed && locations.length > 1 && routeStatus !== 'ready' && (
      <div className="map-route-unavailable" role="status">{routeAvailabilityMessage(routeStatus)}</div>
    )}
    {selected && <MapLocationQuickView location={selected} onClose={() => onSelectLocation('')} />}
  </div>;
};

export default BaiduMapAdapter;
