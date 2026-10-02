import React, { useMemo, useState } from 'react';
import { Button, Dropdown, Drawer, Modal, Input, Tooltip, message } from 'antd';
import { useNavigate, useLocation } from 'react-router-dom';
import {
  PlusOutlined,
  HistoryOutlined,
  DeleteOutlined,
  MoreOutlined,
  ExclamationCircleOutlined,
  CompassOutlined,
  PictureOutlined,
  DatabaseOutlined,
  SettingOutlined,
  UserOutlined,
  SearchOutlined
} from '@ant-design/icons';
import { useChatHistory, ChatHistoryItem } from '../hooks/useChatHistory';

const { confirm } = Modal;

interface SidebarProps {
  currentChatId?: string;
  onChatSelect?: (chat: ChatHistoryItem) => void | Promise<void>;
  onChatDeleted?: (chatId: string) => void;
  onHistoryCleared?: () => void;
  onNewChat?: () => void;
}

const Sidebar: React.FC<SidebarProps> = ({ currentChatId, onChatSelect, onChatDeleted, onHistoryCleared, onNewChat }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const { history, deleteChat, clearHistory, hasMore, loadMore, isLoading } = useChatHistory();
  const [historyQuery, setHistoryQuery] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);

  const filteredHistory = useMemo(() => {
    const query = historyQuery.trim().toLowerCase();
    if (!query) return history;

    return history.filter((item) => {
      const title = item.title?.toLowerCase() || '';
      const content = item.messages
        .map((message) => `${message.content || ''} ${message.displayContent || ''}`)
        .join(' ')
        .toLowerCase();
      const preview = item.preview?.toLowerCase() || '';
      return title.includes(query) || preview.includes(query) || content.includes(query);
    });
  }, [history, historyQuery]);

  const primaryMenuItems = [
    {
      key: 'new-chat',
      icon: <PlusOutlined />,
      label: '新旅程'
    },
    {
      key: '/photo-editor',
      icon: <PictureOutlined />,
      label: '旅行照片',
    },
  ];

  const secondaryMenuItems = [
    {
      key: '/knowledge',
      icon: <DatabaseOutlined />,
      label: '知识库',
    },
    {
      key: '/profile',
      icon: <UserOutlined />,
      label: '用户画像',
    },
    {
      key: '/settings',
      icon: <SettingOutlined />,
      label: '设置',
    },
  ];

  const handleChatClick = async (chatItem: ChatHistoryItem) => {
    setHistoryOpen(false);
    await onChatSelect?.(chatItem);
  };

  const handleDeleteChat = (e: React.MouseEvent, chatId: string) => {
    e.stopPropagation();
    confirm({
      title: '删除旅程',
      icon: <ExclamationCircleOutlined />,
      content: '确定要删除这个旅程记录吗？',
      centered: true,
      okText: '删除',
      okType: 'danger',
      cancelText: '取消',
      async onOk() {
        try {
          await deleteChat(chatId);
          onChatDeleted?.(chatId);
        } catch (error) {
          message.error(error instanceof Error ? error.message : '删除旅程失败');
          throw error;
        }
      },
    });
  };

  const handleClearHistory = () => {
    confirm({
      title: '删除全部旅程',
      icon: <ExclamationCircleOutlined />,
      content: '将永久删除当前设备下的全部旅程、消息、正式版本和草稿。此操作不可恢复。',
      centered: true,
      okText: '永久删除全部旅程',
      okType: 'danger',
      cancelText: '取消',
      async onOk() {
        try {
          await clearHistory();
          onHistoryCleared?.();
        } catch (error) {
          message.error(error instanceof Error ? error.message : '删除全部旅程失败');
          throw error;
        }
      },
    });
  };

  const getChatDropdownItems = (chatId: string) => [
    {
      key: 'delete',
      label: '删除旅程',
      icon: <DeleteOutlined />,
      danger: true,
      onClick: (e: any) => handleDeleteChat(e.domEvent, chatId)
    }
  ];

  const formatDate = (date: Date): string => {
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

    if (diffDays === 0) {
      return '今天';
    } else if (diffDays === 1) {
      return '昨天';
    } else if (diffDays < 7) {
      return `${diffDays}天前`;
    } else {
      return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
    }
  };

  const railItem = (item: { key: string; icon: React.ReactNode; label: string }) => (
    <Tooltip title={item.label} placement="right" key={item.key}>
      <button type="button" className={`c1-nav-item ${location.pathname === item.key ? 'is-active' : ''}`}
        aria-label={item.label} aria-current={location.pathname === item.key ? 'page' : undefined}
        onClick={() => item.key === 'new-chat' ? onNewChat?.() : navigate(item.key)}>
        {item.icon}<span>{item.label}</span>
      </button>
    </Tooltip>
  );
  return <>
    <aside className="c1-rail" aria-label="主导航">
      <button className="c1-brand" aria-label="返回旅行规划" onClick={() => navigate('/')}><CompassOutlined /></button>
      <nav className="c1-nav-main">
        {railItem(primaryMenuItems[0])}
        <button className={`c1-nav-item ${historyOpen ? 'is-active' : ''}`} onClick={() => setHistoryOpen(true)} aria-label="旅程管理">
          <HistoryOutlined /><span>旅程管理</span>
        </button>
        {railItem(primaryMenuItems[1])}
      </nav>
      <nav className="c1-nav-bottom">{secondaryMenuItems.map(railItem)}</nav>
    </aside>
    <Drawer title="旅程管理" placement="left" width={380} open={historyOpen} onClose={() => setHistoryOpen(false)} rootClassName="c1-history-drawer">
      <p className="c1-muted">收藏走过的地方，也安排下一次出发。</p>
      <Button type="primary" icon={<PlusOutlined />} block onClick={() => { onNewChat?.(); setHistoryOpen(false); }}>新旅程</Button>
      <Input allowClear prefix={<SearchOutlined />} placeholder="搜索行程" aria-label="搜索行程" value={historyQuery} onChange={event => setHistoryQuery(event.target.value)} className="c1-history-search" />
      <div className="c1-history-heading"><span>旅程历史</span>{history.length > 0 && <Button type="text" size="small" onClick={handleClearHistory}>清空</Button>}</div>
      <div className="c1-history-list">
        {filteredHistory.length === 0 && <p className="c1-empty">{isLoading ? '正在加载旅程…' : historyQuery ? '没有匹配的旅程' : '还没有旅程记录'}</p>}
        {filteredHistory.map(chat => <div key={chat.id} className={`c1-history-row ${chat.id === currentChatId ? 'is-active' : ''}`}>
          <button className="c1-history-select" onClick={() => void handleChatClick(chat)} aria-current={chat.id === currentChatId ? 'true' : undefined}>
            <strong>{chat.title}</strong><span>{formatDate(chat.contentUpdatedAt)}</span>
          </button>
          <Dropdown menu={{ items: getChatDropdownItems(chat.id) }} trigger={['click']}>
            <Button type="text" icon={<MoreOutlined />} aria-label={`管理旅程：${chat.title}`} />
          </Dropdown>
        </div>)}
      </div>
      {hasMore && <Button block type="text" loading={isLoading} onClick={() => void loadMore()}>加载更多</Button>}
    </Drawer>
  </>;
};
export default Sidebar;
