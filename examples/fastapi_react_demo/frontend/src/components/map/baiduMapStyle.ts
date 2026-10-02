/** C · 地图漫游：雾白陆地、浅青水面，保留道路与地名的阅读层次。
 * 百度 JSAPI GL setMapStyleV2 的官方 styleJson 格式。
 */
export const baiduRoamingStyle = [
  { featureType: 'land', elementType: 'geometry', stylers: { color: '#f3f4f1ff' } },
  { featureType: 'water', elementType: 'geometry', stylers: { color: '#c5dddfff' } },
  { featureType: 'green', elementType: 'geometry', stylers: { color: '#e1e9dfff' } },
  { featureType: 'building', elementType: 'geometry.fill', stylers: { color: '#e8ebe6ff' } },
  { featureType: 'building', elementType: 'geometry.stroke', stylers: { color: '#e2e5e0ff' } },
  { featureType: 'road', elementType: 'geometry.fill', stylers: { color: '#ffffffff' } },
  { featureType: 'road', elementType: 'geometry.stroke', stylers: { color: '#dce2ddff' } },
  { featureType: 'road', elementType: 'labels.icon', stylers: { visibility: 'off' } },
  { featureType: 'highway', elementType: 'geometry.fill', stylers: { color: '#ffffffff' } },
  { featureType: 'highway', elementType: 'geometry.stroke', stylers: { color: '#d5dbd6ff' } },
  { featureType: 'arterial', elementType: 'geometry.fill', stylers: { color: '#ffffffff' } },
  { featureType: 'arterial', elementType: 'geometry.stroke', stylers: { color: '#e0e4dfff' } },
  { featureType: 'local', elementType: 'geometry.fill', stylers: { color: '#ffffffff' } },
  { featureType: 'local', elementType: 'geometry.stroke', stylers: { color: '#e8ebe6ff' } },
  { featureType: 'railway', elementType: 'geometry', stylers: { color: '#c9d1ceff' } },
  { featureType: 'all', elementType: 'labels.text.fill', stylers: { color: '#738581ff' } },
  { featureType: 'all', elementType: 'labels.text.stroke', stylers: { color: '#f9faf7ff' } },
  { featureType: 'poilabel', elementType: 'labels', stylers: { visibility: 'off' } },
];
