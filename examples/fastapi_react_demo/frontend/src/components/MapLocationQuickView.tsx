import { CloseOutlined, EnvironmentOutlined } from '@ant-design/icons';
import type { LocationPoint } from './MapComponent';
import { mapCategoryLabel } from './map/mapMarkerPresentation';

interface MapLocationQuickViewProps {
  location: LocationPoint;
  onClose: () => void;
}

const dayLabel = (day: LocationPoint['day']) => {
  if (day === undefined || day === null || day === '') return null;
  const normalized = String(day).trim();
  const number = normalized.match(/^(?:第\s*|Day\s*)?(\d+)(?:\s*天)?$/i)?.[1];
  return number ? `第${number}天` : normalized;
};

const MapLocationQuickView = ({ location, onClose }: MapLocationQuickViewProps) => {
  const description = String(location.summary || location.description || '').trim();
  const day = dayLabel(location.day);

  return (
    <aside className="map-location-quick-view" aria-label={`${location.name}地点摘要`}>
      <button type="button" className="map-location-quick-view-close" onClick={onClose} aria-label="关闭地点摘要">
        <CloseOutlined />
      </button>
      <div className="map-location-quick-view-heading">
        <EnvironmentOutlined aria-hidden="true" />
        <div>
          <strong>{location.name}</strong>
          <span>{[location.category ? mapCategoryLabel(location.category) : '地点类型待确认', day].filter(Boolean).join(' · ')}</span>
        </div>
      </div>
      {description && <p>{description}</p>}
      {!description && <p className="map-location-quick-view-muted">暂无补充说明，可在日程中查看安排。</p>}
    </aside>
  );
};

export default MapLocationQuickView;
