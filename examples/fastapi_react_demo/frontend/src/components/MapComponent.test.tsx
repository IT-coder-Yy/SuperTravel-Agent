import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  map: {
    closePopup: vi.fn(),
    flyTo: vi.fn(),
    flyToBounds: vi.fn(),
    fitBounds: vi.fn(),
    stop: vi.fn(),
    getContainer: vi.fn(() => ({ clientWidth: 400, clientHeight: 300 })),
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
      <div data-testid={`marker-${key}`} role="button" tabIndex={0} onClick={eventHandlers?.click}>
        {children}
      </div>
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

import MapComponent, { orderMapLocations, type LocationGroup, type LocationPoint } from './MapComponent';

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
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    mocks.markerOpenPopups.clear();
    mocks.polyline.mockImplementation(() => ({ addTo: vi.fn().mockReturnThis() }));
    mocks.map.getContainer.mockImplementation(() => ({ clientWidth: 400, clientHeight: 300 }));
  });

  it('移动端隐藏地图不启动飞行动画，展开后重新展示选中地点', async () => {
    const container = { clientWidth: 0, clientHeight: 0 };
    mocks.map.getContainer.mockReturnValue(container);
    let notifyResize: () => void = () => {};
    vi.stubGlobal('ResizeObserver', class {
      constructor(callback: () => void) { notifyResize = callback; }
      observe() {}
      disconnect() {}
    });
    render(<MapComponent locations={locations} selectedLocationId="lingyin" onSelectLocation={vi.fn()} />);
    expect(mocks.map.flyTo).not.toHaveBeenCalled();
    expect(mocks.map.flyToBounds).not.toHaveBeenCalled();
    expect(mocks.map.fitBounds).not.toHaveBeenCalled();

    container.clientWidth = 390;
    container.clientHeight = 360;
    notifyResize();
    expect(mocks.map.setView).toHaveBeenCalledWith([30.24, 120.1], 16, { animate: false });
    await waitFor(() => expect(mocks.markerOpenPopups.get('30.24,120.1')).toHaveBeenCalledTimes(1));
  });

  it('卸载时断开尺寸观察，不调用可能已被父容器销毁的地图', () => {
    const disconnect = vi.fn();
    vi.stubGlobal('ResizeObserver', class {
      observe() {}
      disconnect = disconnect;
    });
    const view = render(<MapComponent locations={locations} onSelectLocation={vi.fn()} />);
    // React 父容器会先清理 MapContainer；Leaflet remove 已删除地图面板。
    mocks.map.stop.mockImplementationOnce(() => {
      throw new Error("Cannot read properties of undefined (reading '_leaflet_pos')");
    });
    expect(() => view.unmount()).not.toThrow();
    expect(disconnect).toHaveBeenCalledOnce();
    mocks.map.stop.mockReset();
  });

  it('按日程的日期和活动顺序为路线地点编号', () => {
    const ordered = orderMapLocations([
      { ...locations[0], id: 'day-2-first', day: 2, order: 1 },
      { ...locations[1], id: 'day-1-second', day: 1, order: 2 },
      { ...locations[0], id: 'day-1-first', day: 1, order: 1 },
    ]);

    expect(ordered.map((location) => location.id)).toEqual([
      'day-1-first',
      'day-1-second',
      'day-2-first',
    ]);
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
      expect(mocks.map.setView).toHaveBeenCalledWith([30.24, 120.1], 16, { animate: false });
      expect(mocks.map.flyTo).not.toHaveBeenCalled();
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
          day: 1, plan_version: 2, provider: 'openrouteservice', status: 'ready', coordinate_system: 'WGS84',
          legs: [{ status: 'ready', provider: 'openrouteservice', coordinate_system: 'WGS84', geometry: { type: 'LineString', coordinates: [[120.15, 30.25], [120.12, 30.245], [120.1, 30.24]] } }],
        }]}
        onSelectLocation={vi.fn()}
      />
    );

    await waitFor(() => expect(mocks.polyline).toHaveBeenCalledTimes(1));
    expect(mocks.polyline.mock.calls[0][0][0]).toEqual([30.25, 120.15]);
    expect(mocks.polyline.mock.calls[0][0].at(-1)).toEqual([30.24, 120.1]);
  });

  it('无效路线响应不会被绘制成地点之间的直线', () => {
    render(
      <MapComponent
        locations={locations}
        dayRoutes={[{
          day: 1, plan_version: 2, provider: 'openrouteservice', status: 'ready', coordinate_system: 'WGS84',
          legs: [{ status: 'ready', provider: 'openrouteservice', coordinate_system: 'WGS84', geometry: { type: 'LineString', coordinates: [[120.15, 30.25]] } }],
        }]}
        onSelectLocation={vi.fn()}
      />
    );

    expect(mocks.polyline).not.toHaveBeenCalled();
    expect(screen.getByText('真实路线暂不可用，当前仅显示日程地点')).toBeTruthy();
  });

  it('国内 BD-09 路线缺少浏览器凭证时不与 OSM 混绘', () => {
    render(
      <MapComponent
        locations={locations}
        baiduMapApiKey=""
        dayRoutes={[{
          day: 1, plan_version: 1, provider: 'baidu_directionlite', status: 'unavailable', coordinate_system: 'BD09LL', legs: [],
        }]}
        onSelectLocation={vi.fn()}
      />
    );

    expect(screen.getByText('国内地图暂不可显示')).not.toBeNull();
    expect(screen.queryByTestId('map-container')).toBeNull();
    expect(mocks.polyline).not.toHaveBeenCalled();
  });

  it('国内地图降级时以轻量地点摘要保留选中地点，而不展示覆盖地图的详情页', () => {
    const onSelectLocation = vi.fn();
    render(
      <MapComponent
        locations={locations}
        baiduMapApiKey=""
        selectedLocationId="west-lake"
        dayRoutes={[{
          day: 1, plan_version: 1, provider: 'baidu_directionlite', status: 'unavailable', coordinate_system: 'BD09LL', legs: [],
        }]}
        onSelectLocation={onSelectLocation}
      />
    );

    expect(screen.getByRole('complementary', { name: '西湖地点摘要' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '关闭地点摘要' }));
    expect(onSelectLocation).toHaveBeenCalledWith('');
  });

  it('恢复和候选图层的空路线保留国内底图，明确国际路线才切换坐标系', () => {
    const shared = { locations, baiduMapApiKey: '', onSelectLocation: vi.fn() };
    const domestic = [{ day: 1, plan_version: 1, provider: 'baidu_directionlite',
      status: 'unavailable' as const, coordinate_system: 'BD09LL' as const, legs: [] }];
    const view = render(<MapComponent {...shared} dayRoutes={domestic} />);
    expect(screen.getByText('国内地图暂不可显示')).toBeTruthy();
    view.rerender(<MapComponent {...shared} dayRoutes={[]} />);
    expect(screen.getByText('国内地图暂不可显示')).toBeTruthy();
    expect(screen.queryByTestId('map-container')).toBeNull();
    view.rerender(<MapComponent {...shared} dayRoutes={domestic} />);
    expect(screen.queryByTestId('map-container')).toBeNull();
    view.rerender(<MapComponent {...shared} dayRoutes={[{ day: 1, plan_version: 1,
      provider: 'openrouteservice', status: 'unavailable', coordinate_system: 'WGS84', legs: [] }]} />);
    expect(screen.queryByText('国内地图暂不可显示')).toBeNull();
    expect(screen.getByTestId('map-container')).toBeTruthy();
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
