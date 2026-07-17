import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, Empty, Input, Progress, Segmented, Select, Tag, Typography, message } from 'antd';
import {
  CopyOutlined,
  DatabaseOutlined,
  EnvironmentOutlined,
  PlusOutlined,
  ReloadOutlined,
  SearchOutlined
} from '@ant-design/icons';
import { apiClient, type TravelKnowledgeCity, type TravelKnowledgeSearchItem } from '../services/apiClient';
import { addSelectedKnowledgeContext } from '../hooks/useSelectedKnowledgeContext';

const { Title, Paragraph } = Typography;

const knowledgeTypes = [
  { label: '城市介绍', source: 'baidu_intro', description: '行政区划、地理环境、历史文化、交通经济与基础背景。' },
  { label: '地理位置', source: 'geo_location', description: '城市所属地区、省份、区位与路线规划上下文。' },
  { label: '旅游攻略', source: 'travel_guide', description: '2-3 天弹性行程、城区核心景点、近郊补充与美食建议。' },
];

const normalizeText = (value: string) => value.trim().toLowerCase();

const buildSearchText = (item: TravelKnowledgeCity) => [
  item.city,
  item.official_name,
  item.province,
  item.summary,
  item.best_for,
  item.tip,
  ...item.tags,
  ...item.chunks.flatMap((chunk) => [chunk.title, chunk.source, chunk.content])
].join(' ').toLowerCase();

