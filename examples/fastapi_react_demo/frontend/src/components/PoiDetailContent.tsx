import React from 'react';
import { ClockCircleOutlined, LinkOutlined, StarFilled } from '@ant-design/icons';
import type { LocationPoint } from './MapComponent';
import { mapCategoryLabel } from './map/mapMarkerPresentation';
import { displayableImageAssets, TravelImageAsset } from '../features/travel/conversation/TravelImageAsset';

const UNKNOWN = '暂无可靠实时数据';

const displayValue = (value: unknown): string => {
  if (Array.isArray(value)) return value.filter(Boolean).join('、') || UNKNOWN;
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    return String(record.description || record.text || record.status || UNKNOWN);
  }
  return String(value || '').trim() || UNKNOWN;
};

const formatUpdatedAt = (value: unknown): string => {
  const text = String(value || '').trim();
  if (!text) return '待确认';
  const date = new Date(text);
  return Number.isNaN(date.getTime()) ? text : date.toLocaleString('zh-CN', { hour12: false });
};

export const buildBaiduPlaceSearchUrl = (location: Pick<LocationPoint, 'name' | 'city' | 'address'>): string => {
  const query = String(location.name || '').trim();
  const address = String(location.address || '').trim();
  const region = String(location.city || '').trim()
    || address.match(/^(.{2,10}?市)/)?.[1]
    || '全国';
  const params = new URLSearchParams({ query, region, output: 'html', src: 'SuperTravelAgent' });
  return `https://api.map.baidu.com/place/search?${params.toString()}`;
};

const fieldMeta = (location: LocationPoint, field: string) => {
  const evidence = location.field_evidence?.[field];
  const sourceId = String(evidence?.source_reference_id || '');
  const source = location.sources?.find((item) => String(item.source_reference_id || '') === sourceId)
    || location.sources?.[0];
  const rawUpdatedAt = evidence?.updated_at || source?.updated_at || location.updated_at;
  return {
    source: String(source?.title || source?.source || location.source || '暂无可靠来源'),
    updatedAt: formatUpdatedAt(rawUpdatedAt),
    reliable: Boolean((sourceId || source) && rawUpdatedAt),
  };
};

const reviewSummaryMeta = (location: LocationPoint) => {
  const review = location.review_summary;
  const text = review?.text?.trim();
  if (!text || !review?.source_refs?.length) return null;
  const sources = review.source_refs
    .map((sourceId) => location.sources?.find((source) => (
      String(source.source_reference_id || source.source_id || '') === sourceId
    )))
    .filter((source): source is Record<string, unknown> => Boolean(source));
  if (sources.length === 0) return null;
  const sourceLabels = Array.from(new Set(sources.map((source) => (
    String(source.title || source.source || '').trim()
  )).filter(Boolean)));
  if (sourceLabels.length === 0) return null;
  const updatedAt = review.updated_at || sources[0].updated_at || null;
  return { text, source: sourceLabels.join('、'), updatedAt: formatUpdatedAt(updatedAt) };
};

const DetailRow: React.FC<{ label: string; value: unknown; location: LocationPoint; field: string; realtime?: boolean }> = ({ label, value, location, field, realtime = false }) => {
  const meta = fieldMeta(location, field);
  return (
    <div className="poi-detail-row">
      <span>{label}</span>
      <strong>{displayValue(realtime && !meta.reliable ? null : value)}</strong>
      <small>来源：{meta.source} · 更新：{meta.updatedAt}</small>
    </div>
  );
};

const PoiDetailContent: React.FC<{ location: LocationPoint }> = ({ location }) => {
  const [failedImageIds, setFailedImageIds] = React.useState<string[]>([]);
  React.useEffect(() => {
    setFailedImageIds([]);
  }, [location.id]);
  const images = displayableImageAssets(location.image_assets, 3)
    .filter((image) => !failedImageIds.includes(image.image_id));
  const ratingMeta = fieldMeta(location, 'rating');
  const summaryMeta = fieldMeta(location, 'summary');
  const review = reviewSummaryMeta(location);
  return (
    <div className="poi-detail-content" data-poi-id={location.poi_id || location.id}>
      {images.length > 0 && (
        <div className="poi-detail-image-grid" aria-label={`${location.name}图片`}>
          {images.map((image) => (
            <TravelImageAsset
              key={image.image_id}
              image={image}
              className="poi-detail-image"
              imageLabel={`${location.name}参考图片`}
              onImageError={(imageId) => setFailedImageIds((current) => (
                current.includes(imageId) ? current : [...current, imageId]
              ))}
            />
          ))}
        </div>
      )}
      <div className="poi-detail-heading">
        <div>
          <strong>{location.name}</strong>
          <span>{location.category ? mapCategoryLabel(location.category) : '类型待确认'}</span>
        </div>
        <span className="poi-rating"><StarFilled /> {ratingMeta.reliable ? location.rating ?? '待确认' : '待确认'}</span>
      </div>
      <small className="poi-detail-meta">评分来源：{ratingMeta.source} · 更新：{ratingMeta.updatedAt}</small>
      <p className="poi-summary">{location.summary || location.description || UNKNOWN}</p>
      <small className="poi-detail-meta">简介来源：{summaryMeta.source} · 更新：{summaryMeta.updatedAt}</small>
      {review && (
        <section className="poi-review-summary" aria-labelledby={`poi-review-summary-${location.id}`}>
          <h4 id={`poi-review-summary-${location.id}`}>游客评价摘要</h4>
          <p>{review.text}</p>
          <small>来源：{review.source} · 更新：{review.updatedAt}</small>
        </section>
      )}
      <DetailRow label="类型" value={location.category ? mapCategoryLabel(location.category) : null} location={location} field="category" />
      <DetailRow label="地址" value={location.address} location={location} field="address" />
      <DetailRow label="坐标" value={Number.isFinite(location.lat) && Number.isFinite(location.lng) ? `${Number(location.lat).toFixed(5)}, ${Number(location.lng).toFixed(5)}` : null} location={location} field="coordinates" />
      <DetailRow label="建议停留" value={location.suggested_duration_minutes ? `${location.suggested_duration_minutes} 分钟` : null} location={location} field="suggested_duration_minutes" />
      <DetailRow label="开放时间" value={location.opening_hours} location={location} field="opening_hours" realtime />
      <DetailRow label="预约说明" value={location.reservation} location={location} field="reservation" realtime />
      <DetailRow label="价格参考" value={location.price} location={location} field="price" realtime />
      <DetailRow label="适合人群" value={location.suitable_for} location={location} field="suitable_for" />
      <DetailRow label="不适合人群" value={location.unsuitable_for} location={location} field="unsuitable_for" />
      <div className="poi-detail-footer">
        <ClockCircleOutlined /> 更新：{formatUpdatedAt(location.updated_at)}
        <a href={buildBaiduPlaceSearchUrl(location)} target="_blank" rel="noreferrer">
          <LinkOutlined /> 百度地图搜索导航
        </a>
        {Boolean(location.sources?.[0]?.url) && (
          <a href={String(location.sources?.[0]?.url)} target="_blank" rel="noreferrer"><LinkOutlined /> 查看来源</a>
        )}
      </div>
    </div>
  );
};

export default PoiDetailContent;
