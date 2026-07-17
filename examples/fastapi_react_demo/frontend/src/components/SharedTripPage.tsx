import React, { useEffect, useState } from 'react';
import { Alert, Empty, Spin, Tag } from 'antd';
import { CalendarOutlined, CheckCircleOutlined, LinkOutlined } from '@ant-design/icons';
import { useParams } from 'react-router-dom';

const SharedTripPage: React.FC = () => {
  const { token = '' } = useParams();
  const [snapshot, setSnapshot] = useState<Record<string, any> | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    fetch(`/api/shares/${encodeURIComponent(token)}`, { signal: controller.signal })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(payload?.detail?.message || '分享不存在或已关闭');
        setSnapshot(payload);
      })
      .catch((reason) => {
        if (reason?.name !== 'AbortError') setError(reason instanceof Error ? reason.message : '无法打开分享');
      });
    return () => controller.abort();
  }, [token]);

  if (error) return <main className="shared-trip-page"><Alert type="error" showIcon message={error} /></main>;
  if (!snapshot) return <main className="shared-trip-page shared-trip-loading"><Spin /><span>正在打开只读行程</span></main>;

  const plan = snapshot.plan || {};
  const document = snapshot.document || {};
  const overview = document.destination_overview || {};
  const outbound = document.outbound_transport || {};
  const hotels = document.hotel_recommendations || {};
  const returnTransport = document.return_transport || {};
  const reminders = document.friendly_reminders || {};
  const mapGuidance = document.map_guidance || {};
  const delivery = document.delivery || {};
  const days = Array.isArray(document.itinerary?.days) && document.itinerary.days.length
    ? document.itinerary.days
    : Array.isArray(plan.trip_days) && plan.trip_days.length
    ? plan.trip_days
    : Array.from({ length: Number(plan.days || 0) }, (_, index) => ({
      day: index + 1,
      activities: (plan.activities || []).filter((activity: any) => Number(activity.day) === index + 1),
    }));
  const budget = snapshot.budget || {};
  const knownTotal = budget.known_total ?? budget.estimated_total;
  const categoryLabels: Record<string, string> = {
    transport: '交通', accommodation: '住宿', food: '餐饮', tickets: '门票', other: '其他',
  };
  const renderTransport = (section: any) => section.options?.length ? section.options.map((option: any) => (
    <div className="shared-trip-activity" key={option.option_id}>
      <strong>{option.mode} {option.service_number || ''}</strong>
      <span>{option.departure_station || '待确认'} → {option.arrival_station || '待确认'} · {option.departure_time || '待确认'}-{option.arrival_time || '待确认'}</span>
      <small>价格：{option.price == null ? '待确认' : `${option.currency || 'CNY'} ${option.price}`} · 余量：{option.availability || 'unknown'} · 查询：{option.queried_at || '待确认'}</small>
    </div>
  )) : <p>{section.status_reason || '暂无可靠实时数据'}</p>;

  return (
    <main className="shared-trip-page">
      <header className="shared-trip-header">
        <div><span>只读行程</span><h1>{snapshot.title || plan.title || '旅行行程'}</h1></div>
        <Tag color="green">只读快照</Tag>
      </header>
      {snapshot.document && <section className="shared-trip-section shared-destination-overview">
        <h2>1. 目的地介绍</h2>
        {overview.cover_image && <figure className="shared-cover-image" tabIndex={0}>
          <img src={overview.cover_image.url} alt={overview.cover_image.alt || overview.name_zh || '目的地'} />
          <figcaption><a href={overview.cover_image.photographer_url} target="_blank" rel="noreferrer">{overview.cover_image.photographer_name}</a> / <a href={overview.cover_image.unsplash_url} target="_blank" rel="noreferrer">Unsplash</a></figcaption>
        </figure>}
        <p><strong>{overview.name_zh || '待确认'}{overview.name_en ? ` / ${overview.name_en}` : ''}</strong> · {overview.country_name || '国家/地区待确认'} · {overview.timezone || '时区待确认'}</p>
        <p>{overview.area_overview || overview.status_reason || '暂无可靠目的地介绍'}</p>
      </section>}
      {snapshot.document && <section className="shared-trip-section"><h2>2. 出发地到目的地的车票</h2>{renderTransport(outbound)}</section>}
      {snapshot.document && <section className="shared-trip-section"><h2>3. 酒店推荐</h2>{hotels.recommendations?.length ? hotels.recommendations.map((hotel: any) => <div className="shared-trip-activity" key={hotel.hotel_id}><strong>{hotel.name}</strong><span>{hotel.area} · 每晚 {hotel.nightly_price == null ? '待确认' : `${hotel.currency || 'CNY'} ${hotel.nightly_price}`} · 评分 {hotel.rating ?? '待确认'}</span><small>来源：{hotel.source_reference_id || '暂无可靠来源'} · 更新：{hotel.updated_at || '待确认'}</small></div>) : <p>{hotels.status_reason || '暂无可靠实时数据'}</p>}</section>}
      {snapshot.plan && <section className="shared-trip-section"><h2><CalendarOutlined /> 4. 日程规划（景点与美食）</h2>{days.map((day: any) => (
        <div className="shared-trip-day" key={day.day}><h3>Day {day.day}{day.theme ? ` · ${day.theme}` : ''}</h3>{day.activities?.length ? day.activities.map((activity: any) => (
          <div className="shared-trip-activity" key={activity.activity_id || activity.title}><strong>{activity.start_time ? `${activity.start_time} ` : ''}{activity.title}</strong><span>{activity.place?.name || '地点待确认'}{activity.estimated_cost != null ? ` · 约 ¥${Math.round(activity.estimated_cost)}` : ''}</span></div>
        )) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当天暂无活动" />}</div>
      ))}</section>}
      {snapshot.document && <section className="shared-trip-section"><h2>5. 目的地返回出发地的车票</h2>{renderTransport(returnTransport)}</section>}
      {snapshot.document && <section className="shared-trip-section"><h2>6. 友情提醒</h2>{reminders.items?.length ? reminders.items.map((item: any, index: number) => <div className="shared-source-row" key={`${item.category}-${index}`}><strong>{item.category}</strong><span>{item.content}</span><small>来源：{item.source_reference_id || '暂无可靠来源'} · 更新：{item.updated_at || '待确认'}</small></div>) : <p>{reminders.status_reason || '暂无可靠实时数据'}</p>}</section>}
      {snapshot.document && <section className="shared-trip-section"><h2>7. 景点和地图提醒</h2><p>当前版本日程包含 {mapGuidance.location_ids?.length || 0} 个地图地点。</p><p>{mapGuidance.status_reason || mapGuidance.status || '待确认'}</p></section>}
      {snapshot.budget && <section className="shared-trip-section"><h2>预算</h2><div className="shared-budget-grid"><div><span>总预算</span><strong>{budget.budget_total == null ? '待确认' : `¥${Math.round(budget.budget_total)}`}</strong></div><div><span>人均预算</span><strong>{budget.budget_per_person == null ? '待确认' : `¥${Math.round(budget.budget_per_person)}`}</strong></div><div><span>可统计费用</span><strong>{knownTotal == null ? '待确认' : `¥${Math.round(knownTotal)}`}</strong></div><div><span>未知费用</span><strong>{budget.unknown_count || 0} 项</strong></div>{Object.entries(budget.categories || {}).map(([key, value]) => <div key={key}><span>{categoryLabels[key] || key}</span><strong>¥{Math.round(Number(value) || 0)}</strong></div>)}</div>{budget.over_budget && <Alert className="shared-budget-alert" type="warning" showIcon message={`预计超出预算 ¥${Math.round(Number(budget.overrun_amount) || 0)}`} />}{budget.source_label && <p className="shared-budget-source">{budget.source_label}{budget.updated_at ? ` · 更新：${budget.updated_at}` : ''}</p>}</section>}
      {snapshot.checklist && <section className="shared-trip-section"><h2><CheckCircleOutlined /> 清单</h2>{snapshot.checklist.map((item: any) => <div className="shared-list-row" key={item.id}><span>{item.completed ? '已完成' : '待处理'}</span><strong>{item.text}</strong></div>)}</section>}
      {snapshot.notes && <section className="shared-trip-section"><h2>便签</h2>{snapshot.notes.map((note: any) => <p key={note.id}>{note.content}</p>)}</section>}
      {snapshot.sources && <section className="shared-trip-section"><h2>来源</h2>{snapshot.sources.length ? snapshot.sources.map((source: any, index: number) => <div className="shared-source-row" key={`${source.title}-${index}`}><strong>{source.title || '未命名来源'}</strong><span>{source.type || '参考资料'} · 更新：{source.updated_at || '待确认'} · 可信度：{source.confidence ?? '待确认'}</span>{(source.related_fields?.length || source.related_places?.length) && <span>对应：{[...(source.related_places || []), ...(source.related_fields || [])].join('、')}</span>}{source.url && <a href={source.url} target="_blank" rel="noreferrer"><LinkOutlined /> 打开来源</a>}</div>) : <p>暂无可靠来源</p>}</section>}
      {snapshot.document && <section className="shared-trip-section"><h2>8. 下载与分享</h2><p>{delivery.markdown_filename || 'Markdown 文件待确认'} · 当前分享范围：{snapshot.scopes?.join('、') || '行程'}</p><Tag>{snapshot.plan_id} · v{snapshot.version}</Tag></section>}
      <footer>该页面仅包含创建者选择公开的只读内容，不包含私有对话和内部处理记录。</footer>
    </main>
  );
};

export default SharedTripPage;
