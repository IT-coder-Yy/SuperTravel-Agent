import React, { useEffect, useState } from 'react';
import { Alert, Button, Card, Skeleton, Tag, Typography } from 'antd';
import { InfoCircleOutlined, ReloadOutlined, ToolOutlined } from '@ant-design/icons';
import { apiClient, ToolInfo } from '../services/apiClient';

const { Text } = Typography;

interface ToolsPanelProps {
  embedded?: boolean;
}

const ToolsPanel: React.FC<ToolsPanelProps> = ({ embedded = false }) => {
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchTools = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiClient.getTools();
      setTools(data);
    } catch (error: unknown) {
      const errorMessage = error instanceof Error ? error.message : '获取工具列表失败';
      setError(errorMessage || '获取工具列表失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTools();
  }, []);

  return (
    <div className={embedded ? 'settings-section tools-panel' : 'travel-page tools-panel'} style={embedded ? undefined : { padding: '24px' }}>
      <Card
        title={
          <div className="settings-card-title">
            <ToolOutlined />
            <span>旅行工具</span>
            <Tag>{tools.length} 个</Tag>
          </div>
        }
        extra={<Button icon={<ReloadOutlined />} onClick={fetchTools} loading={loading}>刷新</Button>}
      >
        {error && (
          <Alert message="加载失败" description={error} type="error" style={{ marginBottom: 16 }} />
        )}

        {loading ? (
          <Skeleton active paragraph={{ rows: 5 }} />
        ) : tools.length === 0 ? (
          <div className="travel-empty-panel compact">
            <div className="travel-empty-icon"><InfoCircleOutlined /></div>
            <h3>暂无可用工具</h3>
            <p>检查后端工具注册后，这里会展示可用于行程规划的能力。</p>
          </div>
        ) : (
          <div className="brief-card-grid">
            {tools.map((tool) => {
              const params = Object.keys(tool.parameters || {});
              return (
                <div className="brief-card" key={tool.name}>
                  <div className="brief-card-head">
                    <div className="brief-card-icon"><ToolOutlined /></div>
                    <div>
                      <h3>{tool.name}</h3>
                      <Text type="secondary">{params.length > 0 ? `${params.length} 个参数` : '无需参数'}</Text>
                    </div>
                  </div>
                  <p>{tool.description || '该工具暂未提供说明。'}</p>
                  {params.length > 0 && (
                    <div className="brief-tag-row">
                      {params.slice(0, 4).map((param) => <Tag key={param}>{param}</Tag>)}
                      {params.length > 4 && <Tag>+{params.length - 4}</Tag>}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
};

export default ToolsPanel;
