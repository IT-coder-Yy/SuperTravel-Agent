import React from 'react';
import { ClockCircleOutlined, LinkOutlined, StarFilled } from '@ant-design/icons';
import type { LocationPoint } from './MapComponent';

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
  const images = Array.isArray(location.images) ? location.images.filter((url) => /^https?:\/\//.test(url)) : [];
  const imageMeta = fieldMeta(location, 'images');
  const ratingMeta = fieldMeta(location, 'rating');
  const summaryMeta = fieldMeta(location, 'summary');
  return (
    <div className="poi-detail-content" data-poi-id={location.poi_id || location.id}>
      {images.length > 0 && imageMeta.reliable ? (
        <img className="poi-detail-image" src={images[0]} alt={`${location.name}参考图片`} loading="lazy" referrerPolicy="no-referrer" />
      ) : (
        <div className="poi-detail-image-empty">暂无可靠图片</div>
      )}
      <small className="poi-detail-meta">图片来源：{imageMeta.source} · 更新：{imageMeta.updatedAt}</small>
      <div className="poi-detail-heading">
        <div>
          <strong>{location.name}</strong>
          <span>{location.category || '类型待确认'}</span>
        </div>
        <span className="poi-rating"><StarFilled /> {ratingMeta.reliable ? location.rating ?? '待确认' : '待确认'}</span>
      </div>
      <small className="poi-detail-meta">评分来源：{ratingMeta.source} · 更新：{ratingMeta.updatedAt}</small>
      <p className="poi-summary">{location.summary || location.description || UNKNOWN}</p>
      <small className="poi-detail-meta">简介来源：{summaryMeta.source} · 更新：{summaryMeta.updatedAt}</small>
      <DetailRow label="类型" value={location.category} location={location} field="category" />
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
        {Boolean(location.sources?.[0]?.url) && (
          <a href={String(location.sources?.[0]?.url)} target="_blank" rel="noreferrer"><LinkOutlined /> 查看来源</a>
        )}
      </div>
    </div>
  );
};

export default PoiDetailContent;
