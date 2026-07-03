import React from 'react';
import { Layout, Menu, Button, Dropdown, Modal } from 'antd';
import { useNavigate, useLocation } from 'react-router-dom';
import {
  PlusOutlined,
  HistoryOutlined,
  DeleteOutlined,
  MoreOutlined,
  ExclamationCircleOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  PictureOutlined
} from '@ant-design/icons';
import { useChatHistory, ChatHistoryItem } from '../hooks/useChatHistory';

const { Sider } = Layout;
const { confirm } = Modal;

interface SidebarProps {
  collapsed: boolean;
  currentChatId?: string;
  onChatSelect?: (chatId: string, messages: ChatHistoryItem['messages']) => void;
  onChatDeleted?: (chatId: string) => void;
  onHistoryCleared?: () => void;
  onNewChat?: () => void;
  onToggleCollapse?: () => void;
}

const Sidebar: React.FC<SidebarProps> = ({ collapsed, currentChatId, onChatSelect, onChatDeleted, onHistoryCleared, onNewChat, onToggleCollapse }) => {
  const navigate = useNavigate();
  const location = useLocation();
  const { history, deleteChat, clearHistory } = useChatHistory();

  const menuItems = [
    {
      key: 'new-chat',
      icon: <PlusOutlined />,
      label: '新旅程',
      style: {
        marginBottom: '8px',
        background: '#f8fafc',
        border: '1px solid #e2e8f0',
        borderRadius: '8px'
      }
    },
    {
      key: '/photo-editor',
      icon: <PictureOutlined />,
      label: '旅行照片',
    },
    // 隐藏的菜单项：
    // {
    //   key: '/tools',
    //   icon: <ToolOutlined />,
    //   label: '旅游工具',
    // },
    // {
    //   key: '/mcp-servers',
    //   icon: <CloudServerOutlined />,
    //   label: '旅游服务',
    // },
    // {
    //   key: '/config',
    //   icon: <SettingOutlined />,
    //   label: '系统配置',
    // },
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
    onChatSelect?.(chatItem.id, chatItem.messages);
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
      onOk() {
        deleteChat(chatId);
        onChatDeleted?.(chatId);
      },
    });
  };

  const handleClearHistory = () => {
    confirm({
      title: '清空历史',
      icon: <ExclamationCircleOutlined />,
      content: '确定要清空所有旅程历史吗？此操作不可恢复。',
      centered: true,
      okText: '清空',
      okType: 'danger',
      cancelText: '取消',
      onOk() {
        clearHistory();
        onHistoryCleared?.();
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
      trigger={null}
      collapsible
      collapsed={collapsed}
      width={240}
      style={{
        background: '#ffffff',
        borderRight: '1px solid #f1f5f9',
        boxShadow: '2px 0 8px rgba(0, 0, 0, 0.04)',
        display: 'flex',
        flexDirection: 'column'
      }}
    >
      {/* 顶部标题区域 */}
      <div style={{
        padding: collapsed ? '16px 8px' : '20px 16px',
        textAlign: collapsed ? 'center' : 'left',
        borderBottom: '1px solid #f1f5f9',
        flexShrink: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between'
      }}>
        {!collapsed ? (
          <div>
            <div style={{
              color: '#1f2937',
              fontSize: '16px',
              fontWeight: 600,
              marginBottom: '4px'
            }}>
              ✈️ SuperTravelAgent
            </div>
            <div style={{
              color: '#6b7280',
              fontSize: '12px'
            }}>
              智能旅游规划
            </div>
          </div>
        ) : (
          <div style={{
            color: '#1f2937',
            fontSize: '20px'
          }}>
            ✈️
          </div>
        )}

        {/* 折叠/展开按钮 */}
        <Button
          type="text"
          icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
          onClick={onToggleCollapse}
          style={{
            fontSize: '14px',
            width: 32,
            height: 32,
            color: '#666666',
            borderRadius: '6px',
            flexShrink: 0
          }}
        />
      </div>

      {/* 菜单区域 */}
      <div style={{ padding: '0 12px', marginBottom: '16px', flexShrink: 0 }}>
        <Menu
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={handleMenuClick}
          style={{
            background: 'transparent',
            border: 'none',
            fontSize: '14px'
          }}
          className="sidebar-menu-light"
        />
      </div>

      {/* 对话历史区域 */}
      {!collapsed && (
        <div style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          padding: '0 12px'
        }}>
          {/* 历史标题和操作 */}
          <div style={{
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
              color: '#6b7280',
              fontSize: '13px',
              fontWeight: 500
            }}>
              <HistoryOutlined style={{ fontSize: '14px' }} />
              对话历史
            </div>
            {history.length > 0 && (
              <Button
                type="text"
                size="small"
                icon={<DeleteOutlined />}
                onClick={handleClearHistory}
                style={{
                  color: '#9ca3af',
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
          <div style={{
            flex: 1,
            overflowY: 'auto',
            overflowX: 'hidden'
          }}>
            {history.length === 0 ? (
              <div style={{
                textAlign: 'center',
                color: '#9ca3af',
                fontSize: '13px',
                padding: '20px 0'
              }}>
                暂无对话历史
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                {history.map((chatItem) => {
                  const isActive = chatItem.id === currentChatId;
                  return (
                    <div
                      key={chatItem.id}
                      onClick={() => handleChatClick(chatItem)}
                      className="chat-history-item"
                      style={{
                        padding: '8px 12px',
                        borderRadius: '8px',
                        cursor: 'pointer',
                        background: isActive ? '#eef2ff' : 'transparent',
                        border: isActive ? '1px solid #c7d2fe' : '1px solid transparent',
                        transition: 'all 0.2s ease',
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'flex-start',
                        gap: '8px'
                      }}
                      onMouseEnter={(e) => {
                        if (isActive) {
                          e.currentTarget.style.background = '#e0e7ff';
                          e.currentTarget.style.borderColor = '#a5b4fc';
                        } else {
                          e.currentTarget.style.background = '#f8fafc';
                          e.currentTarget.style.borderColor = '#e2e8f0';
                        }
                      }}
                      onMouseLeave={(e) => {
                        if (isActive) {
                          e.currentTarget.style.background = '#eef2ff';
                          e.currentTarget.style.borderColor = '#c7d2fe';
                        } else {
                          e.currentTarget.style.background = 'transparent';
                          e.currentTarget.style.borderColor = 'transparent';
                        }
                      }}
                    >
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{
                          color: isActive ? '#3730a3' : '#1f2937',
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
                          color: isActive ? '#6366f1' : '#9ca3af',
                          fontSize: '11px'
                        }}>
                          {formatDate(chatItem.updatedAt)}
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
                            color: isActive ? '#6366f1' : '#9ca3af',
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
              </div>
            )}
          </div>
        </div>
      )}

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
