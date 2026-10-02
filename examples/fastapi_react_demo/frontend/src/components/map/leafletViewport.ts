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
  // 桌面 C1 的行程卡与输入框覆盖地图边缘，拟合时为它们留出真实可见空间。
  const floatingWorkbench = Boolean(container.closest('.c1-workbench')) && container.clientWidth > 768;
  const topLeft: L.PointTuple = floatingWorkbench
    ? [Math.min(360, container.clientWidth * 0.34), Math.min(100, container.clientHeight / 5)]
    : [0, 0];
  const bottomRight: L.PointTuple = floatingWorkbench
    ? [60, Math.min(200, container.clientHeight / 3)]
    : [0, 0];
  const selected = locations.find((location) => location.id === selectedLocationId);
  if (selected) {
    // 先完成定位再打开弹窗；飞行动画中的旧投影会让弹窗自动平移与视口恢复互相覆盖。
    map.setView([selected.lat, selected.lng], 16, { animate: false });
    if (floatingWorkbench) map.panBy([-Math.min(150, container.clientWidth * 0.12), 0], { animate: false });
  } else if (locations.length) {
    // 固定 50px 边距在窄小视口中会使有效宽高变负，Leaflet 由此计算出 NaN zoom。
    const padding = Math.min(50, container.clientWidth / 4, container.clientHeight / 4);
    map.fitBounds(L.latLngBounds(locations.map(({ lat, lng }) => [lat, lng])), {
      padding: [padding, padding],
      ...(floatingWorkbench ? { paddingTopLeft: topLeft, paddingBottomRight: bottomRight } : {}),
      maxZoom: 16,
      animate: false,
    });
  } else {
    map.setView([39.9042, 116.4074], 12, { animate: false });
  }
  return true;
};
