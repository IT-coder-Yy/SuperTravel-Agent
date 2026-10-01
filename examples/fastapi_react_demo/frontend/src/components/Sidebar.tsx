import React, { useMemo, useState } from 'react';
import { Layout, Menu, Button, Dropdown, Modal, Input, message } from 'antd';
import { useNavigate, useLocation } from 'react-router-dom';
import {
  PlusOutlined,
  HistoryOutlined,
  DeleteOutlined,
  MoreOutlined,
  ExclamationCircleOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  PictureOutlined,
  DatabaseOutlined,
  SettingOutlined,
  UserOutlined,
  SearchOutlined
} from '@ant-design/icons';
import { useChatHistory, ChatHistoryItem } from '../hooks/useChatHistory';

const { Sider } = Layout;
const { confirm } = Modal;

interface SidebarProps {
  collapsed: boolean;
  currentChatId?: string;
  onChatSelect?: (chat: ChatHistoryItem) => void | Promise<void>;
  onChatDeleted?: (chatId: string) => void;
  onHistoryCleared?: () => void;
  onNewChat?: () => void;
  onToggleCollapse?: () => void;
}

const Sidebar: React.FC<SidebarProps> = ({ collapsed, currentChatId, onChatSelect, onChatDeleted, onHistoryCleared, onNewChat, onToggleCollapse }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const { history, deleteChat, clearHistory, hasMore, loadMore, isLoading } = useChatHistory();
  const [historyQuery, setHistoryQuery] = useState('');

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

  const handleMenuClick = (item: any) => {
    console.log('菜单项被点击:', item.key);
    console.log('item对象:', item);
    console.log('onNewChat函数存在:', !!onNewChat);

    if (item.key === 'new-chat') {
      console.log('新旅程被点击，调用onNewChat');
      console.log('onNewChat类型:', typeof onNewChat);
      if (onNewChat) {
        console.log('正在调用onNewChat...');
        onNewChat();
        console.log('onNewChat调用完成');
      } else {
        console.warn('onNewChat回调函数未定义');
      }
    } else {
      console.log('导航到:', item.key);
      navigate(item.key);
    }
  };

  const handleChatClick = (chatItem: ChatHistoryItem) => {
    onChatSelect?.(chatItem);
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

  return (
    <Sider
      className="travel-sidebar"
      trigger={null}
      collapsible
      collapsed={collapsed}
      collapsedWidth={64}
      width={250}
      style={{
        background: 'var(--travel-sidebar-bg)',
        borderRight: '1px solid var(--travel-border-soft)',
        display: 'flex',
        flexDirection: 'column'
      }}
    >
      {/* 顶部标题区域 */}
      <div className="travel-sidebar-brand" style={{
        padding: collapsed ? '16px 8px' : '20px 16px',
        textAlign: collapsed ? 'center' : 'left',
        borderBottom: '1px solid var(--travel-border-soft)',
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between'
      }}>
        {!collapsed ? (
          <div className="travel-sidebar-brand-copy">
            <div className="travel-sidebar-wordmark" style={{
              color: 'var(--travel-ink)',
              fontSize: '16px',
              fontWeight: 600,
              marginBottom: '4px'
            }}>
              SuperTravelAgent
            </div>
            <div className="travel-sidebar-tagline" style={{
              color: 'var(--travel-muted)',
              fontSize: '12px'
            }}>
              轻松规划每一段旅程
            </div>
          </div>
        ) : (
          <div className="travel-sidebar-monogram" style={{
            color: 'var(--travel-primary)',
            fontSize: '20px',
            fontWeight: 700
          }}>
            S
          </div>
        )}

        {/* 折叠/展开按钮 */}
        <Button
          className="travel-sidebar-toggle"
          type="text"
          icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
          onClick={onToggleCollapse}
          style={{
            fontSize: '14px',
            width: 32,
            height: 32,
            color: 'var(--travel-muted)',
            borderRadius: '6px',
            flexShrink: 0
          }}
        />
      </div>

      {/* 菜单区域 */}
      <div className="travel-sidebar-primary" style={{ padding: '0 12px', marginBottom: '16px', flexShrink: 0 }}>
        <Menu
          mode="inline"
          selectedKeys={[location.pathname]}
          items={primaryMenuItems}
          onClick={handleMenuClick}
          style={{
            background: 'transparent',
            border: 'none',
            fontSize: '14px'
          }}
          className="sidebar-menu-light"
        />
      </div>

      {!collapsed && (
        <div className="travel-sidebar-search" style={{ padding: '0 12px 14px', flexShrink: 0 }}>
          <Input
            allowClear
            size="middle"
            prefix={<SearchOutlined />}
            placeholder="搜索行程"
            value={historyQuery}
            onChange={(event) => setHistoryQuery(event.target.value)}
            className="journey-search-input"
          />
        </div>
      )}

      {/* 对话历史区域 */}
      {!collapsed && (
        <div className="travel-sidebar-history" style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          padding: '0 12px'
        }}>
          {/* 历史标题和操作 */}
          <div className="travel-sidebar-history-heading" style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: '12px',
            padding: '0 4px'
          }}>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              color: 'var(--travel-muted)',
              fontSize: '13px',
              fontWeight: 500
            }}>
              <HistoryOutlined style={{ fontSize: '14px' }} />
              旅程历史
            </div>
            {history.length > 0 && (
              <Button
                type="text"
                size="small"
                icon={<DeleteOutlined />}
                onClick={handleClearHistory}
                style={{
                  color: 'var(--travel-muted)',
                  fontSize: '12px',
                  height: '24px',
                  padding: '0 6px'
                }}
              >
                清空
              </Button>
            )}
          </div>

          {/* 历史对话列表 */}
          <div className="travel-sidebar-history-list" style={{
            flex: 1,
            overflowY: 'auto',
            overflowX: 'hidden'
          }}>
            {filteredHistory.length === 0 ? (
              <div style={{
                textAlign: 'center',
                color: 'var(--travel-muted)',
                fontSize: '13px',
                padding: '20px 0'
              }}>
                {historyQuery ? '没有匹配的旅程' : '还没有旅程记录'}
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                {filteredHistory.map((chatItem) => {
                  const isActive = chatItem.id === currentChatId;
                  return (
                    <div
                      key={chatItem.id}
                      onClick={() => handleChatClick(chatItem)}
                      className="chat-history-item"
                      style={{
                        padding: '8px 12px',
                        borderRadius: '12px',
                        cursor: 'pointer',
                        background: isActive ? 'linear-gradient(135deg, color-mix(in oklch, var(--travel-selected) 82%, white 18%), color-mix(in oklch, var(--travel-violet) 18%, white 82%))' : 'transparent',
                        border: isActive ? '1px solid var(--travel-primary-soft)' : '1px solid transparent',
                        transition: 'background 180ms ease, border-color 180ms ease, transform 180ms ease',
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'flex-start',
                        gap: '8px'
                      }}
                      onMouseEnter={(e) => {
                        if (isActive) {
                          e.currentTarget.style.background = 'linear-gradient(135deg, var(--travel-selected-strong), color-mix(in oklch, var(--travel-violet) 18%, white 82%))';
                          e.currentTarget.style.borderColor = 'var(--travel-primary-soft)';
                        } else {
                          e.currentTarget.style.background = 'color-mix(in oklch, var(--travel-hover) 78%, white 22%)';
                          e.currentTarget.style.borderColor = 'var(--travel-border)';
                        }
                      }}
                      onMouseLeave={(e) => {
                        if (isActive) {
                          e.currentTarget.style.background = 'linear-gradient(135deg, color-mix(in oklch, var(--travel-selected) 82%, white 18%), color-mix(in oklch, var(--travel-violet) 18%, white 82%))';
                          e.currentTarget.style.borderColor = 'var(--travel-primary-soft)';
                        } else {
                          e.currentTarget.style.background = 'transparent';
                          e.currentTarget.style.borderColor = 'transparent';
                        }
                      }}
                    >
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{
                          color: isActive ? 'var(--travel-primary-dark)' : 'var(--travel-ink)',
                          fontSize: '13px',
                          fontWeight: isActive ? 600 : 500,
                          marginBottom: '2px',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap'
                        }}>
                          {chatItem.title}
                        </div>
                        <div style={{
                          color: isActive ? 'var(--travel-primary)' : 'var(--travel-muted)',
                          fontSize: '11px'
                        }}>
                          {formatDate(chatItem.contentUpdatedAt)}
                        </div>
                      </div>
                      <Dropdown
                        menu={{ items: getChatDropdownItems(chatItem.id) }}
                        trigger={['click']}
                        placement="bottomRight"
                      >
                        <Button
                          type="text"
                          size="small"
                          icon={<MoreOutlined />}
                          onClick={(e) => e.stopPropagation()}
                          style={{
                            color: isActive ? 'var(--travel-primary)' : 'var(--travel-muted)',
                            width: '20px',
                            height: '20px',
                            padding: 0,
                            minWidth: 'auto',
                            opacity: 0,
                            transition: 'opacity 0.2s ease'
                          }}
                          className="chat-item-more-btn"
                        />
                      </Dropdown>
                    </div>
                  );
                })}
                {hasMore && !historyQuery && (
                  <Button
                    type="text"
                    size="small"
                    loading={isLoading}
                    onClick={() => void loadMore()}
                    style={{ color: 'var(--travel-primary)', marginTop: '6px' }}
                  >
                    加载更多
                  </Button>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      <div className="travel-sidebar-secondary" style={{
        flexShrink: 0,
        marginTop: 'auto',
        padding: collapsed ? '10px 8px 14px' : '12px',
        borderTop: '1px solid var(--travel-border-soft)',
        background: 'linear-gradient(180deg, color-mix(in oklch, var(--travel-surface) 42%, transparent), color-mix(in oklch, var(--travel-surface-muted) 58%, white 42%))'
      }}>
        <Menu
          mode="inline"
          selectedKeys={[location.pathname]}
          items={secondaryMenuItems}
          onClick={handleMenuClick}
          style={{
            background: 'transparent',
            border: 'none',
            fontSize: '14px'
          }}
          className="sidebar-menu-light sidebar-menu-secondary"
        />
      </div>

      {/* 隐藏底部用户信息区域 */}
      {false && !collapsed && (
        <div style={{
          flexShrink: 0,
          padding: '16px',
          borderTop: '1px solid #f1f5f9'
        }}>
          <div style={{
            padding: '12px',
            background: '#f8fafc',
            borderRadius: '8px',
            border: '1px solid #e2e8f0'
          }}>
            <div style={{
              color: '#1f2937',
              fontSize: '13px',
              fontWeight: 500,
              marginBottom: '2px'
            }}>
              体验用户
            </div>
            <div style={{
              color: '#6b7280',
              fontSize: '11px'
            }}>
              享受智能对话体验
            </div>
          </div>
        </div>
      )}
    </Sider>
  );
};

export default Sidebar; 
