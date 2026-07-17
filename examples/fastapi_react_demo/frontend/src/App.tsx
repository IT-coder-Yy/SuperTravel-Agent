import React, { lazy, Suspense, useState, useEffect, useRef } from 'react';
import { Layout, ConfigProvider, theme } from 'antd';
import { BrowserRouter as Router, Routes, Route, useLocation, useNavigate } from 'react-router-dom';
import ChatInterface from './components/ChatInterface';
import Sidebar from './components/Sidebar';
import { SystemProvider } from './context/SystemContext';
import { ChatHistoryItem, useChatHistory } from './hooks/useChatHistory';
import './App.css';

const { Content } = Layout;
const MOBILE_LAYOUT_QUERY = '(max-width: 768px)';
const PhotoEditor = lazy(() => import('./components/PhotoEditor'));
const SettingsPage = lazy(() => import('./components/SettingsPage'));
const KnowledgeBase = lazy(() => import('./components/KnowledgeBase'));
const UserProfile = lazy(() => import('./components/UserProfile'));
const SharedTripPage = lazy(() => import('./components/SharedTripPage'));

const RouteLoading: React.FC = () => (
  <div className="route-loading" role="status" aria-label="页面加载中">
    <span />
    <span />
    <span />
  </div>
);

const AppContent: React.FC = () => {
  const [darkMode] = useState(false);
  const [collapsed, setCollapsed] = useState(
    () => typeof window !== 'undefined' && window.matchMedia(MOBILE_LAYOUT_QUERY).matches
  );
  const [currentChatId, setCurrentChatId] = useState<string>('');
  const [loadedMessages, setLoadedMessages] = useState<ChatHistoryItem['messages'] | null>(null);
  const [loadedTripPlan, setLoadedTripPlan] = useState<Record<string, unknown> | null>(null);
  const [loadedTripDocument, setLoadedTripDocument] = useState<Record<string, unknown> | null>(null);
  const [loadedTripWorkspace, setLoadedTripWorkspace] = useState<Record<string, unknown> | null>(null);
  const { history, getChat } = useChatHistory();
  const hasAutoRestoredRef = useRef(false);
  const restoreRequestRef = useRef(0);
  const chatInterfaceRef = useRef<{
    startNewChat: () => void;
    loadChat: (
      messages: ChatHistoryItem['messages'],
      tripPlan?: Record<string, unknown> | null,
      tripWorkspace?: Record<string, unknown> | null,
      tripDocument?: Record<string, unknown> | null,
    ) => void;
  }>(null);
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    const mobileLayout = window.matchMedia(MOBILE_LAYOUT_QUERY);
    const collapseOnMobile = (event: MediaQueryListEvent | MediaQueryList) => {
      if (event.matches) setCollapsed(true);
    };
    collapseOnMobile(mobileLayout);
    mobileLayout.addEventListener('change', collapseOnMobile);
    return () => mobileLayout.removeEventListener('change', collapseOnMobile);
  }, []);

  // 刷新后自动恢复最近会话
  useEffect(() => {
    if (hasAutoRestoredRef.current) return;
    if (!history || history.length === 0) return;

    const latestChat = history[0];
    hasAutoRestoredRef.current = true;
    const requestNumber = ++restoreRequestRef.current;
    void getChat(latestChat.id).then((detail) => {
      if (!detail || requestNumber !== restoreRequestRef.current) return;
      setCurrentChatId(detail.id);
      setLoadedMessages([...detail.messages]);
      setLoadedTripPlan(detail.tripPlan || null);
      setLoadedTripDocument(detail.tripDocument || null);
      setLoadedTripWorkspace(detail.tripWorkspace || null);
    });
  }, [history]);

  // 处理新对话
  const handleNewChat = () => {
    console.log('App.tsx - handleNewChat被调用');

    // 首先导航到首页，确保ChatInterface组件已渲染
    navigate('/');

    // 重置状态
    setCurrentChatId('');
    if (chatInterfaceRef.current) {
      chatInterfaceRef.current.startNewChat();
      setLoadedMessages(null);
      setLoadedTripPlan(null);
      setLoadedTripDocument(null);
      setLoadedTripWorkspace(null);
      console.log('App.tsx - startNewChat直接调用完成');
      return;
    }

    setLoadedMessages([]); // ChatInterface尚未挂载时，用空数组触发挂载后的清空逻辑

    // 使用setTimeout确保ChatInterface组件已经渲染完成
    setTimeout(() => {
      console.log('App.tsx - 准备调用chatInterfaceRef.current?.startNewChat()');
      if (chatInterfaceRef.current) {
        chatInterfaceRef.current.startNewChat();
        console.log('App.tsx - startNewChat调用完成');
      } else {
        console.error('App.tsx - chatInterfaceRef.current仍为null，重试中...');
        // 如果还是null，再等一会儿重试
        setTimeout(() => {
          if (chatInterfaceRef.current) {
            chatInterfaceRef.current.startNewChat();
            console.log('App.tsx - startNewChat重试成功');
          } else {
            console.error('App.tsx - startNewChat重试失败，chatInterfaceRef仍为null');
          }
        }, 100);
      }
    }, 50);
  };

  // 处理选择历史对话
  const handleChatSelect = async (chat: ChatHistoryItem) => {
    console.log('App.tsx - handleChatSelect被调用，chatId:', chat.id, '消息数量:', chat.messages.length);

    // 导航到首页
    navigate('/');

    const requestNumber = ++restoreRequestRef.current;
    const detail = await getChat(chat.id);
    if (!detail || requestNumber !== restoreRequestRef.current) return;
    setCurrentChatId(detail.id);
    setLoadedTripPlan(detail.tripPlan || null);
    setLoadedTripDocument(detail.tripDocument || null);
    setLoadedTripWorkspace(detail.tripWorkspace || null);
    setLoadedMessages([...detail.messages]);
  };

  const handleChatDeleted = (chatId: string) => {
    if (currentChatId !== chatId) return;
    setCurrentChatId('');
    setLoadedMessages([]);
    setLoadedTripPlan(null);
    setLoadedTripDocument(null);
    setLoadedTripWorkspace(null);
    setTimeout(() => {
      chatInterfaceRef.current?.startNewChat();
    }, 0);
  };

  const handleHistoryCleared = () => {
    setCurrentChatId('');
    setLoadedMessages([]);
    setLoadedTripPlan(null);
    setLoadedTripDocument(null);
    setLoadedTripWorkspace(null);
    setTimeout(() => {
      chatInterfaceRef.current?.startNewChat();
    }, 0);
  };

  // 主题配置 - 旅行规划产品风格
  const themeConfig = {
    algorithm: darkMode ? theme.darkAlgorithm : theme.defaultAlgorithm,
    token: {
      colorPrimary: '#6269d8',
      colorSuccess: '#2b9a68',
      colorWarning: '#c98624',
      colorError: '#d2473d',
      borderRadius: 12,
      fontFamily: 'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
      colorBgLayout: darkMode ? '#101820' : '#f3efff',
      colorBgContainer: darkMode ? '#1f2937' : '#fbfdff',
      colorBgElevated: darkMode ? '#374151' : '#ffffff',
      colorText: darkMode ? '#f9fafb' : '#202f3f',
      colorTextSecondary: darkMode ? '#d1d5db' : '#5f6f80',
      colorBorder: darkMode ? '#4b5563' : '#d2dbe7',
      boxShadow: '0 8px 14px rgba(28, 52, 78, 0.08)',
    },
    components: {
      Button: {
        controlHeight: 36,
        borderRadius: 12,
        primaryShadow: 'none',
      },
      Card: {
        borderRadiusLG: 16,
        boxShadowTertiary: '0 8px 14px rgba(28, 52, 78, 0.08)',
      },
      Input: {
        borderRadius: 12,
        activeShadow: '0 0 0 3px rgba(48, 93, 132, 0.16)',
      },
      InputNumber: {
        borderRadius: 12,
      },
      Menu: {
        itemBorderRadius: 12,
        itemSelectedBg: '#dfeaf4',
        itemSelectedColor: '#234a68',
      },
      Tabs: {
        inkBarColor: '#2e5f8a',
        itemSelectedColor: '#234a68',
      },
    },
  };

  if (location.pathname.startsWith('/share/')) {
    return (
      <ConfigProvider theme={themeConfig}>
        <Suspense fallback={<RouteLoading />}>
          <Routes><Route path="/share/:token" element={<SharedTripPage />} /></Routes>
        </Suspense>
      </ConfigProvider>
    );
  }

  return (
    <ConfigProvider theme={themeConfig}>
      <SystemProvider>
        <Layout style={{
          height: '100vh',
          overflow: 'hidden',
          background: 'var(--travel-gradient-page)'
        }}>
          <Layout style={{ flex: 1, overflow: 'hidden' }}>
            <Sidebar
              collapsed={collapsed}
              currentChatId={currentChatId}
              onNewChat={handleNewChat}
              onChatSelect={handleChatSelect}
              onChatDeleted={handleChatDeleted}
              onHistoryCleared={handleHistoryCleared}
              onToggleCollapse={() => setCollapsed(!collapsed)}
            />

            <Layout style={{
              flex: 1,
              overflow: 'hidden',
              background: 'var(--travel-gradient-page)'
            }}>
              <Content
                style={{
                  margin: 0,
                  height: '100%',
                  background: 'var(--travel-gradient-page)',
                  overflow: 'auto',
                  display: 'flex',
                  flexDirection: 'column'
                }}
              >
                <Suspense fallback={<RouteLoading />}>
                  <Routes>
                    <Route
                      path="/"
                      element={
                        <ChatInterface
                          ref={chatInterfaceRef}
                          currentChatId={currentChatId}
                          loadedMessages={loadedMessages}
                          loadedTripPlan={loadedTripPlan}
                          loadedTripDocument={loadedTripDocument}
                          loadedTripWorkspace={loadedTripWorkspace}
                        />
                      }
                    />
                    <Route path="/photo-editor" element={<PhotoEditor />} />
                    <Route path="/knowledge" element={<KnowledgeBase />} />
                    <Route path="/profile" element={<UserProfile />} />
                    <Route path="/settings" element={<SettingsPage />} />
                    <Route path="/config" element={<SettingsPage />} />
                    <Route path="/tools" element={<SettingsPage />} />
                    <Route path="/mcp-servers" element={<SettingsPage />} />
                  </Routes>
                </Suspense>
              </Content>
            </Layout>
          </Layout>
        </Layout>
      </SystemProvider>
    </ConfigProvider>
  );
};

const App: React.FC = () => {
  return (
    <Router>
      <AppContent />
    </Router>
  );
};

export default App; 
