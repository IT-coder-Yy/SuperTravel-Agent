import React, { lazy, Suspense, useState, useEffect, useRef } from 'react';
import { Layout, ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import { BrowserRouter as Router, Routes, Route, useLocation, useNavigate } from 'react-router-dom';
import TravelPlannerPage, { type TravelPlannerPageRef } from './features/travel/TravelPlannerPage';
import Sidebar from './components/Sidebar';
import { SystemProvider } from './context/SystemContext';
import { ChatHistoryItem, useChatHistory } from './hooks/useChatHistory';
import type { TripEditDraftPayload } from './features/travel/state/tripEditDraft';
import { travelTheme } from './features/travel/theme/travelTheme';
import { apiClient } from './services/apiClient';
import './App.css';
import './styles/visual-system.css';
import './styles/c1-system.css';

const { Content } = Layout;
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
  const [currentChatId, setCurrentChatId] = useState<string>('');
  const [loadedMessages, setLoadedMessages] = useState<ChatHistoryItem['messages'] | null>(null);
  const [loadedTripPlan, setLoadedTripPlan] = useState<Record<string, unknown> | null>(null);
  const [loadedTripDocument, setLoadedTripDocument] = useState<Record<string, unknown> | null>(null);
  const [loadedTripWorkspace, setLoadedTripWorkspace] = useState<Record<string, unknown> | null>(null);
  const [loadedAgentTimeline, setLoadedAgentTimeline] = useState<Record<string, unknown> | null>(null);
  const [loadedTripDraft, setLoadedTripDraft] = useState<TripEditDraftPayload | null>(null);
  const [loadedHasPreviousFormalSnapshot, setLoadedHasPreviousFormalSnapshot] = useState(false);
  const { history, getChat } = useChatHistory();
  const hasAutoRestoredRef = useRef(false);
  const restoreRequestRef = useRef(0);
  const chatInterfaceRef = useRef<TravelPlannerPageRef>(null);
  const navigate = useNavigate();
  const location = useLocation();

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
      setLoadedAgentTimeline(detail.agentTimeline || null);
      setLoadedTripDraft(detail.tripDraft || null);
      setLoadedHasPreviousFormalSnapshot(Boolean(detail.hasPreviousFormalSnapshot));

      const restoreCompletedBackgroundRun = async () => {
        try {
          let active = (await apiClient.getActivePlanningRun()).active_run;
          // 已完成历史没有后台任务，不要迟到地再次加载并覆盖当前交互。
          if (!active || active.trip_id !== detail.id) return;
          while (active && active.trip_id === detail.id && requestNumber === restoreRequestRef.current) {
            await new Promise((resolve) => window.setTimeout(resolve, 1000));
            active = (await apiClient.getActivePlanningRun()).active_run;
          }
          if (requestNumber !== restoreRequestRef.current) return;
          const completedDetail = await getChat(detail.id);
          if (!completedDetail || requestNumber !== restoreRequestRef.current) return;
          setLoadedMessages([...completedDetail.messages]);
          setLoadedTripPlan(completedDetail.tripPlan || null);
          setLoadedTripDocument(completedDetail.tripDocument || null);
          setLoadedTripWorkspace(completedDetail.tripWorkspace || null);
          setLoadedAgentTimeline(completedDetail.agentTimeline || null);
          setLoadedTripDraft(completedDetail.tripDraft || null);
          setLoadedHasPreviousFormalSnapshot(Boolean(completedDetail.hasPreviousFormalSnapshot));
        } catch (error) {
          console.warn('后台规划状态恢复失败，可通过历史记录重新打开行程:', error);
        }
      };
      void restoreCompletedBackgroundRun();
    });
  }, [history]);

  // 处理新对话
  const handleNewChat = (prefilledPrompt?: string) => {
    console.log('App.tsx - handleNewChat被调用');

    // 首先导航到首页，确保ChatInterface组件已渲染
    navigate('/');

    // 重置状态
    hasAutoRestoredRef.current = true;
    restoreRequestRef.current += 1;
    setCurrentChatId('');
    if (chatInterfaceRef.current) {
      chatInterfaceRef.current.startNewChat(prefilledPrompt);
      setLoadedMessages(null);
      setLoadedTripPlan(null);
      setLoadedTripDocument(null);
      setLoadedTripWorkspace(null);
      setLoadedAgentTimeline(null);
      setLoadedTripDraft(null);
      setLoadedHasPreviousFormalSnapshot(false);
      console.log('App.tsx - startNewChat直接调用完成');
      return;
    }

    setLoadedMessages([]); // ChatInterface尚未挂载时，用空数组触发挂载后的清空逻辑
    setLoadedTripDraft(null);
    setLoadedHasPreviousFormalSnapshot(false);

    // 使用setTimeout确保ChatInterface组件已经渲染完成
    setTimeout(() => {
      console.log('App.tsx - 准备调用chatInterfaceRef.current?.startNewChat()');
      if (chatInterfaceRef.current) {
        chatInterfaceRef.current.startNewChat(prefilledPrompt);
        console.log('App.tsx - startNewChat调用完成');
      } else {
        console.error('App.tsx - chatInterfaceRef.current仍为null，重试中...');
        // 如果还是null，再等一会儿重试
        setTimeout(() => {
          if (chatInterfaceRef.current) {
            chatInterfaceRef.current.startNewChat(prefilledPrompt);
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

    hasAutoRestoredRef.current = true;
    const requestNumber = ++restoreRequestRef.current;
    const detail = await getChat(chat.id);
    if (!detail || requestNumber !== restoreRequestRef.current) return;
    setCurrentChatId(detail.id);
    setLoadedTripPlan(detail.tripPlan || null);
    setLoadedTripDocument(detail.tripDocument || null);
    setLoadedTripWorkspace(detail.tripWorkspace || null);
    setLoadedAgentTimeline(detail.agentTimeline || null);
    setLoadedTripDraft(detail.tripDraft || null);
    setLoadedHasPreviousFormalSnapshot(Boolean(detail.hasPreviousFormalSnapshot));
    setLoadedMessages([...detail.messages]);
  };

  const handleChatDeleted = (chatId: string) => {
    if (currentChatId !== chatId) return;
    setCurrentChatId('');
    setLoadedMessages([]);
    setLoadedTripPlan(null);
    setLoadedTripDocument(null);
    setLoadedTripWorkspace(null);
    setLoadedAgentTimeline(null);
    setLoadedTripDraft(null);
    setLoadedHasPreviousFormalSnapshot(false);
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
    setLoadedAgentTimeline(null);
    setLoadedTripDraft(null);
    setLoadedHasPreviousFormalSnapshot(false);
    setTimeout(() => {
      chatInterfaceRef.current?.startNewChat();
    }, 0);
  };

  if (location.pathname.startsWith('/share/')) {
    return (
      <ConfigProvider theme={travelTheme} locale={zhCN}>
        <Suspense fallback={<RouteLoading />}>
          <Routes><Route path="/share/:token" element={<SharedTripPage />} /></Routes>
        </Suspense>
      </ConfigProvider>
    );
  }

  return (
    <ConfigProvider theme={travelTheme} locale={zhCN}>
      <SystemProvider>
        <Layout className="c1-app" style={{
          height: '100dvh',
          overflow: 'hidden',
          background: 'var(--travel-gradient-page)'
        }}>
          <Layout style={{ flex: 1, overflow: 'hidden' }}>
            <Sidebar
              currentChatId={currentChatId}
              onNewChat={handleNewChat}
              onChatSelect={handleChatSelect}
              onChatDeleted={handleChatDeleted}
              onHistoryCleared={handleHistoryCleared}
            />

            <Layout style={{
              flex: 1,
              overflow: 'hidden',
              background: 'var(--travel-gradient-page)'
            }}>
              <header className="c1-topbar"><a href="/" onClick={event => { event.preventDefault(); navigate('/'); }}>SuperTravel<span>Agent</span></a><span className="c1-topbar-page">{location.pathname === '/' ? '旅行规划' : location.pathname === '/knowledge' ? '知识库' : location.pathname === '/profile' ? '用户画像' : location.pathname === '/photo-editor' ? '旅行照片' : '设置'}</span><button onClick={() => handleNewChat()}>＋ 新旅程</button></header>
              <Content
                className={location.pathname === '/' ? 'c1-planner-content' : undefined}
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
                        <TravelPlannerPage
                          ref={chatInterfaceRef}
                          currentChatId={currentChatId}
                          loadedMessages={loadedMessages}
                          loadedTripPlan={loadedTripPlan}
                          loadedTripDocument={loadedTripDocument}
                          loadedTripWorkspace={loadedTripWorkspace}
                          loadedAgentTimeline={loadedAgentTimeline}
                          loadedTripDraft={loadedTripDraft}
                          loadedHasPreviousFormalSnapshot={loadedHasPreviousFormalSnapshot}
                          onRequestNewTrip={handleNewChat}
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
