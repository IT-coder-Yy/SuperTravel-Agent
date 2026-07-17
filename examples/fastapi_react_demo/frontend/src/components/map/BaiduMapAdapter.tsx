import React, { useEffect, useRef, useState } from 'react';
import PoiDetailContent from '../PoiDetailContent';
import type { DayRouteGeometry, LocationPoint } from '../MapComponent';
import { CloseOutlined } from '@ant-design/icons';

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
const loadBaiduMap = (apiKey: string) => {
  if (window.BMapGL) return Promise.resolve();
  if (loader) return loader;
  loader = new Promise<void>((resolve, reject) => {
    const callbackName = '__superTravelBaiduMapReady';
    window[callbackName] = () => resolve();
    const script = document.createElement('script');
    script.src = `https://api.map.baidu.com/api?v=1.0&type=webgl&ak=${encodeURIComponent(apiKey)}&callback=${callbackName}`;
    script.async = true;
    script.onerror = () => reject(new Error('BAIDU_MAP_SCRIPT_FAILED'));
    document.head.appendChild(script);
  });
  return loader;
};

const BaiduMapAdapter: React.FC<Props> = ({ apiKey, locations, routes, selectedLocationId, onSelectLocation }) => {
  const hostRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<any>(null);
  const markerRefs = useRef<Record<string, any>>({});
  const [loadFailed, setLoadFailed] = useState(false);
  const selected = locations.find((location) => location.id === selectedLocationId);

  useEffect(() => {
    let cancelled = false;
    loadBaiduMap(apiKey).then(() => {
      if (cancelled || !hostRef.current || !window.BMapGL) return;
      const BMapGL = window.BMapGL;
      const map = new BMapGL.Map(hostRef.current);
      map.enableScrollWheelZoom(true);
      mapRef.current = map;
      markerRefs.current = {};
      const points = locations.map((location) => new BMapGL.Point(location.lng, location.lat));
      if (points.length > 0) map.setViewport(points, { margins: [36, 36, 36, 36] });
      else map.centerAndZoom(new BMapGL.Point(116.4074, 39.9042), 11);
      locations.forEach((location) => {
        const marker = new BMapGL.Marker(new BMapGL.Point(location.lng, location.lat));
        marker.addEventListener('click', () => onSelectLocation(location.id));
        map.addOverlay(marker);
        markerRefs.current[location.id] = marker;
      });
      const colors = ['#2563eb', '#0f766e', '#c2410c', '#7c3aed', '#be123c'];
      routes.forEach((route, routeIndex) => route.legs.forEach((leg) => {
        if (leg.status !== 'ready' || leg.geometry?.type !== 'LineString') return;
        const path = leg.geometry.coordinates.map(([lng, lat]) => new BMapGL.Point(lng, lat));
        if (path.length >= 2) map.addOverlay(new BMapGL.Polyline(path, {
          strokeColor: colors[routeIndex % colors.length], strokeWeight: 4, strokeOpacity: 0.82,
        }));
      }));
    }).catch(() => setLoadFailed(true));
    return () => { cancelled = true; mapRef.current?.destroy?.(); mapRef.current = null; };
  }, [apiKey, locations, onSelectLocation, routes]);

  useEffect(() => {
    if (!selected || !mapRef.current || !window.BMapGL) return;
    mapRef.current.panTo(new window.BMapGL.Point(selected.lng, selected.lat));
  }, [selected]);

  return <div className="baidu-map-adapter">
    <div ref={hostRef} className="baidu-map-host" />
    {loadFailed && <div className="map-route-unavailable" role="status">百度地图加载失败，真实路线暂不可用</div>}
    {selected && <aside className="baidu-map-poi-detail"><button type="button" aria-label="关闭地点详情" onClick={() => onSelectLocation('')}><CloseOutlined /></button><PoiDetailContent location={selected} /></aside>}
  </div>;
};

export default BaiduMapAdapter;
