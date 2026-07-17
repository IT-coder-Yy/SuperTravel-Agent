import React, { useEffect, useState } from 'react';
import { Alert, Badge, Button, Card, Skeleton, Tag, Typography } from 'antd';
import { CloudServerOutlined, ReloadOutlined, ToolOutlined } from '@ant-design/icons';
import { apiClient, MCPServerInfo, MCPServersResponse } from '../services/apiClient';

const { Text } = Typography;

interface MCPServersPanelProps {
  embedded?: boolean;
}

const MCPServersPanel: React.FC<MCPServersPanelProps> = ({ embedded = false }) => {
  const [servers, setServers] = useState<MCPServerInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [totalServers, setTotalServers] = useState(0);
  const [activeServers, setActiveServers] = useState(0);

  const fetchMCPServers = async () => {
    try {
      setLoading(true);
      setError(null);
      const data: MCPServersResponse = await apiClient.getMcpServers();
      setServers(data.servers);
      setTotalServers(data.total_servers);
      setActiveServers(data.active_servers);
    } catch (error: unknown) {
      const errorMessage = error instanceof Error ? error.message : '获取MCP服务器信息失败';
      setError(errorMessage);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMCPServers();
  }, []);

  const getServerStatusText = (server: MCPServerInfo) => {
    if (server.disabled) return '已禁用';
    return server.tools_count > 0 ? '已连接' : '未连接';
  };

  const getServerIcon = (server: MCPServerInfo) => {
    if (server.name === 'baidu-map') return '地图';
    if (server.name === '12306-mcp') return '车票';
    if (server.name === 'xhs-mcp' || server.name === 'RedNote MCP') return '攻略';
    if (server.name === 'fetch') return '网页';
    if (server.name.includes('search')) return '搜索';
    return '服务';
  };

  return (
    <div className={embedded ? 'settings-section mcp-servers-panel' : 'travel-page mcp-servers-panel'} style={embedded ? undefined : { padding: '24px' }}>
      <Card
        title={
          <div className="settings-card-title">
            <CloudServerOutlined />
            <span>MCP 服务</span>
            <Tag>{activeServers}/{totalServers} 活跃</Tag>
          </div>
        }
        extra={<Button icon={<ReloadOutlined />} onClick={fetchMCPServers}>刷新</Button>}
      >
        {error && (
          <Alert
            message="加载失败"
            description={error}
            type="error"
            showIcon
            action={<Button size="small" onClick={fetchMCPServers}>重试</Button>}
            style={{ marginBottom: 16 }}
          />
        )}

        {loading ? (
          <Skeleton active paragraph={{ rows: 5 }} />
        ) : servers.length === 0 ? (
          <div className="travel-empty-panel compact">
            <div className="travel-empty-icon"><CloudServerOutlined /></div>
            <h3>暂无 MCP 服务配置</h3>
            <p>添加地图、搜索、票务等服务后，智能体会在规划时调用它们。</p>
          </div>
        ) : (
          <div className="brief-card-grid">
            {servers.map((server) => (
              <div className="brief-card" key={server.name}>
                <div className="brief-card-head">
                  <div className="brief-card-icon text">{getServerIcon(server)}</div>
                  <div>
                    <h3>{server.name}</h3>
                    <Badge
                      status={server.disabled ? 'default' : server.tools_count > 0 ? 'success' : 'warning'}
                      text={getServerStatusText(server)}
                    />
                  </div>
                </div>
                <p>{server.description || '外部服务连接，用于补充旅行规划所需的实时能力。'}</p>
                <div className="brief-tag-row">
                  <Tag>{server.type.toUpperCase()}</Tag>
                  <Tag icon={<ToolOutlined />}>{server.tools_count} 个工具</Tag>
                  {server.name === 'baidu-map' && (
                    <Tag color={server.config.env_status?.BAIDU_MAP_API_KEY ? 'green' : 'warning'}>
                      地图密钥{server.config.env_status?.BAIDU_MAP_API_KEY ? '已配置' : '待配置'}
                    </Tag>
                  )}
                </div>
                <Text type="secondary" className="brief-card-footnote">
                  {server.disabled ? '当前不会参与智能体调用。' : '可在旅行规划中按需调用。'}
                </Text>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
};

export default MCPServersPanel;
