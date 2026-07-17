import React from 'react';
import { Tabs, Typography } from 'antd';
import {
  CloudServerOutlined,
  SettingOutlined,
  ToolOutlined
} from '@ant-design/icons';
import { useLocation } from 'react-router-dom';
import SystemConfig from './SystemConfig';
import ToolsPanel from './ToolsPanel';
import MCPServersPanel from './MCPServersPanel';

const { Title, Paragraph } = Typography;

const getActiveTabFromPath = (pathname: string) => {
  if (pathname.includes('tools')) return 'tools';
  if (pathname.includes('mcp')) return 'services';
  if (pathname.includes('config')) return 'model';
  return 'model';
};

const SettingsPage: React.FC = () => {
  const location = useLocation();

  return (
    <div className="travel-page settings-page">
      <div className="travel-page-header">
        <div>
          <Title level={2}>设置</Title>
          <Paragraph>
            管理模型配置、旅行工具和外部服务连接，让规划过程保持稳定顺畅。
          </Paragraph>
        </div>
      </div>

      <Tabs
        defaultActiveKey={getActiveTabFromPath(location.pathname)}
        className="travel-tabs"
        items={[
          {
            key: 'model',
            label: (
              <span>
                <SettingOutlined />
                模型配置
              </span>
            ),
            children: <SystemConfig embedded />,
          },
          {
            key: 'tools',
            label: (
              <span>
                <ToolOutlined />
                旅行工具
              </span>
            ),
            children: <ToolsPanel embedded />,
          },
          {
            key: 'services',
            label: (
              <span>
                <CloudServerOutlined />
                MCP 服务
              </span>
            ),
            children: <MCPServersPanel embedded />,
          },
        ]}
      />
    </div>
  );
};

export default SettingsPage;
