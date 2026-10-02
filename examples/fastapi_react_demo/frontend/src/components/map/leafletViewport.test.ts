import L from 'leaflet';
import { afterEach, describe, expect, it } from 'vitest';
import { syncLeafletViewport } from './leafletViewport';

const tokyo = [
  { id: 'asakusa', lat: 35.7147, lng: 139.7967 },
  { id: 'ueno', lat: 35.7155, lng: 139.7745 },
];
let map: L.Map | undefined;

const createMap = (width: number, height: number) => {
  const container = document.createElement('div');
  document.body.appendChild(container);
  Object.defineProperty(container, 'clientWidth', { configurable: true, value: width });
  Object.defineProperty(container, 'clientHeight', { configurable: true, value: height });
  map = L.map(container).setView([39.9042, 116.4074], 12);
  return { map, container };
};

afterEach(() => {
  const container = map?.getContainer();
  map?.remove();
  container?.remove();
});

describe('真实 Leaflet 视口恢复', () => {
  it('窄小容器仍以真实东京经纬度拟合，缩放值不会变成 NaN', () => {
    const { map } = createMap(80, 80);
    expect(syncLeafletViewport(map, tokyo)).toBe(true);
    expect(Number.isFinite(map.getZoom())).toBe(true);
    expect(map.getCenter().lat).toBeCloseTo(35.7151, 3);
    expect(map.getCenter().lng).toBeCloseTo(139.7856, 3);
  });

  it('隐藏面板暂缓定位，展开后恢复全部地点而不调换经纬度', () => {
    const { map, container } = createMap(0, 0);
    expect(syncLeafletViewport(map, tokyo)).toBe(false);
    expect(map.getZoom()).toBe(12);
    Object.defineProperty(container, 'clientWidth', { value: 390 });
    Object.defineProperty(container, 'clientHeight', { value: 360 });
    expect(syncLeafletViewport(map, tokyo)).toBe(true);
    expect(map.getBounds().contains([35.7147, 139.7967])).toBe(true);
    expect(map.getBounds().contains([35.7155, 139.7745])).toBe(true);
  });

  it('只有一个地点时限制最大缩放，选中地点保持准确坐标', () => {
    const { map } = createMap(390, 360);
    syncLeafletViewport(map, tokyo.slice(0, 1));
    expect(map.getZoom()).toBe(16);
    syncLeafletViewport(map, tokyo, 'ueno');
    expect(map.getCenter()).toEqual(L.latLng(35.7155, 139.7745));
  });

  it('C1 桌面拟合为行程卡和输入框留出空间，选中点仍可见', () => {
    const { map, container } = createMap(1200, 800);
    container.classList.add('c1-workbench');
    syncLeafletViewport(map, tokyo);
    for (const point of tokyo) {
      const position = map.latLngToContainerPoint([point.lat, point.lng]);
      expect(position.x).toBeGreaterThan(330);
      expect(position.y).toBeLessThan(620);
    }
    syncLeafletViewport(map, tokyo, 'ueno');
    const selected = map.latLngToContainerPoint([tokyo[1].lat, tokyo[1].lng]);
    expect(selected.x).toBeGreaterThan(330);
    expect(selected.x).toBeLessThan(1100);
  });

  it('隐藏地图选中东京地点，展开和连续尺寸更新后仍在选中地点', async () => {
    const { map, container } = createMap(0, 0);
    expect(syncLeafletViewport(map, tokyo, 'ueno')).toBe(false);
    Object.defineProperty(container, 'clientWidth', { value: 326 });
    Object.defineProperty(container, 'clientHeight', { value: 776 });
    expect(syncLeafletViewport(map, tokyo, 'ueno')).toBe(true);
    expect(map.getCenter()).toEqual(L.latLng(35.7155, 139.7745));
    Object.defineProperty(container, 'clientHeight', { value: 700 });
    syncLeafletViewport(map, tokyo, 'ueno');
    await new Promise(resolve => setTimeout(resolve, 60));
    expect(map.getBounds().contains([35.7155, 139.7745])).toBe(true);
    expect(map.getCenter().lat).toBeCloseTo(35.7155, 4);
    expect(map.getCenter().lng).toBeCloseTo(139.7745, 4);
    expect(map.getZoom()).toBe(16);
  });
});
