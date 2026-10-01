import L from 'leaflet';

interface ViewportLocation {
  id: string;
  lat: number;
  lng: number;
}

/** 隐藏的移动端面板没有可投影的视口；展开后由尺寸观察器重新定位。 */
export const syncLeafletViewport = (
  map: L.Map,
  locations: ViewportLocation[],
  selectedLocationId?: string,
): boolean => {
  map.stop();
  const container = map.getContainer();
  if (container.clientWidth <= 0 || container.clientHeight <= 0) return false;

  map.invalidateSize({ pan: false });
  const selected = locations.find((location) => location.id === selectedLocationId);
  if (selected) {
    // 先完成定位再打开弹窗；飞行动画中的旧投影会让弹窗自动平移与视口恢复互相覆盖。
    map.setView([selected.lat, selected.lng], 16, { animate: false });
  } else if (locations.length) {
    // 固定 50px 边距在窄小视口中会使有效宽高变负，Leaflet 由此计算出 NaN zoom。
    const padding = Math.min(50, container.clientWidth / 4, container.clientHeight / 4);
    map.fitBounds(L.latLngBounds(locations.map(({ lat, lng }) => [lat, lng])), {
      padding: [padding, padding],
      maxZoom: 16,
      animate: false,
    });
  } else {
    map.setView([39.9042, 116.4074], 12, { animate: false });
  }
  return true;
};
