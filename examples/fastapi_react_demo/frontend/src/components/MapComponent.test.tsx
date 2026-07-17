import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  map: {
    closePopup: vi.fn(),
    flyTo: vi.fn(),
    flyToBounds: vi.fn(),
    invalidateSize: vi.fn(),
    removeLayer: vi.fn(),
    setView: vi.fn(),
  },
  markerOpenPopups: new Map<string, ReturnType<typeof vi.fn>>(),
  polyline: vi.fn(),
}));

vi.mock('leaflet', () => {
  class DefaultIcon {}

  Object.assign(DefaultIcon, { mergeOptions: vi.fn() });
  Object.assign(DefaultIcon.prototype, { _getIconUrl: vi.fn() });

  const leaflet = {
    Icon: { Default: DefaultIcon },
    divIcon: vi.fn((options) => options),
    latLngBounds: vi.fn((points) => points),
    polyline: mocks.polyline,
  };

  return { default: leaflet };
});

vi.mock('react-leaflet', async () => {
  const ReactModule = await import('react');

  const MapContainer = ReactModule.forwardRef<unknown, React.PropsWithChildren>(
    ({ children }, ref) => {
      ReactModule.useImperativeHandle(ref, () => mocks.map);
      return <div data-testid="map-container">{children}</div>;
    }
  );

  const Marker = ReactModule.forwardRef<unknown, React.PropsWithChildren<{
    position: [number, number];
    eventHandlers?: { click?: () => void };
  }>>(({ children, position, eventHandlers }, ref) => {
    const key = position.join(',');
    const openPopup = ReactModule.useMemo(() => {
      const handler = vi.fn();
      mocks.markerOpenPopups.set(key, handler);
      return handler;
    }, [key]);

    ReactModule.useImperativeHandle(ref, () => ({ openPopup }));
    return (
      <button data-testid={`marker-${key}`} onClick={eventHandlers?.click}>
        {children}
      </button>
    );
  });

  return {
    MapContainer,
    Marker,
    Popup: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
    TileLayer: ({ eventHandlers, url }: {
      eventHandlers?: { tileerror?: () => void };
      url: string;
    }) => (
      <button data-testid="tile-layer" data-url={url} onClick={eventHandlers?.tileerror} />
    ),
    useMap: () => mocks.map,
  };
});

import MapComponent, { type LocationGroup, type LocationPoint } from './MapComponent';

const locations: LocationPoint[] = [
  { id: 'west-lake', name: '西湖', lat: 30.25, lng: 120.15, category: '景点' },
  { id: 'lingyin', name: '灵隐寺', lat: 30.24, lng: 120.10, category: '景点' },
  { id: 'canal', name: '京杭大运河', lat: 30.32, lng: 120.14, category: '景点' },
];

const locationGroups: LocationGroup[] = [
  { id: 'day-1', title: '第一天', locations: locations.slice(0, 2) },
  { id: 'day-2', title: '第二天', locations: locations.slice(2) },
];

describe('MapComponent', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.markerOpenPopups.clear();
    mocks.polyline.mockImplementation(() => ({ addTo: vi.fn().mockReturnThis() }));
  });

  it('只渲染当前受控分组的 Marker，不渲染分组列表', () => {
    render(
      <MapComponent
        locations={locations}
        locationGroups={locationGroups}
        activeGroupId="day-2"
        onSelectLocation={vi.fn()}
      />
    );

    expect(screen.getByTestId('marker-30.32,120.14')).toBeTruthy();
    expect(screen.queryByTestId('marker-30.25,120.15')).toBeNull();
    expect(screen.queryByText('第一天')).toBeNull();
    expect(screen.queryByText('第二天')).toBeNull();
  });

  it('Marker 点击只把地点 ID 回调给父级', () => {
    const onSelectLocation = vi.fn();
    render(
      <MapComponent locations={locations} onSelectLocation={onSelectLocation} />
    );

    fireEvent.click(screen.getByTestId('marker-30.24,120.1'));

    expect(onSelectLocation).toHaveBeenCalledTimes(1);
    expect(onSelectLocation).toHaveBeenCalledWith('lingyin');
  });

  it('外部选中地点变化时聚焦并打开同一个 POI', async () => {
    const { rerender } = render(
      <MapComponent locations={locations} onSelectLocation={vi.fn()} />
    );

    rerender(
      <MapComponent
        locations={locations}
        selectedLocationId="lingyin"
        onSelectLocation={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(mocks.map.flyTo).toHaveBeenCalledWith([30.24, 120.1], 16);
      expect(mocks.markerOpenPopups.get('30.24,120.1')).toHaveBeenCalledTimes(1);
    });
  });

  it('只绘制路线服务返回的真实 geometry', async () => {
    render(
      <MapComponent
        locations={locations}
        locationGroups={locationGroups}
        activeGroupId="day-1"
        dayRoutes={[{
          day: 1, plan_version: 2, status: 'ready', coordinate_system: 'WGS84',
          legs: [{ status: 'ready', geometry: { type: 'LineString', coordinates: [[120.15, 30.25], [120.12, 30.245], [120.1, 30.24]] } }],
        }]}
        onSelectLocation={vi.fn()}
      />
    );

    await waitFor(() => expect(mocks.polyline).toHaveBeenCalledTimes(1));
    expect(mocks.polyline.mock.calls[0][0][0]).toEqual([30.25, 120.15]);
    expect(mocks.polyline.mock.calls[0][0].at(-1)).toEqual([30.24, 120.1]);
  });

  it('国内 BD-09 路线缺少浏览器凭证时不与 OSM 混绘', () => {
    render(
      <MapComponent
        locations={locations}
        dayRoutes={[{
          day: 1, plan_version: 1, status: 'unavailable', coordinate_system: 'BD09LL', legs: [],
        }]}
        onSelectLocation={vi.fn()}
      />
    );

    expect(screen.getByText('国内地图暂不可显示')).not.toBeNull();
    expect(screen.queryByTestId('map-container')).toBeNull();
    expect(mocks.polyline).not.toHaveBeenCalled();
  });

  it('路线服务不可用时不绘制伪路线', () => {
    render(<MapComponent locations={locations} onSelectLocation={vi.fn()} />);
    expect(mocks.polyline).not.toHaveBeenCalled();
    expect(screen.getByText('真实路线暂不可用，当前仅显示日程地点')).not.toBeNull();
  });

  it('连续三次瓦片错误后切换到备用底图', () => {
    render(<MapComponent locations={locations} onSelectLocation={vi.fn()} />);

    const tileLayer = screen.getByTestId('tile-layer');
    fireEvent.click(tileLayer);
    fireEvent.click(tileLayer);
    fireEvent.click(tileLayer);

    expect(screen.getByTestId('tile-layer').getAttribute('data-url')).toBe(
      'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png'
    );
    expect(screen.getByText('当前底图: Carto Light')).toBeTruthy();
  });
});