const KnowledgeBase: React.FC = () => {
  const [query, setQuery] = useState('');
  const [cities, setCities] = useState<TravelKnowledgeCity[]>([]);
  const [totalCities, setTotalCities] = useState(0);
  const [totalChunks, setTotalChunks] = useState(0);
  const [searchItems, setSearchItems] = useState<TravelKnowledgeSearchItem[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [scope, setScope] = useState<'all' | 'domestic' | 'international'>('all');
  const [country, setCountry] = useState('all');
  const [knowledgeType, setKnowledgeType] = useState('all');

  const loadCities = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await apiClient.getTravelKnowledgeCities();
      setCities(response.cities);
      setTotalCities(response.total_cities);
      setTotalChunks(response.total_chunks);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '知识库加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadCities();
  }, [loadCities]);

  useEffect(() => {
    const keyword = query.trim();
    if (!keyword) {
      setSearchItems([]);
      return;
    }

    let cancelled = false;
    const timer = window.setTimeout(() => {
      setSearchLoading(true);
      apiClient.searchTravelKnowledge(keyword, 8)
        .then((response) => {
          if (!cancelled) {
            setSearchItems(response.items);
          }
        })
        .catch((requestError) => {
          if (!cancelled) {
            setError(requestError instanceof Error ? requestError.message : '知识库搜索失败');
            setSearchItems([]);
          }
        })
        .finally(() => {
          if (!cancelled) {
            setSearchLoading(false);
          }
        });
    }, 260);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query]);

  const filteredCities = useMemo(() => {
    const keyword = normalizeText(query);
    return cities.filter((item) => {
      if (keyword && !buildSearchText(item).includes(keyword)) return false;
      if (scope !== 'all' && item.scope !== scope) return false;
      if (country !== 'all' && item.country_code !== country) return false;
      if (knowledgeType !== 'all' && !(item.knowledge_types || []).includes(knowledgeType)) return false;
      return true;
    });
  }, [cities, country, knowledgeType, query, scope]);

  const countryOptions = useMemo(() => [
    { label: '全部国家/地区', value: 'all' },
    ...Array.from(new Map(cities.map((item) => [item.country_code, {
      label: item.country_name || item.country_code || '待确认', value: item.country_code || 'CN',
    }])).values()),
  ], [cities]);
  const knowledgeTypeOptions = useMemo(() => [
    { label: '全部知识类型', value: 'all' },
    ...Array.from(new Set(cities.flatMap((item) => item.knowledge_types || []))).sort()
      .map((value) => ({ label: value, value })),
  ], [cities]);

  const sourceCounts = useMemo(() => {
    return cities.reduce<Record<string, number>>((counts, city) => {
      city.chunks.forEach((chunk) => {
        counts[chunk.source] = (counts[chunk.source] || 0) + 1;
      });
      return counts;
    }, {});
  }, [cities]);

  const visibleCities = useMemo(() => {
    const limit = query.trim() ? 96 : 36;
    return filteredCities.slice(0, limit);
  }, [filteredCities, query]);

  const resultHint = query.trim()
    ? `检索到 ${searchItems.length} 个知识片段，匹配 ${filteredCities.length} 个城市`
    : `已加载 ${cities.length} 个城市，默认展示 ${visibleCities.length} 个常用卡片`;

  const copyKnowledgeItem = async (item: TravelKnowledgeSearchItem) => {
    const text = `【${item.city} / ${item.source} / ${item.title}】${item.snippet}`;
    await navigator.clipboard.writeText(text);
  };

  const addKnowledgeItemToPlan = (item: TravelKnowledgeSearchItem) => {
    const nextItems = addSelectedKnowledgeContext(item);
    message.success(`已加入当前规划上下文（${nextItems.length}条）`);
  };

  return (
    <div className="travel-page knowledge-page">
      <div className="travel-page-header">
        <div>
          <Title level={2}>知识库</Title>
          <Paragraph>
            当前接入本地旅行 RAG 知识库，可搜索中国大陆地级市的城市介绍、地理位置和轻量攻略片段。
          </Paragraph>
        </div>
        <Button loading={loading} icon={<ReloadOutlined />} onClick={loadCities}>重新加载</Button>
      </div>

      <div className="knowledge-hero">
        <div>
          <div className="section-kicker">LOCAL TRAVEL RAG</div>
          <h3>多智能体规划前的本地知识增强</h3>
          <p>
            聊天链路会在普通旅游攻略、行程、景点和城市问题中检索 Top 5 相关片段，
            并以 system_travel_rag_context 注入上下文，提升回答的具体性和一致性。
          </p>
        </div>
        <div className="knowledge-stats">
          <div><strong>{totalCities || '--'}</strong><span>城市</span></div>
          <div><strong>{totalChunks || '--'}</strong><span>知识片段</span></div>
          <div><strong>Top 5</strong><span>默认召回</span></div>
        </div>
      </div>

      <div className="knowledge-search-panel">
        <Input
          size="large"
          allowClear
          prefix={<SearchOutlined />}
          placeholder="搜索城市、省份、景点或旅行主题，例如：河南、郑州、少林寺、美食"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <div className="knowledge-filter-row">
          <Segmented value={scope} onChange={(value) => setScope(value as typeof scope)} options={[
            { label: '全部', value: 'all' }, { label: '国内', value: 'domestic' }, { label: '国外', value: 'international' },
          ]} />
          <Select value={country} onChange={setCountry} options={countryOptions} />
          <Select value={knowledgeType} onChange={setKnowledgeType} options={knowledgeTypeOptions} />
        </div>
        <span>{error ? `加载异常：${error}` : (searchLoading ? '正在检索知识库...' : resultHint)}</span>
      </div>

      {query.trim() && (
        <div className="knowledge-section knowledge-search-results">
          <div className="section-title-row">
            <div>
              <h3>知识条目搜索结果</h3>
              <p>结果来自本地 RAG 知识库，可加入当前规划上下文或复制到对话。</p>
            </div>
            <SearchOutlined />
          </div>
          {searchItems.length > 0 ? (
            <div className="knowledge-result-list">
              {searchItems.map((item) => (
                <section className="knowledge-result-card" key={`${item.city}-${item.source}-${item.title}`}>
                  <div className="knowledge-result-card-header">
                    <div>
                      <strong>{item.title}</strong>
                      <span>{item.city} · {item.country_name || item.province || '地区待补充'} · {item.knowledge_type || item.source} · {item.score.toFixed(1)}</span>
                    </div>
                    <div className="knowledge-result-actions">
                      <Button
                        size="small"
                        icon={<PlusOutlined />}
                        onClick={() => addKnowledgeItemToPlan(item)}
                      >
                        用于规划
                      </Button>
                      <Button
                        size="small"
                        icon={<CopyOutlined />}
                        onClick={() => void copyKnowledgeItem(item)}
                      >
                        复制
                      </Button>
                    </div>
                  </div>
                  <p>{item.snippet}</p>
                  <div className="brief-tag-row">
                    {item.matched_terms.slice(0, 6).map((term) => <Tag key={term}>{term}</Tag>)}
                  </div>
                </section>
              ))}
            </div>
          ) : (
            <Empty description={searchLoading ? '正在检索' : '没有检索到知识条目'} />
          )}
        </div>
      )}

      <div className="knowledge-city-grid">
        {visibleCities.length > 0 ? (
          visibleCities.map((item) => (
            <section className="knowledge-city-card" key={item.city}>
              <div className="knowledge-city-card-header">
                <div>
                  <strong>{item.city}</strong>
                  <span>{item.city_en || item.official_name} · {item.country_name || item.province || '地区信息待补充'}</span>
                </div>
                <EnvironmentOutlined />
              </div>
              <p>{item.summary}</p>
              <div className="brief-tag-row">
                {item.tags.slice(0, 5).map((tag) => <Tag key={tag}>{tag}</Tag>)}
              </div>
              <div className="knowledge-city-meta">
                <span>{item.best_for}</span>
                <small>{item.tip}{item.updated_at ? ` · 更新：${item.updated_at}` : ''}</small>
              </div>
            </section>
          ))
        ) : (
          <div className="knowledge-empty-result">
            <Empty
              description={loading ? '正在加载知识库' : '没有匹配到城市内容，换个城市、省份、景点或主题试试'}
            />
          </div>
        )}
      </div>

      <div className="knowledge-grid">
        {knowledgeTypes.map((item) => (
          <section className="knowledge-card" key={item.label}>
            <div className="knowledge-card-icon"><DatabaseOutlined /></div>
            <h3>{item.label}</h3>
            <p>{item.description}</p>
            <Progress
              percent={cities.length ? 100 : 0}
              showInfo={false}
              strokeColor="var(--travel-primary)"
            />
            <span>{sourceCounts[item.source] || 0} 条</span>
          </section>
        ))}
      </div>

      <div className="knowledge-section">
        <div className="section-title-row">
          <div>
            <h3>检索链路</h3>
            <p>轻量混合检索，无需重型向量数据库，适合本地演示和作品集展示。</p>
          </div>
          <SearchOutlined />
        </div>
        <div className="rag-flow">
          <span>用户问题</span>
          <span>关键词与城市命中</span>
          <span>正文重叠打分</span>
          <span>Top-K 片段</span>
          <span>注入智能体上下文</span>
        </div>
      </div>
    </div>
  );
};

export default KnowledgeBase;
