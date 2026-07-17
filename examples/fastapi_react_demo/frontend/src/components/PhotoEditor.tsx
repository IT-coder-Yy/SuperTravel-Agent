import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Spin,
  Typography
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

interface PhotoTool {
  key: string;
  label: string;
  title: string;
  description: string;
  hint: string;
}

const photoTools: PhotoTool[] = [
  {
    key: 'inpaint',
    label: '局部重绘',
    title: '修复照片里的局部瑕疵',
    description: '适合处理路人、污点、天空留白、衣物褶皱等局部问题。',
    hint: '上传照片后，在编辑器里涂抹需要修改的区域，再生成新的局部结果。'
  },
  {
    key: 'expand',
    label: '图片扩展',
    title: '延展画面边缘和构图比例',
    description: '适合把竖图改成横版封面，或为行程海报补足天空、街景和留白。',
    hint: '先确定目标比例，再扩展画布边缘，保留主体位置会更自然。'
  },
  {
    key: 'remove',
    label: '物体移除',
    title: '移除路人、杂物和遮挡物',
    description: '适合清理景点人群、桌面杂物、反光遮挡，让照片主体更干净。',
    hint: '框选或涂抹需要移除的对象，尽量避开主体边缘和复杂纹理。'
  },
  {
    key: 'cover',
    label: '旅行封面',
    title: '生成更像旅行内容封面的构图',
    description: '适合小红书、朋友圈、作品集展示和行程分享的首图包装。',
    hint: '优先选择主体清晰、天空或街景留白充足的照片，封面文字会更好排版。'
  }
];

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
  const [activeToolKey, setActiveToolKey] = useState<string>(photoTools[0].key);

  const activeTool = useMemo(() => {
    return photoTools.find((tool) => tool.key === activeToolKey) ?? photoTools[0];
  }, [activeToolKey]);

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

  const checkServiceStatus = async () => {
    setServiceStatus('loading');
    const nextPowerpaintUrl = await fetchPowerPaintUrl();
    setPowerpaintUrl(nextPowerpaintUrl);

    try {
      await fetch(nextPowerpaintUrl, {
        method: 'GET',
        mode: 'no-cors'
      });
      setServiceStatus('connected');
    } catch (error) {
      console.error('PowerPaint 服务检查失败', error);
      setServiceStatus('disconnected');
    }
  };

  useEffect(() => {
    checkServiceStatus();
    const interval = setInterval(checkServiceStatus, 30000);
    return () => clearInterval(interval);
  }, []);

  const refreshIframe = () => {
    setIframeLoading(true);
    const iframe = document.getElementById('powerpaint-iframe') as HTMLIFrameElement;
    if (iframe) {
      iframe.src = powerpaintUrl;
    }
  };

  const handleIframeLoad = () => {
    setIframeLoading(false);
    if (serviceStatus !== 'connected') {
      setServiceStatus('connected');
    }
  };

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
      case 'connected': return '编辑服务已连接';
      case 'disconnected': return '编辑服务未连接';
      default: return '检查服务状态中...';
    }
  };

  return (
    <div className="travel-page photo-editor-page" style={style}>
      <div className="travel-page-header">
        <div>
          <Title level={2}>旅行照片</Title>
          <Text>为旅途照片做修复、扩图、局部重绘和出片前处理。</Text>
        </div>
        <Button
          icon={<ReloadOutlined />}
          onClick={refreshIframe}
          disabled={serviceStatus === 'disconnected'}
        >
          重新加载
        </Button>
      </div>

      <div className="photo-workbench">
        <section className="photo-workbench-rail">
          <div className="photo-rail-icon"><PictureOutlined /></div>
          <h3>照片处理工作台</h3>
          <p>适合处理旅行照片中的路人、构图边缘、天空留白和社交平台封面比例。</p>

          <div className="photo-status" style={{ color: getStatusColor() }}>
            <span />
            {getStatusText()}
          </div>

          <div className="photo-tool-list">
            {photoTools.map((tool) => (
              <button
                key={tool.key}
                type="button"
                className={tool.key === activeToolKey ? 'active' : undefined}
                onClick={() => setActiveToolKey(tool.key)}
              >
                {tool.label}
              </button>
            ))}
          </div>

          <div className="photo-mode-note" aria-live="polite">
            <span>当前模式</span>
            <h4>{activeTool.title}</h4>
            <p>{activeTool.description}</p>
            <small>{activeTool.hint}</small>
          </div>
        </section>

        <Card className="photo-editor-card" bodyStyle={{ padding: 0 }}>
          {serviceStatus === 'disconnected' ? (
            <Alert
              message="照片编辑服务未运行"
              description={
                <div>
                  <p>当前未能连接到照片编辑服务。请确认服务已启动，并监听地址：{powerpaintUrl}。</p>
                  <p>如果尚未启动，请在终端运行以下命令：</p>
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
            <div className="photo-iframe-shell">
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
                  <Text style={{ marginTop: '16px' }}>正在加载照片编辑器...</Text>
                </div>
              )}

              <iframe
                id="powerpaint-iframe"
                src={powerpaintUrl}
                style={{
                  width: '100%',
                  height: 'calc(100% + 170px)',
                  border: '1px solid #d9d9d9',
                  borderRadius: '6px',
                  transform: 'translateY(-170px)'
                }}
                onLoad={handleIframeLoad}
                onError={handleIframeError}
                title="照片编辑器"
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
                  已就绪
                </div>
              )}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
};

export default PhotoEditor;
