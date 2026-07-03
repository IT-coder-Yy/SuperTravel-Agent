import React, { useState, useEffect } from 'react';
import {
  Card,
  List,
  Tag,
  Descriptions,
  Alert,
  Spin,
  Button,
  Typography,
  Badge
} from 'antd';
import {
  CloudServerOutlined,
  InfoCircleOutlined,
  ReloadOutlined,
  ToolOutlined
} from '@ant-design/icons';
import { apiClient, MCPServerInfo, MCPServersResponse } from '../services/apiClient';

const { Title, Text } = Typography;

const MCPServersPanel: React.FC = () => {
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
      console.error('获取MCP服务器失败:', error);
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

  const renderServerIcon = (server: MCPServerInfo) => {
    if (server.name === 'baidu-map') {
      return '🗺️';
    } else if (server.name === '12306-mcp') {
      return '🚄';
    } else if (server.name === 'xhs-mcp') {
      return '📖';
    } else if (server.name === 'RedNote MCP') {
      return '📝';
    } else if (server.name === 'fetch') {
      return '🌐';
    } else if (server.name.includes('search')) {
      return '🔍';
    }
    return <CloudServerOutlined />;
  };

  const renderServerType = (type: string) => {
    const typeConfig = {
      sse: { color: 'blue', text: 'SSE' },
      stdio: { color: 'green', text: 'STDIO' }
    };
    const config = typeConfig[type as keyof typeof typeConfig] || { color: 'default', text: type };
    return <Tag color={config.color}>{config.text}</Tag>;
  };

  if (loading) {
    return (
      <div className="mcp-servers-panel" style={{
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        padding: '24px'
      }}>
        <Spin size="large" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="mcp-servers-panel" style={{ padding: '24px' }}>
        <Alert
          message="加载失败"
          description={error}
          type="error"
          showIcon
          action={
            <Button size="small" onClick={fetchMCPServers}>
              重试
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <div className="mcp-servers-panel" style={{ padding: '24px' }}>
      <div style={{ marginBottom: '24px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <Title level={2} style={{ margin: 0 }}>
            <CloudServerOutlined style={{ marginRight: '8px' }} />
            MCP服务器管理
          </Title>
          <Button
            icon={<ReloadOutlined />}
            onClick={fetchMCPServers}
            type="primary"
          >
            刷新
          </Button>
        </div>

        <Alert
          message="MCP (Model Context Protocol) 服务器状态"
          description="MCP协议允许智能体与外部工具和服务进行安全、标准化的通信。当前系统已集成百度地图、网络抓取和搜索等服务。"
          type="info"
          icon={<InfoCircleOutlined />}
          style={{ marginBottom: '16px' }}
        />

        <div style={{ display: 'flex', gap: '16px', marginBottom: '24px' }}>
          <Card size="small" style={{ flex: 1 }}>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '24px', fontWeight: 'bold', color: '#1890ff' }}>{totalServers}</div>
              <div style={{ color: '#666' }}>总服务器数</div>
            </div>
          </Card>
          <Card size="small" style={{ flex: 1 }}>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '24px', fontWeight: 'bold', color: '#52c41a' }}>{activeServers}</div>
              <div style={{ color: '#666' }}>活跃服务器</div>
            </div>
          </Card>
          <Card size="small" style={{ flex: 1 }}>
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '24px', fontWeight: 'bold', color: '#fa8c16' }}>
                {servers.reduce((sum, server) => sum + server.tools_count, 0)}
              </div>
              <div style={{ color: '#666' }}>可用工具数</div>
            </div>
          </Card>
        </div>
      </div>

      <List
        grid={{ gutter: 16, xs: 1, sm: 1, md: 2, lg: 2, xl: 3 }}
        dataSource={servers}
        renderItem={(server) => (
          <List.Item>
            <Card
              hoverable
              style={{ height: '100%' }}
              bodyStyle={{ padding: '20px' }}
            >
              <div style={{ display: 'flex', alignItems: 'center', marginBottom: '16px' }}>
                <div style={{ fontSize: '24px', marginRight: '12px' }}>
                  {renderServerIcon(server)}
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <Text strong style={{ fontSize: '16px' }}>{server.name}</Text>
                    <Badge
                      status={server.disabled ? 'default' : server.tools_count > 0 ? 'success' : 'warning'}
                      text={getServerStatusText(server)}
                    />
                  </div>
                  <div style={{ marginTop: '4px' }}>
                    {renderServerType(server.type)}
                    <Tag icon={<ToolOutlined />} style={{ marginLeft: '8px' }}>
                      {server.tools_count} 工具
                    </Tag>
                  </div>
                </div>
              </div>

              {server.description && (
                <Text type="secondary" style={{ display: 'block', marginBottom: '16px', fontSize: '13px' }}>
                  {server.description}
                </Text>
              )}

              <Descriptions size="small" column={1} style={{ marginBottom: '16px' }}>
                <Descriptions.Item label="协议类型">
                  {server.type.toUpperCase()}
                </Descriptions.Item>
                {server.config.command && (
                  <Descriptions.Item label="命令">
                    <Text code style={{ fontSize: '12px' }}>{server.config.command}</Text>
                  </Descriptions.Item>
                )}
                {server.config.sse_url && (
                  <Descriptions.Item label="SSE URL">
                    <Text code style={{ fontSize: '12px' }}>{server.config.sse_url}</Text>
                  </Descriptions.Item>
                )}
                {server.config.args && (
                  <Descriptions.Item label="参数">
                    <Text code style={{ fontSize: '12px' }}>{server.config.args.join(' ')}</Text>
                  </Descriptions.Item>
                )}
              </Descriptions>

              {server.name === 'baidu-map' && (
                <div style={{ marginBottom: '12px' }}>
                  <Tag color={server.config.env_status?.BAIDU_MAP_API_KEY ? 'green' : 'red'}>
                    BAIDU_MAP_API_KEY {server.config.env_status?.BAIDU_MAP_API_KEY ? 'loaded' : 'missing'}
                  </Tag>
                </div>
              )}

              {server.name === 'baidu-map' && (
                <Alert
                  message="百度地图服务"
                  description={
                    <div>
                      <div>• 地理编码与逆地理编码</div>
                      <div>• 地点检索与周边搜索</div>
                      <div>• 路线规划与导航</div>
                      <div>• 行政区划查询</div>
                    </div>
                  }
                  type="success"
                  showIcon
                />
              )}

              {server.name === '12306-mcp' && (
                <Alert
                  message="12306火车票查询服务"
                  description={
                    <div>
                      <div>• 查询12306购票信息</div>
                      <div>• 过滤列车信息</div>
                      <div>• 过站查询</div>
                      <div>• 中转查询</div>
                    </div>
                  }
                  type="warning"
                  showIcon
                />
              )}

              {server.name === 'fetch' && (
                <Alert
                  message="网络抓取服务"
                  description="提供HTTP请求和网页内容抓取功能，支持GET、POST等多种请求方式"
                  type="info"
                  showIcon
                />
              )}

              {server.name.includes('search') && (
                <Alert
                  message="搜索服务"
                  description="提供网络搜索功能，可以获取实时的搜索结果和相关信息"
                  type="info"
                  showIcon
                />
              )}
            </Card>
          </List.Item>
        )}
      />

      {servers.length === 0 && (
        <div style={{
          textAlign: 'center',
          padding: '60px 20px',
          color: '#999'
        }}>
          <CloudServerOutlined style={{ fontSize: '48px', marginBottom: '16px' }} />
          <div style={{ fontSize: '16px' }}>暂无MCP服务器配置</div>
          <div style={{ fontSize: '14px', marginTop: '8px' }}>
            请在配置文件中添加MCP服务器设置
          </div>
        </div>
      )}
    </div>
  );
};

export default MCPServersPanel; 
