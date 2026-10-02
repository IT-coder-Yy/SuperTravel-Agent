import { render, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import BaiduMapAdapter from './BaiduMapAdapter';

afterEach(() => { delete window.BMapGL; });

it('恢复与编辑地点时复用地图，延迟加载的标记不会访问已销毁面板', async () => {
  const pendingDraws: Array<() => void> = [];
  const maps: any[] = [];
  class Map {
    panes: object | undefined = {};
    enableScrollWheelZoom = vi.fn();
    setViewport = vi.fn();
    centerAndZoom = vi.fn();
    setMapStyleV2 = vi.fn();
    getZoom = () => 16;
    addEventListener = vi.fn();
    removeEventListener = vi.fn();
    removeOverlay = vi.fn();
    destroy = vi.fn(() => { this.panes = undefined; });
    addOverlay = vi.fn(() => {
      pendingDraws.push(() => {
        if (!this.panes) throw new Error("Cannot read properties of undefined (reading 'markerMouseTarget')");
      });
    });
    constructor() { maps.push(this); }
  }
  class Marker { setLabel = vi.fn(); addEventListener = vi.fn(); }
  class Label { setStyle = vi.fn(); }
  class Point { constructor(public lng: number, public lat: number) {} }
  window.BMapGL = { Map, Marker, Label, Point, Icon: class {}, Size: class {} };
  const first = { id: 'one', name: '已验证地点', lat: 30.6, lng: 104.1 };
  const second = { id: 'two', name: '新增地点', lat: 30.7, lng: 104.2 };
  const onSelectLocation = vi.fn();
  const view = render(<BaiduMapAdapter apiKey="test" locations={[first]} routes={[]} onSelectLocation={onSelectLocation} />);
  await waitFor(() => expect(maps[0]?.addOverlay).toHaveBeenCalledOnce());
  expect(maps[0].setMapStyleV2).toHaveBeenCalled();
  const oldZoomListener = maps[0].addEventListener.mock.calls.find(([event]: string[]) => event === 'zoomend')[1];
  view.rerender(<BaiduMapAdapter apiKey="test" locations={[first, second]} routes={[]} onSelectLocation={onSelectLocation} />);
  await waitFor(() => expect(maps[0].addOverlay).toHaveBeenCalledTimes(3));
  expect(maps).toHaveLength(1);
  expect(maps[0].destroy).not.toHaveBeenCalled();
  expect(() => pendingDraws.forEach((draw) => draw())).not.toThrow();
  expect(maps[0].removeOverlay).toHaveBeenCalledTimes(1);
  oldZoomListener();
  expect(maps[0].addOverlay).toHaveBeenCalledTimes(3);
  view.unmount();
  expect(maps[0].destroy).toHaveBeenCalledOnce();
});
