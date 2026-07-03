import React, { useState, useEffect } from 'react';
import {
  Card,
  Button,
  Space,
  Typography,
  Alert,
  Spin
} from 'antd';
import {
  PictureOutlined,
  ReloadOutlined
} from '@ant-design/icons';

const { Text, Title } = Typography;

const DEFAULT_POWERPAINT_URL = 'http://localhost:7860';
const POWERPAINT_STATUS_ENDPOINT = '/api/powerpaint/status';
const POWERPAINT_START_COMMAND = 'powershell -ExecutionPolicy Bypass -File D:/vscode/project/SuperTravelAgent/scripts/start_powerpaint.ps1';

interface PhotoEditorProps {
  style?: React.CSSProperties;
}

interface PowerPaintStatusResponse {
  url?: string;
  powerpaintUrl?: string;
  powerpaint_url?: string;
  data?: {
    url?: string;
    powerpaintUrl?: string;
    powerpaint_url?: string;
  };
}

const normalizePowerPaintUrl = (url?: string) => {
  const value = url?.trim();
  if (!value) {
    return DEFAULT_POWERPAINT_URL;
  }

  return /^https?:\/\//i.test(value) ? value : `http://${value}`;
};

const getPowerPaintUrlFromStatus = (status: PowerPaintStatusResponse) => {
  return normalizePowerPaintUrl(
    status.url ||
    status.powerpaintUrl ||
    status.powerpaint_url ||
    status.data?.url ||
    status.data?.powerpaintUrl ||
    status.data?.powerpaint_url
  );
};

const PhotoEditor: React.FC<PhotoEditorProps> = ({ style }) => {
  const [serviceStatus, setServiceStatus] = useState<'loading' | 'connected' | 'disconnected'>('loading');
  const [iframeLoading, setIframeLoading] = useState<boolean>(true);
  const [powerpaintUrl, setPowerpaintUrl] = useState<string>(DEFAULT_POWERPAINT_URL);

  const fetchPowerPaintUrl = async () => {
    try {
      const response = await fetch(POWERPAINT_STATUS_ENDPOINT, {
        method: 'GET',
        cache: 'no-store'
      });

      if (!response.ok) {
        throw new Error(`状态接口返回 ${response.status}`);
      }

      const status = await response.json() as PowerPaintStatusResponse;
      return getPowerPaintUrlFromStatus(status);
    } catch (error) {
      console.info('获取 PowerPaint 状态失败，使用默认地址:', error);
      return DEFAULT_POWERPAINT_URL;
    }
  };

  // 检查PowerPaint服务状态
  const checkServiceStatus = async () => {
    setServiceStatus('loading');
    const nextPowerpaintUrl = await fetchPowerPaintUrl();
    setPowerpaintUrl(nextPowerpaintUrl);

    try {
      await fetch(nextPowerpaintUrl, {
        method: 'GET',
        mode: 'no-cors' // 避免CORS问题
      });
      setServiceStatus('connected');
    } catch (error) {
      console.error('PowerPaint服务检查失败:', error);
      setServiceStatus('disconnected');
    }
  };

  // 组件加载时检查服务状态
  useEffect(() => {
    checkServiceStatus();
    // 每30秒检查一次服务状态
    const interval = setInterval(checkServiceStatus, 30000);
    return () => clearInterval(interval);
  }, []);

  // 刷新iframe
  const refreshIframe = () => {
    setIframeLoading(true);
    const iframe = document.getElementById('powerpaint-iframe') as HTMLIFrameElement;
    if (iframe) {
      iframe.src = powerpaintUrl;
    }
  };

  // iframe加载完成
  const handleIframeLoad = () => {
    setIframeLoading(false);
    if (serviceStatus !== 'connected') {
      setServiceStatus('connected');
    }
  };

  // iframe加载错误
  const handleIframeError = () => {
    setIframeLoading(false);
    setServiceStatus('disconnected');
  };

  const getStatusColor = () => {
    switch (serviceStatus) {
      case 'connected': return '#52c41a';
      case 'disconnected': return '#ff4d4f';
      default: return '#faad14';
    }
  };

  const getStatusText = () => {
    switch (serviceStatus) {
      case 'connected': return 'PowerPaint 服务已连接';
      case 'disconnected': return 'PowerPaint 服务未连接';
      default: return '检查服务状态中...';
    }
  };

  return (
    <Card
      title={
        <Space>
          <PictureOutlined />
          <Title level={4} style={{ margin: 0 }}>PowerPaint 图片编辑</Title>
          <span
            style={{
              color: getStatusColor(),
              fontSize: '12px',
              marginLeft: '16px'
            }}
          >
            ● {getStatusText()}
          </span>
        </Space>
      }
      style={style}
      bodyStyle={{ padding: '16px' }}
      extra={
        <Button
          icon={<ReloadOutlined />}
          onClick={refreshIframe}
          size="small"
          disabled={serviceStatus === 'disconnected'}
        >
          刷新
        </Button>
      }
    >
      {serviceStatus === 'disconnected' ? (
        <Alert
          message="PowerPaint 服务未运行"
          description={
            <div>
              <p>当前未能连接到 PowerPaint。请确认服务已启动，并监听地址：{powerpaintUrl}。</p>
              <p>如果尚未启动 PowerPaint，请在终端运行以下命令：</p>
              <code style={{
                display: 'block',
                background: '#f5f5f5',
                padding: '8px',
                borderRadius: '4px',
                marginTop: '8px'
              }}>
                {POWERPAINT_START_COMMAND}
              </code>
              <Button
                type="link"
                onClick={checkServiceStatus}
                style={{ paddingLeft: 0, marginTop: '8px' }}
              >
                重新检查服务状态
              </Button>
            </div>
          }
          type="warning"
          showIcon
        />
      ) : (
        <div style={{ position: 'relative', height: '80vh', minHeight: '600px' }}>
          {iframeLoading && (
            <div style={{
              position: 'absolute',
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              background: 'rgba(255, 255, 255, 0.9)',
              zIndex: 10
            }}>
              <Spin size="large" />
              <Text style={{ marginTop: '16px' }}>正在加载 PowerPaint 界面...</Text>
            </div>
          )}

          <iframe
            id="powerpaint-iframe"
            src={powerpaintUrl}
            style={{
              width: '100%',
              height: '100%',
              border: '1px solid #d9d9d9',
              borderRadius: '6px'
            }}
            onLoad={handleIframeLoad}
            onError={handleIframeError}
            title="PowerPaint 图片编辑器"
          />

          {serviceStatus === 'connected' && !iframeLoading && (
            <div style={{
              position: 'absolute',
              bottom: '16px',
              right: '16px',
              background: 'rgba(82, 196, 26, 0.9)',
              color: 'white',
              padding: '4px 12px',
              borderRadius: '12px',
              fontSize: '12px',
              zIndex: 5
            }}>
              ✓ PowerPaint 已就绪
            </div>
          )}
        </div>
      )}
    </Card>
  );
};

export default PhotoEditor; 
