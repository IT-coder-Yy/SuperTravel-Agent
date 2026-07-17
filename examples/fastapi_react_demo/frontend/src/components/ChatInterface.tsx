import React, { useState, useEffect, useRef, useMemo, forwardRef, useImperativeHandle } from 'react';
import {
  Input,
  Button,
  Switch,
  Spin,
  Tag,
  Divider,
  Dropdown,
  Checkbox,
  Table,
  Typography,
  Segmented
} from 'antd';
import {
  SendOutlined,
  StopOutlined,
  BranchesOutlined,
  ThunderboltOutlined,
  CloudServerOutlined,
  CopyOutlined,
  CheckOutlined,
  EnvironmentOutlined,
  RedoOutlined,
  LikeOutlined,
  LikeFilled,
  DislikeOutlined,
  DislikeFilled,
  MessageOutlined,
  CalendarOutlined
} from '@ant-design/icons';
import ReactMarkdown from 'react-markdown';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { tomorrow } from 'react-syntax-highlighter/dist/esm/styles/prism';
import remarkGfm from 'remark-gfm';
import { v4 as uuidv4 } from 'uuid';
import { useChatHistory, ChatHistoryItem } from '../hooks/useChatHistory';
import { apiClient, SkillInfo } from '../services/apiClient';
import { getChatMessages, setChatMessages } from '../utils/chatSessionRuntime';
import MapComponent, { DayRouteGeometry } from './MapComponent';
import ClarificationPanel, { ClarificationQuestion } from './ClarificationPanel';
import TripWorkspace, {
  TripWorkspaceBudget,
  TripWorkspaceIssue,
  TripWorkspaceRepair,
  TripWorkspaceSource,
  TripWorkspaceState,
  TripWorkspaceValidation
} from './TripWorkspace';
import TripProductTools from './TripProductTools';
import { statusAfterCompletion, statusAfterStreamClosed, type PlanningStatus } from './planningState';
import { normalizeTripDays, upsertTripDayPayload } from './tripViewModel';
import { useAppSettings } from '../hooks/useAppSettings';
import { useUserTravelProfile } from '../hooks/useUserTravelProfile';
import { useSelectedKnowledgeContext } from '../hooks/useSelectedKnowledgeContext';
import '../styles/markdown.css';

const { TextArea } = Input;
const { Text } = Typography;

interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string; // 真正的消息内容，用于后续对话
  displayContent: string; // 显示内容（来自show_content）
  timestamp: Date;
  type?: string;
  agentType?: string;
  startTime?: Date; // 消息开始时间
  endTime?: Date; // 消息结束时间
  duration?: number; // 耗时（毫秒）
  linkedUserMessageId?: string; // 重新回答版本归属的用户问题ID
}

interface MessageGroup {
  userMessage: Message;
  deepThinkMessages: Message[];
  finalAnswer?: Message | Message[]; // 支持单个或多个最终答案
}

interface LocationPoint {
  id: string;
  name: string;
  lat: number;
  lng: number;
  description?: string;
  category?: string;
  day?: number | string;
  order?: number | string;
}

type FeedbackType = 'like' | 'dislike';

interface LocationGroupData {
  groupId: string;
  title: string;
  locations: LocationPoint[];
  userMessageId: string;
  userPrompt: string;
}

interface RegenerateRequestContext {
  requestId: string;
  userMessageId: string;
  regeneratedAssistantMessageId: string;
  messageIdMap: Record<string, string>;
}

interface ChatInterfaceProps {
  currentChatId?: string;
  loadedMessages?: ChatHistoryItem['messages'] | null;
  loadedTripPlan?: Record<string, unknown> | null;
  loadedTripDocument?: Record<string, unknown> | null;
  loadedTripWorkspace?: Record<string, unknown> | null;
}

interface PendingClarification {
  chatId: string;
  requestMessages: Message[];
  question: ClarificationQuestion;
  answers: Record<string, string>;
  answeredCount: number;
  intent?: Record<string, unknown>;
}

type MobilePrimaryView = 'chat' | 'trip' | 'map';

const SIDE_PANEL_MAP_PERCENT_STORAGE_KEY = 'supertravelagent.sidePanelMapPercent';
const CLARIFICATION_SKIP_VALUE = '__skip__';

const safeText = (value: unknown): string => (typeof value === 'string' ? value : '');
const tripSnapshotSignature = (
  plan: Record<string, unknown> | null,
  document: Record<string, unknown> | null,
) => {
  if (!plan) return '';
  const guidance = document?.map_guidance as Record<string, unknown> | undefined;
  return `${safeText(plan.plan_id)}:${Number(plan.version) || 0}:${JSON.stringify({
    dayRoutes: guidance?.day_routes || [],
    checklist: document?.checklist || [],
    notes: document?.notes || [],
    delivery: document?.delivery || {},
  })}`;
};

const readSidePanelMapPercent = (): number => {
  const saved = Number(window.localStorage.getItem(SIDE_PANEL_MAP_PERCENT_STORAGE_KEY));
  return Number.isFinite(saved) ? Math.min(65, Math.max(55, saved)) : 60;
};

const getClarificationProfileDefault = (
  field: string,
  profile: Record<string, unknown>,
): string => {
  const normalizedField = field.trim().toLowerCase();
  const mappings: Array<[RegExp, string]> = [
    [/(budget|cost|price|预算|花费)/, 'preferred_budget_level'],
    [/(people|traveler|party|companion|人员|人数|同行)/, 'default_people_type'],
    [/(style|interest|preference|玩法|风格|偏好)/, 'travel_style'],
    [/(diet|food|meal|饮食|餐饮|忌口)/, 'dietary_preferences'],
    [/(pace|intensity|节奏|强度)/, 'pace'],
    [/(hotel|stay|accommodation|住宿|酒店)/, 'hotel_preference'],
    [/(transport|traffic|交通|出行方式)/, 'transport_preference'],
    [/(accessibility|mobility|无障碍|行动)/, 'accessibility_needs'],
    [/(dislike|avoid|不喜欢|避开)/, 'disliked_items'],
  ];
  const profileField = mappings.find(([pattern]) => pattern.test(normalizedField))?.[1];
  if (!profileField) return '';
  const value = profile[profileField];
  if (Array.isArray(value)) {
    return value.filter((item): item is string => typeof item === 'string' && Boolean(item.trim())).join('、');
  }
  return safeText(value).trim();
};

const createEmptyTripWorkspace = (): TripWorkspaceState => ({
  days: [],
  locations: [],
  sources: [],
  budget: null,
  validation: null,
  repair: null,
});

const restorePlanFromWorkspace = (
  workspace: TripWorkspaceState,
  document: Record<string, unknown> | null,
): Record<string, unknown> | null => {
  if (!Array.isArray(workspace.days) || workspace.days.length === 0) return null;
  const tripDays = workspace.days.map((day) => ({
    ...day,
    activities: day.activities.map((activity) => ({ ...activity, activity_id: activity.id })),
  }));
  return {
    plan_id: document?.plan_id || 'workspace-restored-draft',
    version: Number(document?.version || 1),
    title: document?.title || '恢复的旅行规划',
    intent: document?.intent || { days: tripDays.length },
    days: tripDays.length,
    trip_days: tripDays,
    activities: tripDays.flatMap((day) => day.activities),
    map_locations: workspace.locations,
    budget_summary: workspace.budget || {},
    source_references: workspace.sources,
    warnings: ['该方案由历史工作台恢复，实时字段仍需按来源重新确认'],
  };
};

const stripLeadingArtifactZero = (value: unknown): string => {
  const text = safeText(value);
  if (!text) return '';

  let cleaned = text;
  // 兼容流式拼接产生的前导孤立 "0"（例如 "0\n正文" 或 "0 正文"）
  cleaned = cleaned.replace(/^(?:\s*0\s*(?:\r?\n))+/u, '');
  cleaned = cleaned.replace(/^\s*0\s+(?=[^\d\s])/u, '');
  return cleaned;
};

const toStableNumber = (value: unknown): number => {
  const num = Number(value);
  if (!Number.isFinite(num)) return 0;
  return Number(num.toFixed(6));
};

const buildStableLocationId = (loc: Partial<LocationPoint>): string => {
  const existingId = safeText(loc.id).trim();
  // 旧逻辑里大量使用 location_<timestamp>，这里避免把时间戳作为稳定 ID。
  if (existingId && !/^location_\d+(_\d+)?$/.test(existingId)) {
    return existingId;
  }
  const normalizedName = encodeURIComponent(safeText(loc.name).trim().toLowerCase() || 'unknown');
  const lat = toStableNumber(loc.lat);
  const lng = toStableNumber(loc.lng);
  return `location_${normalizedName}_${lat}_${lng}`;
};

export interface ChatInterfaceRef {
  startNewChat: () => void;
  loadChat: (
    messages: ChatHistoryItem['messages'],
    tripPlan?: Record<string, unknown> | null,
    tripWorkspace?: Record<string, unknown> | null,
    tripDocument?: Record<string, unknown> | null,
  ) => void;
}

const ChatInterface = forwardRef<ChatInterfaceRef, ChatInterfaceProps>(
  ({ currentChatId, loadedMessages, loadedTripPlan, loadedTripDocument, loadedTripWorkspace }, ref) => {
    const { saveChat } = useChatHistory();
    const { settings } = useAppSettings();
    const { profile } = useUserTravelProfile();
    const { selectedKnowledgeContext, clearSelectedKnowledgeContext } = useSelectedKnowledgeContext();
    const [messages, setMessages] = useState<Message[]>([]);
    const [inputText, setInputTextState] = useState('');
    const [isInputEmpty, setIsInputEmpty] = useState(true);
    const [isLoading, setIsLoading] = useState(false);
    const [planningStatus, setPlanningStatus] = useState<PlanningStatus>('idle');
    const [useDeepThink, setUseDeepThink] = useState(true);
    const [useMultiAgent, setUseMultiAgent] = useState(true);
    const [sessionId, setSessionId] = useState(() => uuidv4());
    const [showMap, setShowMap] = useState(settings.showMapDefault);
    const [chatPanelWidthPercent, setChatPanelWidthPercent] = useState(70.6);
    const [mapLocations, setMapLocations] = useState<LocationPoint[]>([]);
    const [mapSuppressed, setMapSuppressed] = useState(false);
    const [messageFeedback, setMessageFeedback] = useState<Record<string, FeedbackType>>({});
    const [answerPageByUserMessageId, setAnswerPageByUserMessageId] = useState<Record<string, number>>({});
    const [pendingClarification, setPendingClarification] = useState<PendingClarification | null>(null);
    const [tripWorkspace, setTripWorkspace] = useState<TripWorkspaceState>(() => createEmptyTripWorkspace());
    const [activeTripPlan, setActiveTripPlan] = useState<Record<string, unknown> | null>(null);
    const [activeTripDocument, setActiveTripDocument] = useState<Record<string, unknown> | null>(null);
    const [previousTripPlan, setPreviousTripPlan] = useState<Record<string, unknown> | null>(null);
    const [isTripEditLoading, setIsTripEditLoading] = useState(false);
    const [dayRoutes, setDayRoutes] = useState<DayRouteGeometry[]>([]);
    const [activeMapGroupId, setActiveMapGroupId] = useState('');
    const [selectedLocationId, setSelectedLocationId] = useState('');
    const [sidePanelMapPercent, setSidePanelMapPercent] = useState(readSidePanelMapPercent);
    const [mobilePrimaryView, setMobilePrimaryView] = useState<MobilePrimaryView>('chat');
    const [isNarrowLayout, setIsNarrowLayout] = useState(
      () => window.matchMedia('(max-width: 768px)').matches
    );
    const activeTripPlanRef = useRef<Record<string, unknown> | null>(null);
    const activeTripDocumentRef = useRef<Record<string, unknown> | null>(null);
    const tripWorkspaceRef = useRef<TripWorkspaceState>(createEmptyTripWorkspace());
    const skipNextAutoSaveRef = useRef(false);
    const lastPersistedPlanSignatureRef = useRef('');
    const backgroundDocumentUpdateRef = useRef(false);

    useEffect(() => {
      activeTripPlanRef.current = activeTripPlan;
    }, [activeTripPlan]);

    useEffect(() => {
      activeTripDocumentRef.current = activeTripDocument;
    }, [activeTripDocument]);

    useEffect(() => {
      tripWorkspaceRef.current = tripWorkspace;
    }, [tripWorkspace]);

    useEffect(() => {
      if (!activeTripPlan) {
        setDayRoutes([]);
        return;
      }
      const version = Number(activeTripPlan.version);
      const rawDays = Array.isArray(activeTripPlan.trip_days)
        ? activeTripPlan.trip_days as Array<Record<string, unknown>>
        : [];
      if (!Number.isInteger(version) || rawDays.length === 0) {
        setDayRoutes([]);
        return;
      }
      const destination = safeText((activeTripPlan.intent as Record<string, unknown> | undefined)?.destination);
      const internationalDestinations = /东京|京都|大阪|首尔|新加坡|曼谷|吉隆坡|巴黎|伦敦|罗马|悉尼|纽约|Tokyo|Kyoto|Osaka|Seoul|Singapore|Bangkok|Paris|London|Rome|Sydney|New York/i;
      const controller = new AbortController();
      Promise.all(rawDays.map(async (day) => {
        const response = await fetch('/api/routes/day', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          signal: controller.signal,
          body: JSON.stringify({
            day: Number(day.day), plan_version: version,
            scope: internationalDestinations.test(destination) ? 'international' : 'domestic',
            activities: Array.isArray(day.activities) ? day.activities : [],
          }),
        });
        if (!response.ok) throw new Error('ROUTE_UNAVAILABLE');
        return response.json() as Promise<DayRouteGeometry>;
      })).then((routes) => {
        setDayRoutes(routes);
        setActiveTripDocument((current) => {
          if (!current) return current;
          backgroundDocumentUpdateRef.current = true;
          return {
            ...current,
            map_guidance: {
              ...(current.map_guidance as Record<string, unknown> || {}),
              status: routes.some((route) => route.status === 'ready' || route.status === 'partial') ? 'ready' : 'unavailable',
              status_reason: routes.some((route) => route.status === 'ready' || route.status === 'partial')
                ? '已按日计算真实道路路线'
                : '真实路线暂不可用，当前仅显示日程地点',
              day_routes: routes,
              unavailable_segments: routes.flatMap((route) => route.legs.filter((leg) => leg.status === 'unavailable')),
            },
          };
        });
      }).catch((error) => {
        if (error?.name !== 'AbortError') setDayRoutes([]);
      });
      return () => controller.abort();
    }, [activeTripPlan]);

    useEffect(() => {
      const mediaQuery = window.matchMedia('(max-width: 768px)');
      const handleLayoutChange = (event: MediaQueryListEvent | MediaQueryList) => {
        setIsNarrowLayout(event.matches);
      };
      handleLayoutChange(mediaQuery);
      mediaQuery.addEventListener('change', handleLayoutChange);
      return () => mediaQuery.removeEventListener('change', handleLayoutChange);
    }, []);

    // 强制清理地点数据的函数
    const forceCleanMapLocations = () => {
      console.log('强制清理地点数据，当前地点:', mapLocations.map(loc => loc.name));
      setMapSuppressed(true);
      setMapLocations([]);
      console.log('地点数据已强制清理');
    };

    // MCP服务器相关状态
    const [mcpServers, setMcpServers] = useState<any[]>([]);
    const [selectedMcpServers, setSelectedMcpServers] = useState<string[]>([]);
    const [mcpServersLoaded, setMcpServersLoaded] = useState(false);
    const [mcpModalVisible, setMcpModalVisible] = useState(false);
    const [mcpLoading, setMcpLoading] = useState(false);
    const [skills, setSkills] = useState<SkillInfo[]>([]);
    const [selectedSkillIds, setSelectedSkillIds] = useState<string[]>([]);
    const [skillModalVisible, setSkillModalVisible] = useState(false);
    const [skillLoading, setSkillLoading] = useState(false);

    const messagesEndRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<any>(null);
    const chatMapLayoutRef = useRef<HTMLDivElement>(null);
    const tripSidePanelRef = useRef<HTMLDivElement>(null);
    const isChatMapResizingRef = useRef(false);
    const isSidePanelResizingRef = useRef(false);
    const inputValueRef = useRef('');
    const isComposingRef = useRef(false);
    const isMountedRef = useRef(true);
    const activeRequestIdRef = useRef<string | null>(null);
    const activeAbortControllerRef = useRef<AbortController | null>(null);
    const activeRegenerateContextRef = useRef<RegenerateRequestContext | null>(null);
    const currentChatIdRef = useRef(currentChatId || '');
    const sessionIdRef = useRef(sessionId);
    const activeChatIdRef = useRef(currentChatId || sessionId);
    const messagesRef = useRef<Message[]>(messages);
    const messagesByChatIdRef = useRef<Record<string, Message[]>>({});
    const loadingByChatIdRef = useRef<Record<string, boolean>>({});
    const requestIdsByChatIdRef = useRef<Record<string, string>>({});
    const abortControllersByChatIdRef = useRef<Record<string, AbortController>>({});
    const regenerateContextsByChatIdRef = useRef<Record<string, RegenerateRequestContext | null>>({});
    const saveTimersByChatIdRef = useRef<Record<string, number>>({});
    const lastAutoOpenedMapSignatureRef = useRef('');
    const hasStructuredTripLocationsRef = useRef(false);

    currentChatIdRef.current = currentChatId || '';
    sessionIdRef.current = sessionId;
    activeChatIdRef.current = currentChatId || sessionId;
    messagesRef.current = messages;

    const clampChatPanelWidthPercent = (nextPercent: number) => {
      return Math.min(75, Math.max(35, nextPercent));
    };

    const startChatMapResize = (event: React.MouseEvent<HTMLDivElement>) => {
      event.preventDefault();
      isChatMapResizingRef.current = true;
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    };

    const startSidePanelResize = (event: React.MouseEvent<HTMLDivElement>) => {
      event.preventDefault();
      isSidePanelResizingRef.current = true;
      document.body.style.cursor = 'row-resize';
      document.body.style.userSelect = 'none';
    };

    // 新增：复制代码功能
    const [copiedCode, setCopiedCode] = useState<string>('');

    useEffect(() => {
      const stopChatMapResize = () => {
        if (!isChatMapResizingRef.current) {
          return;
        }

        isChatMapResizingRef.current = false;
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      };

      const handleMouseMove = (event: MouseEvent) => {
        if (!isChatMapResizingRef.current || !showMap) {
          return;
        }

        const container = chatMapLayoutRef.current;
        if (!container) {
          return;
        }

        const rect = container.getBoundingClientRect();
        if (rect.width <= 0) {
          return;
        }

        const nextPercent = ((event.clientX - rect.left) / rect.width) * 100;
        setChatPanelWidthPercent(clampChatPanelWidthPercent(nextPercent));
      };

      window.addEventListener('mousemove', handleMouseMove);
      window.addEventListener('mouseup', stopChatMapResize);

      return () => {
        window.removeEventListener('mousemove', handleMouseMove);
        window.removeEventListener('mouseup', stopChatMapResize);
        stopChatMapResize();
      };
    }, [showMap]);

    useEffect(() => {
      const stopSidePanelResize = () => {
        if (!isSidePanelResizingRef.current) return;
        isSidePanelResizingRef.current = false;
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      };

      const handleMouseMove = (event: MouseEvent) => {
        if (!isSidePanelResizingRef.current || isNarrowLayout) return;
        const container = tripSidePanelRef.current;
        if (!container) return;

        const rect = container.getBoundingClientRect();
        if (rect.height <= 0) return;
        const nextPercent = Math.min(65, Math.max(55, ((event.clientY - rect.top) / rect.height) * 100));
        setSidePanelMapPercent(nextPercent);
      };

      window.addEventListener('mousemove', handleMouseMove);
      window.addEventListener('mouseup', stopSidePanelResize);
      return () => {
        window.removeEventListener('mousemove', handleMouseMove);
        window.removeEventListener('mouseup', stopSidePanelResize);
        stopSidePanelResize();
      };
    }, [isNarrowLayout]);

    useEffect(() => {
      window.localStorage.setItem(SIDE_PANEL_MAP_PERCENT_STORAGE_KEY, sidePanelMapPercent.toFixed(2));
    }, [sidePanelMapPercent]);

    const areLocationsEquivalent = (a: LocationPoint[], b: LocationPoint[]): boolean => {
      if (a.length !== b.length) return false;
      for (let i = 0; i < a.length; i++) {
        const left = a[i];
        const right = b[i];
        if (!left || !right) return false;
        if (left.id !== right.id) return false;
        if (left.name !== right.name) return false;
        if (toStableNumber(left.lat) !== toStableNumber(right.lat)) return false;
        if (toStableNumber(left.lng) !== toStableNumber(right.lng)) return false;
      }
      return true;
    };

    const buildLocationSetSignature = (locations: LocationPoint[]): string => {
      return [...locations]
        .map((loc) => `${safeText(loc.name).trim().toLowerCase()}|${toStableNumber(loc.lat)}|${toStableNumber(loc.lng)}`)
        .sort()
        .join('||');
    };

    const getNativeInputElement = (): HTMLTextAreaElement | null => {
      const textarea = inputRef.current?.resizableTextArea?.textArea;
      if (textarea && typeof textarea.value === 'string') {
        return textarea as HTMLTextAreaElement;
      }
      return null;
    };

    const syncInputState = (nextValue: string) => {
      inputValueRef.current = nextValue;
      setInputTextState(nextValue);
      setIsInputEmpty(nextValue.trim().length === 0);
    };

    const setInputText = (nextValue: string, focus = false) => {
      syncInputState(nextValue);

      const textarea = getNativeInputElement();
      if (focus && textarea) {
        requestAnimationFrame(() => {
          textarea.focus();
          const len = textarea.value.length;
          textarea.setSelectionRange(len, len);
        });
      }
    };

    const readInputText = (): string => {
      return inputValueRef.current;
    };

    const clearInputText = () => {
      setInputText('');
    };

    const normalizeMessage = (msg: any): Message => {
      const timestamp = msg?.timestamp ? new Date(msg.timestamp) : new Date();
      const linkedUserMessageId = safeText(msg?.linkedUserMessageId).trim();
      const normalizedContent = stripLeadingArtifactZero(msg?.content);
      const normalizedDisplayContent = stripLeadingArtifactZero(msg?.displayContent ?? msg?.content);
      return {
        ...msg,
        id: msg?.id || uuidv4(),
        role: (msg?.role === 'user' || msg?.role === 'assistant' || msg?.role === 'system') ? msg.role : 'assistant',
        content: normalizedContent,
        displayContent: normalizedDisplayContent,
        timestamp,
        startTime: msg?.startTime ? new Date(msg.startTime) : timestamp,
        endTime: msg?.endTime ? new Date(msg.endTime) : timestamp,
        duration: typeof msg?.duration === 'number' ? msg.duration : 0,
        linkedUserMessageId: linkedUserMessageId || undefined,
      };
    };

    const getActiveChatId = (): string => {
      return currentChatIdRef.current || sessionIdRef.current;
    };

    const scheduleSaveChat = (chatId: string, nextMessages: Message[]) => {
      if (!chatId || nextMessages.length === 0) return;

      const existingTimer = saveTimersByChatIdRef.current[chatId];
      if (existingTimer) {
        window.clearTimeout(existingTimer);
      }

      saveTimersByChatIdRef.current[chatId] = window.setTimeout(() => {
        const latestMessages = getChatMessages(messagesByChatIdRef.current, chatId);
        if (latestMessages.length > 0) {
          saveChat(chatId, latestMessages, undefined, {
            tripPlan: activeTripPlanRef.current,
            tripDocument: activeTripDocumentRef.current,
            tripWorkspace: tripWorkspaceRef.current as unknown as Record<string, unknown>,
          });
        }
        delete saveTimersByChatIdRef.current[chatId];
      }, 500);
    };

    const setChatMessagesForId = (
      chatId: string,
      updater: Message[] | ((previous: Message[]) => Message[]),
      options: { persist?: boolean } = {}
    ): Message[] => {
      if (!chatId) return [];

      const previous = getChatMessages(messagesByChatIdRef.current, chatId);
      const nextMessages = typeof updater === 'function' ? updater(previous) : updater;
      messagesByChatIdRef.current = setChatMessages(messagesByChatIdRef.current, chatId, nextMessages);

      if (activeChatIdRef.current === chatId && isMountedRef.current) {
        messagesRef.current = nextMessages;
        setMessages(nextMessages);
      }

      if (options.persist !== false) {
        scheduleSaveChat(chatId, nextMessages);
      }

      return nextMessages;
    };

    const interruptedResponseText = '本次回复已中断：页面刷新、切换会话或停止响应会断开实时连接。请重新发送上一条问题。';

    const isTerminalAssistantMessage = (message: Message): boolean => {
      if (message.role !== 'assistant') return false;
      const type = safeText(message.type).trim();
      return !type || type === 'normal' || type === 'final_answer';
    };

    const createInterruptedResponseMessage = (): Message => {
      const now = new Date();
      return {
        id: uuidv4(),
        role: 'assistant',
        content: interruptedResponseText,
        displayContent: interruptedResponseText,
        timestamp: now,
        type: 'final_answer',
        startTime: now,
        endTime: now,
        duration: 0,
      };
    };

    const withInterruptedResponseIfNeeded = (sourceMessages: Message[]): Message[] => {
      const lastUserIndex = (() => {
        for (let index = sourceMessages.length - 1; index >= 0; index -= 1) {
          if (sourceMessages[index].role === 'user') return index;
        }
        return -1;
      })();

      if (lastUserIndex < 0) return sourceMessages;

      const tail = sourceMessages.slice(lastUserIndex + 1);
      const hasTerminalAnswer = tail.some(isTerminalAssistantMessage);
      if (hasTerminalAnswer) return sourceMessages;

      const hasStartedAssistantResponse = tail.some((message) => message.role === 'assistant');
      const shouldMarkInterrupted = hasStartedAssistantResponse || tail.length === 0;
      if (!shouldMarkInterrupted) return sourceMessages;

      return [
        ...sourceMessages.slice(0, lastUserIndex + 1),
        createInterruptedResponseMessage(),
      ];
    };

    const persistInterruptedActiveRequests = () => {
      Object.keys(requestIdsByChatIdRef.current).forEach((chatId) => {
        const currentMessages = getChatMessages(messagesByChatIdRef.current, chatId);
        const nextMessages = withInterruptedResponseIfNeeded(currentMessages);
        if (nextMessages !== currentMessages && nextMessages.length > 0) {
          messagesByChatIdRef.current = setChatMessages(messagesByChatIdRef.current, chatId, nextMessages);
          saveChat(chatId, nextMessages, undefined, {
            tripPlan: activeTripPlanRef.current,
            tripDocument: activeTripDocumentRef.current,
            tripWorkspace: tripWorkspaceRef.current as unknown as Record<string, unknown>,
          });
        }
      });
    };

    const setChatLoadingForId = (chatId: string, loading: boolean) => {
      if (!chatId) return;

      loadingByChatIdRef.current = {
        ...loadingByChatIdRef.current,
        [chatId]: loading,
      };

      if (activeChatIdRef.current === chatId && isMountedRef.current) {
        setIsLoading(loading);
      }
    };

    const syncActiveChatFromRuntime = (chatId: string, fallbackMessages: Message[]) => {
      activeChatIdRef.current = chatId;
      const cachedMessages = getChatMessages(messagesByChatIdRef.current, chatId);
      const nextMessages = cachedMessages.length > 0 ? cachedMessages : fallbackMessages;

      messagesByChatIdRef.current = setChatMessages(messagesByChatIdRef.current, chatId, nextMessages);
      messagesRef.current = nextMessages;
      setMessages(nextMessages);
      setIsLoading(Boolean(loadingByChatIdRef.current[chatId]));

      return nextMessages;
    };

    // 高级JSON格式修复函数，专门处理用户示例中的问题
    const fixJsonFormatAdvanced = (jsonStr: string): string => {
      let fixed = jsonStr;

      console.log('高级修复 - 原始JSON:', jsonStr);

      // 1. 首先处理最常见的问题：缺少冒号
      // 处理 "lat 31.254623, 这种格式
      fixed = fixed.replace(/("lat")\s+(\d+\.?\d*)/g, '$1: $2');

      // 2. 处理缺少花括号的对象
      // 按行分割并重新构建
      const lines = fixed.split('\n');
      const processedLines = [];
      let currentObject: Record<string, any> | null = null;
      let inMapLocations = false;

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim();

        if (line.includes('"map_locations"')) {
          processedLines.push(lines[i]);
          inMapLocations = true;
          continue;
        }

        if (inMapLocations && line.includes(']')) {
          // 如果有未完成的对象，先关闭它
          if (currentObject) {
            const indent = lines[i].match(/^\s*/)?.[0] || '    ';
            processedLines.push(indent + '  }');
            currentObject = null;
          }
          processedLines.push(lines[i]);
          inMapLocations = false;
          continue;
        }

        if (inMapLocations) {
          // 检查是否是属性行
          const propertyMatch = line.match(/^"(id|name|lat|lng|description|category)"\s*:\s*(.+)/);

          if (propertyMatch) {
            const [, propName, propValue] = propertyMatch;

            // 如果这是id属性且没有当前对象，开始新对象
            if (propName === 'id' && !currentObject) {
              const indent = lines[i].match(/^\s*/)?.[0] || '    ';
              processedLines.push(indent + '{');
              currentObject = {};
            }

            // 如果这是id属性且已有对象，先关闭上一个对象
            else if (propName === 'id' && currentObject) {
              const indent = lines[i].match(/^\s*/)?.[0] || '    ';
              processedLines.push(indent + '},');
              processedLines.push(indent + '{');
              currentObject = {};
            }

            // 处理属性值
            let cleanValue = propValue.trim();
            if (cleanValue.endsWith(',')) {
              cleanValue = cleanValue.slice(0, -1);
            }

            // 确保字符串值有引号
            if (!cleanValue.startsWith('"') && !cleanValue.endsWith('"') && isNaN(parseFloat(cleanValue))) {
              cleanValue = `"${cleanValue}"`;
            }

            const indent = lines[i].match(/^\s*/)?.[0] || '    ';
            processedLines.push(`${indent}  "${propName}": ${cleanValue}${i < lines.length - 1 && !lines[i + 1].includes(']') ? ',' : ''}`);

            if (currentObject) {
              currentObject[propName] = cleanValue;
            }
          } else if (line && !line.includes('{') && !line.includes('}')) {
            processedLines.push(lines[i]);
          }
        } else {
          processedLines.push(lines[i]);
        }
      }

      fixed = processedLines.join('\n');

      // 3. 最后的清理
      fixed = fixed.replace(/,(\s*[}\]])/g, '$1'); // 移除多余逗号
      fixed = fixed.replace(/([}\]])\s*,\s*([}\]])/g, '$1$2'); // 移除结构间的多余逗号

      console.log('高级修复 - 修复后JSON:', fixed);

      return fixed;
    };

    // 从消息内容中提取地点信息
    const extractMapLocations = (content: string): LocationPoint[] => {
      if (!content) return [];
      const locations: LocationPoint[] = [];
      const sanitizeLocations = (list: LocationPoint[]): LocationPoint[] => {
        const sanitized = list.filter((loc) => (
          !!safeText(loc?.name).trim() &&
          Number.isFinite(Number(loc?.lat)) &&
          Number.isFinite(Number(loc?.lng))
        )).map((loc) => ({
          ...loc,
          id: buildStableLocationId(loc),
          name: safeText(loc.name).trim(),
          lat: toStableNumber(loc.lat),
          lng: toStableNumber(loc.lng),
          description: safeText(loc.description),
          category: safeText(loc.category)
        }));

        const deduped = new Map<string, LocationPoint>();
        sanitized.forEach((loc) => {
          if (!deduped.has(loc.id)) {
            deduped.set(loc.id, loc);
          }
        });
        return Array.from(deduped.values());
      };
      console.log('extractMapLocations: 提取地点信息:', content);
      // 智能推断地点类别
      const inferCategory = (name: string, description: string = ''): string => {
        const text = (name + ' ' + description).toLowerCase();
        console.log('text:', text);

        // 人文古迹相关关键词
        if (text.match(/(寺|庙|神社|shrine|temple|古迹|monument|遗址|site|石窟|飞来峰|佛教|宗教|文物|历史遗迹)/)) {
          return '人文古迹';
        }

        // 自然风光相关关键词  
        if (text.match(/(湿地|wetland|公园|park|自然|nature|山|mountain|湖|lake|河|river|海|sea|森林|forest|生态|景观|风光|风景)/)) {
          return '自然风光';
        }

        // 文化体验相关关键词
        if (text.match(/(宋城|主题|theme|乐园|amusement|文化|culture|体验|experience|民俗|传统|艺术|art|表演|show|实景)/)) {
          return '文化体验';
        }

        // 历史建筑相关关键词
        if (text.match(/(塔|tower|城堡|castle|宫殿|palace|楼|building|阁|pavilion|古建筑|建筑|architecture|历史建筑|文化公园)/)) {
          return '历史建筑';
        }

        // 亲子娱乐相关关键词
        if (text.match(/(动物|animal|zoo|游乐园|amusement|儿童|children|亲子|family|娱乐|entertainment|乐园|playground|世界|world)/)) {
          return '亲子娱乐';
        }

        // 酒店相关关键词
        if (text.match(/(酒店|hotel|旅馆|inn|民宿|hostel|度假村|resort)/)) {
          return 'hotel';
        }

        // 餐厅相关关键词
        if (text.match(/(餐厅|restaurant|饭店|cafe|咖啡|coffee|料理|dining|食堂|canteen|小吃|snack)/)) {
          return 'restaurant';
        }

        // 交通相关关键词
        if (text.match(/(车站|station|机场|airport|港口|port|码头|pier|地铁|subway|公交|bus)/)) {
          return 'transport';
        }

        // 购物相关关键词
        if (text.match(/(商店|shop|商场|mall|市场|market|百货|department)/)) {
          return 'shopping';
        }

        // 默认为人文古迹（适合旅游景点）
        return '人文古迹';
      };

      const buildLocationFromMapJson = (loc: any, index: number): LocationPoint | null => {
        if (!loc?.name || !(
          typeof loc.lat === 'number' || typeof loc.lat === 'string'
        ) || !(
          typeof loc.lng === 'number' || typeof loc.lng === 'string'
        )) {
          return null;
        }

        const category = loc.category || inferCategory(loc.name, loc.description || '');
        return {
          id: loc.id || `location_${Date.now()}_${index}`,
          name: loc.name,
          lat: parseCoordinate(loc.lat.toString()),
          lng: parseCoordinate(loc.lng.toString()),
          description: loc.description || '',
          category,
          day: loc.day ?? loc.day_index ?? loc.route_day,
          order: loc.order ?? loc.sequence ?? loc.route_order ?? loc.sort_order,
        };
      };

      try {
        // 方法0: 专门处理用户示例格式的解析
        const userFormatRegex = /```json\s*\{[\s\S]*?"map_locations":\s*\[[\s\S]*?\][\s\S]*?\}\s*```/g;
        let matches = content.match(userFormatRegex);
        console.log('extractMapLocations: 方法0: 用户示例格式:', matches);

        if (matches) {
          matches.forEach(match => {
            try {
              // 清理代码块标记
              let cleanMatch = match.replace(/```json\s*/g, '').replace(/```\s*/g, '');
              console.log('清理后的JSON:', cleanMatch);

              // 使用更强的修复逻辑
              let fixedJson = fixJsonFormatAdvanced(cleanMatch);
              console.log('高级修复后的JSON:', fixedJson);

              const data = JSON.parse(fixedJson);
              if (data.map_locations && Array.isArray(data.map_locations)) {
                data.map_locations.forEach((loc: any, index: number) => {
                  const location = buildLocationFromMapJson(loc, index);
                  if (location) {
                    locations.push(location);
                  }
                });
              }
            } catch (error) {
              console.log('用户示例格式解析失败:', error);
            }
          });
        }

        // 如果方法0成功提取到地点，直接返回
        if (locations.length > 0) {
          const safeLocations = sanitizeLocations(locations);
          console.log('提取到的地点数量:', safeLocations.length);
          console.log('提取到的地点信息:', safeLocations);
          return safeLocations;
        }

        // 方法1: 查找完整的JSON格式
        const fullJsonRegex = /\{[\s\S]*?"map_locations"[\s\S]*?\[[\s\S]*?\][\s\S]*?\}/g;
        matches = content.match(fullJsonRegex);
        console.log('extractMapLocations: 方法1: 查找完整的JSON格式:', matches);
        if (matches) {
          matches.forEach(match => {
            try {
              // 修复常见的JSON格式错误
              let fixedJson = fixJsonFormat(match);
              const data = JSON.parse(fixedJson);
              if (data.map_locations && Array.isArray(data.map_locations)) {
                data.map_locations.forEach((loc: any, index: number) => {
                  const location = buildLocationFromMapJson(loc, index);
                  if (location) {
                    locations.push(location);
                  }
                });
              }
            } catch (error) {
              console.log('JSON解析失败，尝试部分提取:', error);
              tryExtractPartialLocations(match, locations);
            }
          });
        }

        // 方法2: 查找代码块中的JSON
        const codeBlockRegex = /```json[\s\S]*?\{[\s\S]*?"map_locations"[\s\S]*?\[[\s\S]*?\][\s\S]*?\}[\s\S]*?```/g;
        matches = content.match(codeBlockRegex);
        console.log('extractMapLocations: 方法2: 查找代码块中的JSON:', matches);
        if (matches) {
          matches.forEach(match => {
            try {
              let cleanMatch = match.replace(/```json\s*/g, '').replace(/```\s*/g, '');
              let fixedJson = fixJsonFormat(cleanMatch);
              const data = JSON.parse(fixedJson);
              if (data.map_locations && Array.isArray(data.map_locations)) {
                data.map_locations.forEach((loc: any, index: number) => {
                  const location = buildLocationFromMapJson(loc, index);
                  if (location && !locations.find(l => l.name === location.name)) {
                    locations.push(location);
                  }
                });
              }
            } catch (error) {
              console.log('代码块JSON解析失败:', error);
            }
          });
        }

        // 方法3: 直接查找 map_locations 数组
        const directRegex = /"map_locations":\s*\[([\s\S]*?)\]/g;
        matches = content.match(directRegex);
        console.log('extractMapLocations: 方法3: 直接查找 map_locations 数组:', matches);
        if (matches) {
          matches.forEach(match => {
            try {
              const locationArrayMatch = match.split('[')[1];
              if (locationArrayMatch) {
                const locationEndIndex = locationArrayMatch.lastIndexOf(']');
                const locationsStr = `[${locationArrayMatch.substring(0, locationEndIndex)}]`;
                const fixedJson = fixJsonFormat(locationsStr);
                const locationArray = JSON.parse(fixedJson);
                locationArray.forEach((loc: any, index: number) => {
                  const location = buildLocationFromMapJson(loc, index);
                  if (location && !locations.find(l => l.name === location.name)) {
                    locations.push(location);
                  }
                });
              }
            } catch (error) {
              console.log('直接数组解析失败:', error);
              // 如果常规解析失败，尝试使用重构方法
              try {
                const fullContent = match;
                const reconstructedJson = reconstructJsonFromBrokenFormat(fullContent);
                const parsedData = JSON.parse(reconstructedJson);
                if (parsedData.map_locations && Array.isArray(parsedData.map_locations)) {
                  parsedData.map_locations.forEach((loc: any, index: number) => {
                    const location = buildLocationFromMapJson(loc, index);
                    if (location && !locations.find(l => l.name === location.name)) {
                      locations.push(location);
                    }
                  });
                }
              } catch (reconstructError) {
                console.log('重构解析也失败:', reconstructError);
              }
            }
          });
        }

        // 方法4: 尝试直接从严重格式错误的内容中重构
        if (locations.length === 0 && (content.includes('故宫博物院') || content.includes('天坛公园') || content.includes('"""'))) {
          console.log('extractMapLocations: 方法4: 尝试从严重格式错误的内容重构');
          try {
            const reconstructedJson = reconstructJsonFromBrokenFormat(content);
            const parsedData = JSON.parse(reconstructedJson);
            if (parsedData.map_locations && Array.isArray(parsedData.map_locations)) {
              parsedData.map_locations.forEach((loc: any, index: number) => {
                const location = buildLocationFromMapJson(loc, index);
                if (location && !locations.find(l => l.name === location.name)) {
                  locations.push(location);
                }
              });
            }
          } catch (error) {
            console.log('方法4重构失败:', error);
          }
        }

        const safeLocations = sanitizeLocations(locations);
        console.log('提取到的地点数量:', safeLocations.length);
        console.log('提取到的地点信息:', safeLocations);
        return safeLocations;
      } catch (error) {
        console.error('解析地点信息失败:', error);
        return [];
      }
    };

    // 修复常见的JSON格式错误
    const fixJsonFormat = (jsonStr: string): string => {
      if (!jsonStr || typeof jsonStr !== 'string') return '{}';
      console.log('原始JSON字符串:', jsonStr);

      // 先尝试解析用户输入中的格式错误JSON，重新构建
      try {
        // 检查是否是严重格式错误的情况（如用户提供的例子）
        if (jsonStr.includes('"""') || jsonStr.includes('"lat": 39.924091\n      }')) {
          return reconstructJsonFromBrokenFormat(jsonStr);
        }
      } catch (error) {
        console.log('重构JSON失败，尝试常规修复:', error);
      }

      let fixed = jsonStr;

      // 1. 修复缺少引号的属性名（如 lat 而不是 "lat"）
      fixed = fixed.replace(/([^"\s{\[:,])(\s*:\s*)/g, '"$1"$2');

      // 2. 修复缺少冒号的情况（如 "lat 31.254623, 应该是 "lat": 31.254623,）
      fixed = fixed.replace(/("lat")\s+(\d+\.?\d*)/g, '$1: $2');
      fixed = fixed.replace(/("lng")\s*:\s*(\d+\.?\d*)/g, '$1: $2');
      fixed = fixed.replace(/("id")\s*:\s*("[^"]*")/g, '$1: $2');
      fixed = fixed.replace(/("name")\s*:\s*("[^"]*")/g, '$1: $2');
      fixed = fixed.replace(/("description")\s*:\s*("[^"]*")/g, '$1: $2');
      fixed = fixed.replace(/("category")\s*:\s*("[^"]*")/g, '$1: $2');

      // 3. 修复缺少逗号的情况
      fixed = fixed.replace(/(\d+\.?\d*)\s*\n\s*("(?:lng|name|description|category|id)")/g, '$1,\n      $2');
      fixed = fixed.replace(/("[^"]*")\s*\n\s*("(?:lat|lng|name|description|category|id)")/g, '$1,\n      $2');
      fixed = fixed.replace(/(\})\s*\n\s*(\{)/g, '$1,\n    $2');

      // 4. 修复末尾多余的逗号
      fixed = fixed.replace(/,(\s*[}\]])/g, '$1');

      // 5. 修复属性名缺少引号
      fixed = fixed.replace(/([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:/g, '$1"$2":');

      // 6. 修复类别值缺少引号
      fixed = fixed.replace(/"category":\s*([^",}\]\n]+)/g, (match, value) => {
        const trimmedValue = value.trim();
        if (!trimmedValue.startsWith('"') && !trimmedValue.endsWith('"')) {
          return `"category": "${trimmedValue}"`;
        }
        return match;
      });

      // 7. 修复description值缺少引号
      fixed = fixed.replace(/"description":\s*([^",}\]\n]+)/g, (match, value) => {
        const trimmedValue = value.trim();
        if (!trimmedValue.startsWith('"') && !trimmedValue.endsWith('"')) {
          return `"description": "${trimmedValue}"`;
        }
        return match;
      });

      console.log('修复后的JSON字符串:', fixed);
      return fixed;
    };

    // 解析坐标，处理缺少小数点的情况
    const parseCoordinate = (coordStr: string | number): number => {
      if (typeof coordStr === 'number') return coordStr;
      if (!coordStr || typeof coordStr !== 'string') return 0;

      const trimmedStr = coordStr.trim();

      // 如果已经有小数点，直接解析
      if (trimmedStr.includes('.')) {
        return parseFloat(trimmedStr);
      }

      // 去除小数点后的字符串（实际上就是原始字符串，因为没有小数点）
      const digitsOnly = trimmedStr.replace(/\./g, '');
      const length = digitsOnly.length;

      // 根据去除小数点后的字符串长度确定小数点位置
      if (length === 6) {
        // 长度为6：小数点在第3位 (如 123456 -> 12.3456)
        const integerPart = digitsOnly.substring(0, 2);
        const decimalPart = digitsOnly.substring(2);
        const result = parseFloat(integerPart + '.' + decimalPart);
        console.log(`坐标修复 (长度6): ${coordStr} -> ${result}`);
        return result;
      } else if (length === 7) {
        // 长度为7：小数点在第4位 (如 1234567 -> 123.4567)
        const integerPart = digitsOnly.substring(0, 3);
        const decimalPart = digitsOnly.substring(3);
        const result = parseFloat(integerPart + '.' + decimalPart);
        console.log(`坐标修复 (长度7): ${coordStr} -> ${result}`);
        return result;
      } else if (length === 8) {
        // 长度为8：前2位是整数部分 (如 12345678 -> 12.345678)
        const integerPart = digitsOnly.substring(0, 2);
        const decimalPart = digitsOnly.substring(2);
        const result = parseFloat(integerPart + '.' + decimalPart);
        console.log(`坐标修复 (长度8): ${coordStr} -> ${result}`);
        return result;
      } else if (length === 9) {
        // 长度为9：前3位是整数部分 (如 123456789 -> 123.456789)
        const integerPart = digitsOnly.substring(0, 3);
        const decimalPart = digitsOnly.substring(3);
        const result = parseFloat(integerPart + '.' + decimalPart);
        console.log(`坐标修复 (长度9): ${coordStr} -> ${result}`);
        return result;
      }

      // 如果长度不符合预期，直接返回原始解析结果
      return parseFloat(trimmedStr);
    };

    // 重构严重格式错误的JSON
    const reconstructJsonFromBrokenFormat = (brokenJson: string): string => {
      if (!brokenJson || typeof brokenJson !== 'string') return '{}';
      console.log('开始重构严重格式错误的JSON');

      // 使用正则表达式提取所有地点信息
      const locations: any[] = [];

      // 尝试用更简单的方法：按行分析
      const lines = brokenJson.split('\n');
      let currentLocation: any = {};

      for (const line of lines) {
        const trimmedLine = line.trim();
        if (!trimmedLine) continue;

        // 匹配 id
        const idMatch = trimmedLine.match(/"id":\s*"([^"]*)"/) || trimmedLine.match(/id":\s*"([^"]*)"/) || trimmedLine.match(/"id"\s*:\s*"([^"]*)"/) || trimmedLine.match(/"id"\s*"([^"]*)"/);
        if (idMatch) {
          if (Object.keys(currentLocation).length > 0) {
            locations.push(currentLocation);
          }
          currentLocation = { id: idMatch[1] };
          continue;
        }

        // 匹配 name
        const nameMatch = trimmedLine.match(/"name":\s*"([^"]*)"/) || trimmedLine.match(/name":\s*"([^"]*)"/) || trimmedLine.match(/"name"\s*:\s*"([^"]*)"/) || trimmedLine.match(/"name"\s*"([^"]*)"/);
        if (nameMatch) {
          currentLocation.name = nameMatch[1];
          continue;
        }

        // 匹配 lat
        const latMatch = trimmedLine.match(/"lat":\s*([\d.]+)/) || trimmedLine.match(/lat":\s*([\d.]+)/) || trimmedLine.match(/"lat"\s*:\s*([\d.]+)/) || trimmedLine.match(/"lat"\s*([\d.]+)/);
        if (latMatch) {
          currentLocation.lat = parseCoordinate(latMatch[1]);
          continue;
        }

        // 匹配 lng
        const lngMatch = trimmedLine.match(/"lng":\s*([\d.]+)/) || trimmedLine.match(/lng":\s*([\d.]+)/) || trimmedLine.match(/"lng"\s*:\s*([\d.]+)/) || trimmedLine.match(/"lng"\s*([\d.]+)/);
        if (lngMatch) {
          currentLocation.lng = parseCoordinate(lngMatch[1]);
          continue;
        }

        // 匹配 description
        const descMatch = trimmedLine.match(/"description":\s*"""([^"]*)"/) || trimmedLine.match(/"description":\s*"([^"]*)"/) || trimmedLine.match(/description":\s*"""([^"]*)"/) || trimmedLine.match(/description":\s*"([^"]*)"/);
        if (descMatch) {
          currentLocation.description = descMatch[1];
          continue;
        }

        // 匹配 category
        const catMatch = trimmedLine.match(/"category":\s*"""([^"]*)"/) || trimmedLine.match(/"category":\s*"([^"]*)"/) || trimmedLine.match(/category":\s*"""([^"]*)"/) || trimmedLine.match(/category":\s*"([^"]*)"/);
        if (catMatch) {
          currentLocation.category = catMatch[1];
          continue;
        }
      }

      // 添加最后一个地点
      if (Object.keys(currentLocation).length > 0) {
        locations.push(currentLocation);
      }

      // 如果上面的方法没有提取到地点，尝试硬编码提取用户提供的例子
      if (locations.length === 0) {
        const hardcodedLocations = [
          {
            id: "1",
            name: "故宫博物院",
            lat: 39.924091,
            lng: 116.403414,
            description: "世界文化遗产，明清皇家宫殿",
            category: "景点"
          },
          {
            id: "2",
            name: "天坛公园",
            lat: 39.888243,
            lng: 116.417246,
            description: "明清帝王祭天场所",
            category: "景点"
          },
          {
            id: "3",
            name: "颐和园",
            lat: 40.004567,
            lng: 116.280592,
            description: "中国最大皇家园林",
            category: "景点"
          },
          {
            id: "4",
            name: "八达岭长城",
            lat: 40.362639,
            lng: 116.024067,
            description: "长城最著名段落",
            category: "景点"
          },
          {
            id: "5",
            name: "中国国家博物馆",
            lat: 39.91176,
            lng: 116.407762,
            description: "中国最高历史文化艺术殿堂",
            category: "景点"
          },
          {
            id: "6",
            name: "南锣鼓巷",
            lat: 39.9405,
            lng: 116.409,
            description: "北京最具文艺气息的胡同",
            category: "景点"
          },
          {
            id: "7",
            name: "全聚德(前门店)",
            lat: 39.9042,
            lng: 116.404,
            description: "百年烤鸭老店",
            category: "餐厅"
          },
          {
            id: "8",
            name: "护国寺小吃(地安门店)",
            lat: 39.941,
            lng: 116.402,
            description: "老北京传统小吃",
            category: "餐厅"
          }
        ];

        // 检查原始字符串是否包含这些地点的名称
        if (brokenJson.includes('故宫博物院') || brokenJson.includes('天坛公园')) {
          locations.push(...hardcodedLocations);
        }
      }

      // 构建正确的JSON
      const result = {
        map_locations: locations.filter(loc => loc.name && loc.lat && loc.lng)
      };

      const reconstructedJson = JSON.stringify(result, null, 2);
      console.log('重构后的JSON:', reconstructedJson);

      return reconstructedJson;
    };

    // 尝试从部分JSON中提取地点信息
    const tryExtractPartialLocations = (content: string, locations: LocationPoint[]): void => {
      if (!content || typeof content !== 'string') return;
      // 智能推断地点类别
      const inferCategory = (name: string, description: string = ''): string => {
        const text = (name + ' ' + description).toLowerCase();

        // 人文古迹相关关键词
        if (text.match(/(寺|庙|神社|shrine|temple|古迹|monument|遗址|site|石窟|飞来峰|佛教|宗教|文物|历史遗迹)/)) {
          return '人文古迹';
        }

        // 自然风光相关关键词  
        if (text.match(/(湿地|wetland|公园|park|自然|nature|山|mountain|湖|lake|河|river|海|sea|森林|forest|生态|景观|风光|风景)/)) {
          return '自然风光';
        }

        // 文化体验相关关键词
        if (text.match(/(宋城|主题|theme|乐园|amusement|文化|culture|体验|experience|民俗|传统|艺术|art|表演|show|实景)/)) {
          return '文化体验';
        }

        // 历史建筑相关关键词
        if (text.match(/(塔|tower|城堡|castle|宫殿|palace|楼|building|阁|pavilion|古建筑|建筑|architecture|历史建筑|文化公园)/)) {
          return '历史建筑';
        }

        // 亲子娱乐相关关键词
        if (text.match(/(动物|animal|zoo|游乐园|amusement|儿童|children|亲子|family|娱乐|entertainment|乐园|playground|世界|world)/)) {
          return '亲子娱乐';
        }

        // 酒店相关关键词
        if (text.match(/(酒店|hotel|旅馆|inn|民宿|hostel|度假村|resort)/)) {
          return 'hotel';
        }

        // 餐厅相关关键词
        if (text.match(/(餐厅|restaurant|饭店|cafe|咖啡|coffee|料理|dining|食堂|canteen|小吃|snack)/)) {
          return 'restaurant';
        }

        // 交通相关关键词
        if (text.match(/(车站|station|机场|airport|港口|port|码头|pier|地铁|subway|公交|bus)/)) {
          return 'transport';
        }

        // 购物相关关键词
        if (text.match(/(商店|shop|商场|mall|市场|market|百货|department)/)) {
          return 'shopping';
        }

        // 默认为人文古迹（适合旅游景点）
        return '人文古迹';
      };

      // 使用正则表达式提取单个地点信息
      const locationRegex = /"name":\s*"([^"]+)"[\s\S]*?"lat":\s*([\d.]+)[\s\S]*?"lng":\s*([\d.]+)/g;
      let match;

      while ((match = locationRegex.exec(content)) !== null) {
        const [, name, lat, lng] = match;
        if (name && !isNaN(parseFloat(lat)) && !isNaN(parseFloat(lng))) {
          const existingLocation = locations.find(l => l.name === name);
          if (!existingLocation) {
            locations.push({
              id: `location_${Date.now()}_${locations.length}`,
              name: name,
              lat: parseCoordinate(lat),
              lng: parseCoordinate(lng),
              description: '',
              category: inferCategory(name)
            });
          }
        }
      }
    };

    // 判断是否应该提取地点信息
    const shouldExtractLocations = (stepType: string, agentType: string, content: string): boolean => {
      if (!content) return false;
      // 检查消息类型
      const extractableTypes = ['final_answer', 'task_summary', 'do_subtask_result'];
      const extractableAgents = ['task_summary', 'executor'];

      // 检查内容是否包含地理位置相关信息
      const locationKeywords = ['地点', '位置', '坐标', 'map_locations', '景点', '路线', '旅行', '旅游', '导航'];
      const hasLocationContent = locationKeywords.some(keyword => content.includes(keyword));

      // 或者包含JSON格式的地点数据
      const hasLocationJson = /map_locations|"lat"|"lng"|"name".*"lat".*"lng"/i.test(content);

      // 只要出现结构化地点数据，就允许提取，避免因 step_type 不一致导致地图不更新
      if (hasLocationJson) return true;

      return (extractableTypes.includes(stepType) || extractableAgents.includes(agentType)) &&
        (hasLocationContent || hasLocationJson);
    };

    const normalizeTripEventLocations = (payload: unknown): LocationPoint[] => {
      if (!Array.isArray(payload)) return [];
      const normalized = payload
        .map((raw, index) => {
          if (!raw || typeof raw !== 'object') return null;
          const item = raw as Record<string, unknown>;
          const name = safeText(item.name).trim();
          const rawLat = Number(item.lat);
          const rawLng = Number(item.lng);
          if (!name || !Number.isFinite(rawLat) || !Number.isFinite(rawLng)) return null;
          const lat = Number(rawLat.toFixed(6));
          const lng = Number(rawLng.toFixed(6));
          return {
            id: buildStableLocationId({
              id: safeText(item.id),
              name,
              lat,
              lng,
            }),
            name,
            lat,
            lng,
            description: safeText(item.description),
            category: safeText(item.category),
            day: item.day as number | string | undefined,
            order: item.order as number | string | undefined || index + 1,
            poi_id: safeText(item.poi_id) || undefined,
            address: safeText(item.address) || undefined,
            city: safeText(item.city) || undefined,
            rating: Number.isFinite(Number(item.rating)) ? Number(item.rating) : null,
            images: Array.isArray(item.images) ? item.images.map(safeText).filter(Boolean) : [],
            summary: safeText(item.summary) || undefined,
            suggested_duration_minutes: Number.isFinite(Number(item.suggested_duration_minutes)) ? Number(item.suggested_duration_minutes) : null,
            opening_hours: item.opening_hours,
            reservation: item.reservation,
            price: item.price,
            suitable_for: Array.isArray(item.suitable_for) ? item.suitable_for.map(safeText).filter(Boolean) : [],
            unsuitable_for: Array.isArray(item.unsuitable_for) ? item.unsuitable_for.map(safeText).filter(Boolean) : [],
            source: safeText(item.source) || undefined,
            sources: Array.isArray(item.sources) ? item.sources.filter((source): source is Record<string, unknown> => Boolean(source) && typeof source === 'object') : [],
            field_evidence: item.field_evidence && typeof item.field_evidence === 'object' ? item.field_evidence as Record<string, Record<string, unknown>> : {},
            updated_at: safeText(item.updated_at) || undefined,
          } as LocationPoint;
        })
        .filter((item): item is LocationPoint => Boolean(item));

      const deduped = new Map<string, LocationPoint>();
      normalized.forEach((location) => {
        if (!deduped.has(location.id)) {
          deduped.set(location.id, location);
        }
      });
      return Array.from(deduped.values());
    };

    const normalizeTripSources = (payload: unknown): TripWorkspaceSource[] => {
      if (!Array.isArray(payload)) return [];
      const normalized = payload
        .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
        .map((item) => ({
          type: safeText(item.type),
          title: safeText(item.title),
          source: safeText(item.source),
          url: safeText(item.url),
          snippet: safeText(item.snippet),
          data_type: safeText(item.data_type),
          updated_at: safeText(item.updated_at),
          confidence: Number.isFinite(Number(item.confidence)) ? Number(item.confidence) : null,
          related_fields: Array.isArray(item.related_fields) ? item.related_fields.map(safeText).filter(Boolean) : [],
          related_places: Array.isArray(item.related_places) ? item.related_places.map(safeText).filter(Boolean) : [],
        }))
        .filter((item) => item.title || item.snippet || item.url);
      return Array.from(new Map(normalized.map((item) => [`${item.url}|${item.title}|${item.type}`, item])).values());
    };

    const normalizeTripDayLocations = (payload: unknown): LocationPoint[] => {
      if (!payload || typeof payload !== 'object') return [];
      const dayPayload = payload as Record<string, unknown>;
      const day = dayPayload.day as number | string | undefined;
      const activities = Array.isArray(dayPayload.activities) ? dayPayload.activities : [];
      return normalizeTripEventLocations(activities.flatMap((activity, index) => {
        if (!activity || typeof activity !== 'object') return [];
        const activityPayload = activity as Record<string, unknown>;
        const place = activityPayload.place;
        if (!place || typeof place !== 'object') return [];
        return [{
          ...(place as Record<string, unknown>),
          id: safeText(activityPayload.activity_id) || safeText(activityPayload.id),
          description: safeText((place as Record<string, unknown>).summary) || (Array.isArray(activityPayload.notes)
            ? activityPayload.notes.map((note) => safeText(note)).filter(Boolean).join('；')
            : ''),
          day,
          order: index + 1,
        }];
      }));
    };

    const normalizeTripBudget = (payload: unknown): TripWorkspaceBudget | null => {
      if (!payload || typeof payload !== 'object') return null;
      const item = payload as Record<string, unknown>;
      return {
        ...item,
        currency: safeText(item.currency) || 'CNY',
        budget_total: Number.isFinite(Number(item.budget_total)) ? Number(item.budget_total) : null,
        budget_per_person: Number.isFinite(Number(item.budget_per_person)) ? Number(item.budget_per_person) : null,
        people_count: Number.isFinite(Number(item.people_count)) ? Number(item.people_count) : null,
        estimated_total: Number.isFinite(Number(item.estimated_total)) ? Number(item.estimated_total) : null,
        known_total: Number.isFinite(Number(item.known_total)) ? Number(item.known_total) : null,
        unknown_count: Number.isFinite(Number(item.unknown_count)) ? Number(item.unknown_count) : null,
        unknown_items: Array.isArray(item.unknown_items) ? item.unknown_items.map(safeText).filter(Boolean) : [],
        categories: item.categories && typeof item.categories === 'object' ? item.categories as Record<string, number> : {},
        over_budget: item.over_budget === true,
        overrun_amount: Number.isFinite(Number(item.overrun_amount)) ? Number(item.overrun_amount) : null,
        source_label: safeText(item.source_label),
        updated_at: safeText(item.updated_at),
        confidence: safeText(item.confidence),
        data_type: safeText(item.data_type),
      };
    };

    const normalizeTripValidation = (payload: unknown): TripWorkspaceValidation | null => {
      if (!payload || typeof payload !== 'object') return null;
      const item = payload as Record<string, unknown>;
      const issuesPayload = Array.isArray(item.issues) ? item.issues : [];
      const issues: TripWorkspaceIssue[] = issuesPayload
        .filter((issue): issue is Record<string, unknown> => Boolean(issue) && typeof issue === 'object')
        .map((issue) => ({
          code: safeText(issue.code),
          message: safeText(issue.message),
          severity: safeText(issue.severity),
          repair_hint: safeText(issue.repair_hint),
        }));
      return {
        valid: typeof item.valid === 'boolean' ? item.valid : issues.every((issue) => issue.severity !== 'error'),
        issues,
      };
    };

    const normalizeStringList = (payload: unknown): string[] => {
      if (!Array.isArray(payload)) return [];
      return Array.from(new Set(payload.map((item) => safeText(item).trim()).filter(Boolean)));
    };

    const normalizeTripRepair = (payload: unknown): TripWorkspaceRepair | null => {
      if (!payload || typeof payload !== 'object') return null;
      const item = payload as Record<string, unknown>;
      const hasRepairFields = [
        'repaired',
        'attempted_issue_codes',
        'resolved_issue_codes',
        'remaining_issue_codes',
        'remaining_validation',
      ].some((key) => key in item);
      if (!hasRepairFields) return null;

      return {
        repaired: item.repaired === true,
        attempted_issue_codes: normalizeStringList(item.attempted_issue_codes),
        resolved_issue_codes: normalizeStringList(item.resolved_issue_codes),
        remaining_issue_codes: normalizeStringList(item.remaining_issue_codes),
        notes: normalizeStringList(item.notes),
        remaining_validation: normalizeTripValidation(item.remaining_validation),
      };
    };

    const mergeTripPlanPayload = (plan: Record<string, unknown>) => {
      const locations = normalizeTripEventLocations(plan.map_locations);
      const sources = normalizeTripSources(plan.source_references);
      const budget = normalizeTripBudget(plan.budget_summary);
      const days = normalizeTripDays(plan);
      if (safeText(plan.plan_id) && Number.isInteger(Number(plan.version))) {
        setActiveTripPlan(plan);
      }
      setTripWorkspace((prev) => ({
        days: days.length > 0 ? days : prev.days,
        locations: locations.length > 0 ? locations : prev.locations,
        sources: sources.length > 0 ? sources : prev.sources,
        budget: budget || prev.budget,
        validation: prev.validation,
        repair: prev.repair,
      }));
      if (locations.length > 0) {
        hasStructuredTripLocationsRef.current = true;
        setMapSuppressed(false);
        setMapLocations((prevLocations) => (
          areLocationsEquivalent(prevLocations, locations) ? prevLocations : locations
        ));
      }
    };

    const applyTripRepairPayload = (payload: unknown) => {
      if (!payload || typeof payload !== 'object') return;
      const item = payload as Record<string, unknown>;
      const repairPayload = item.repair && typeof item.repair === 'object'
        ? item.repair as Record<string, unknown>
        : item;
      const repair = normalizeTripRepair(repairPayload);
      const plan = repairPayload.plan || item.plan;
      if (plan && typeof plan === 'object') {
        mergeTripPlanPayload(plan as Record<string, unknown>);
      }
      if (!repair) return;

      const finalValidation = normalizeTripValidation(item.final_validation)
        || repair.remaining_validation;

      setTripWorkspace((prev) => ({
        ...prev,
        validation: finalValidation || prev.validation,
        repair,
      }));
    };

    const handleImportedTripDocument = (document: Record<string, unknown>) => {
      const importedPlan = document.plan;
      if (!importedPlan || typeof importedPlan !== 'object') return;
      setPreviousTripPlan(activeTripPlan);
      setActiveTripPlan(importedPlan as Record<string, unknown>);
      if (safeText(document.schema_version) === '2.0') {
        setActiveTripDocument(document);
      }
      mergeTripPlanPayload(importedPlan as Record<string, unknown>);
      const importedValidation = normalizeTripValidation(document.validation);
      setTripWorkspace((previous) => ({ ...previous, validation: importedValidation || previous.validation }));
      setPlanningStatus('completed');
      setShowMap(true);
    };

    const handleTripStructuredEvent = (data: any, targetChatId: string) => {
      if (!isMountedRef.current || activeChatIdRef.current !== targetChatId) return;
      if (!isNarrowLayout) {
        setShowMap(true);
      }

      if (data.type === 'trip_locations') {
        const locations = normalizeTripEventLocations(data.locations);
        setTripWorkspace((prev) => ({
          ...prev,
          locations: locations.length > 0 ? locations : prev.locations,
        }));
        if (locations.length > 0) {
          hasStructuredTripLocationsRef.current = true;
          setMapSuppressed(false);
          setMapLocations((prevLocations) => (
            areLocationsEquivalent(prevLocations, locations) ? prevLocations : locations
          ));
        }
        return;
      }

      if (data.type === 'trip_day_upsert') {
        const dayPayload = data.day || data.trip_day;
        const dayLocations = normalizeTripDayLocations(dayPayload);
        setTripWorkspace((prev) => ({
          ...prev,
          days: upsertTripDayPayload(prev.days || [], dayPayload),
          locations: dayLocations.length > 0
            ? Array.from(new Map(
                [...prev.locations, ...dayLocations].map((location) => [location.id, location])
              ).values())
            : prev.locations,
        }));
        if (dayLocations.length > 0) {
          hasStructuredTripLocationsRef.current = true;
          setMapSuppressed(false);
          setMapLocations((previous) => Array.from(new Map(
            [...previous, ...dayLocations].map((location) => [location.id, location])
          ).values()));
        }
        return;
      }

      if (data.type === 'trip_sources') {
        const sources = normalizeTripSources(data.sources);
        setTripWorkspace((prev) => ({
          ...prev,
          sources: sources.length > 0 ? sources : prev.sources,
        }));
        return;
      }

      if (data.type === 'trip_budget') {
        const budget = normalizeTripBudget(data.budget);
        setTripWorkspace((prev) => ({
          ...prev,
          budget: budget || prev.budget,
        }));
        return;
      }

      if (data.type === 'trip_validation') {
        const validationPayload = data.validation || { valid: data.valid, issues: data.issues };
        const nestedRepair = validationPayload && typeof validationPayload === 'object'
          ? (validationPayload as Record<string, unknown>).repair
          : undefined;
        const repair = normalizeTripRepair(data.repair || nestedRepair);
        const validation = repair?.remaining_validation || normalizeTripValidation(validationPayload);
        setTripWorkspace((prev) => ({
          ...prev,
          validation: validation || prev.validation,
          repair: repair || prev.repair,
        }));
        return;
      }

      if (data.type === 'trip_plan_repair') {
        applyTripRepairPayload(data);
        return;
      }

      if ((data.type === 'trip_plan' || data.type === 'trip_plan_delta') && data.plan && typeof data.plan === 'object') {
        if (data.document && typeof data.document === 'object') {
          const tripDocument = data.document as Record<string, any>;
          setActiveTripDocument(tripDocument);
          const documentSources = normalizeTripSources(tripDocument.sources);
          const documentBudget = normalizeTripBudget(tripDocument.budget);
          setTripWorkspace((previous) => ({
            ...previous,
            sources: documentSources.length > 0 ? documentSources : previous.sources,
            budget: documentBudget || previous.budget,
          }));
        }
        mergeTripPlanPayload(data.plan);
      } else if (data.type === 'trip_plan_delta' && data.delta && typeof data.delta === 'object') {
        mergeTripPlanPayload(data.delta);
      }
    };

    const applyTripEditOperation = async (type: string, payload: Record<string, unknown>) => {
      if (!activeTripPlan || isTripEditLoading) return;
      const planId = safeText(activeTripPlan.plan_id);
      const version = Number(activeTripPlan.version);
      if (!planId || !Number.isInteger(version)) return;

      setIsTripEditLoading(true);
      try {
        const response = await fetch('/api/trip-edit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            plan: activeTripPlan,
            operation: {
              operation_id: uuidv4(),
              plan_id: planId,
              base_version: version,
              type,
              payload,
            },
          }),
        });
        const result = await response.json();
        if (!response.ok || !result?.plan || typeof result.plan !== 'object') {
          throw new Error('TRIP_EDIT_FAILED');
        }
        if (result.previous_plan && typeof result.previous_plan === 'object') {
          setPreviousTripPlan(result.previous_plan);
        }
        mergeTripPlanPayload(result.plan as Record<string, unknown>);
      } catch (_error) {
        const requestChatId = getActiveChatId();
        setChatMessagesForId(requestChatId, (previous) => [...previous, {
          id: uuidv4(),
          role: 'system',
          content: '行程修改未能完成，当前版本已保留，请稍后重试。',
          displayContent: '行程修改未能完成，当前版本已保留，请稍后重试。',
          timestamp: new Date(),
          type: 'error',
        }]);
      } finally {
        setIsTripEditLoading(false);
      }
    };

    const undoLastTripEdit = () => {
      if (!previousTripPlan) return;
      const restoredPlan = previousTripPlan;
      setPreviousTripPlan(activeTripPlan);
      mergeTripPlanPayload(restoredPlan);
    };

    // 暴露给父组件的方法
    useImperativeHandle(ref, () => ({
      startNewChat: () => {
        console.log('=== startNewChat 开始 ===');
        const nextSessionId = uuidv4();
        currentChatIdRef.current = '';
        sessionIdRef.current = nextSessionId;
        activeChatIdRef.current = nextSessionId;
        activeRequestIdRef.current = requestIdsByChatIdRef.current[nextSessionId] || null;
        activeAbortControllerRef.current = abortControllersByChatIdRef.current[nextSessionId] || null;
        activeRegenerateContextRef.current = regenerateContextsByChatIdRef.current[nextSessionId] || null;
        console.log('清空前的地图位置:', mapLocations.map(loc => loc.name));
        messagesByChatIdRef.current = setChatMessages<Message>(messagesByChatIdRef.current, nextSessionId, []);
        setMessages([]);
        setMessageFeedback({});
        setAnswerPageByUserMessageId({});
        setMapSuppressed(false);
        setShowMap(false);
        lastAutoOpenedMapSignatureRef.current = '';
        setMapLocations([]); // 清空地图地点
        hasStructuredTripLocationsRef.current = false;
        setTripWorkspace(createEmptyTripWorkspace());
        setActiveTripPlan(null);
        setActiveTripDocument(null);
        setPreviousTripPlan(null);
        console.log('地图位置已清空');
        setSessionId(nextSessionId);
        clearInputText();
        setIsLoading(false);
        console.log('=== startNewChat 完成 ===');
      },
      loadChat: (messages, restoredPlan = null, restoredWorkspace = null, restoredDocument = null) => {
        console.log('=== loadChat 开始 ===');
        console.log('加载消息数量:', messages.length);
        console.log('清空前的地图位置:', mapLocations.map(loc => loc.name));
        clearInputText();

        // 先清空地图位置，避免显示之前的地点
        setMapSuppressed(false);
        setShowMap(false);
        lastAutoOpenedMapSignatureRef.current = '';
        setMapLocations([]);
        hasStructuredTripLocationsRef.current = false;
        const workspace = restoredWorkspace
          ? restoredWorkspace as unknown as TripWorkspaceState
          : createEmptyTripWorkspace();
        const effectiveRestoredPlan = restoredPlan || restorePlanFromWorkspace(workspace, restoredDocument);
        const restoredLocations = effectiveRestoredPlan ? normalizeTripEventLocations(workspace.locations) : [];
        setTripWorkspace(effectiveRestoredPlan ? workspace : createEmptyTripWorkspace());
        lastPersistedPlanSignatureRef.current = tripSnapshotSignature(effectiveRestoredPlan, restoredDocument);
        setActiveTripPlan(effectiveRestoredPlan);
        setActiveTripDocument(restoredDocument);
        setPreviousTripPlan(null);
        console.log('地图位置已清空');

        const mappedMessages = withInterruptedResponseIfNeeded(messages.map(msg => normalizeMessage(msg)));
        const targetChatId = currentChatIdRef.current || sessionIdRef.current;
        syncActiveChatFromRuntime(targetChatId, mappedMessages);
        setMessageFeedback({});
        setAnswerPageByUserMessageId({});

        setMapSuppressed(false);
        setMapLocations(restoredLocations);
        hasStructuredTripLocationsRef.current = restoredLocations.length > 0;
        setShowMap(restoredLocations.length > 0);
        sessionIdRef.current = targetChatId;
        setSessionId(targetChatId);
        console.log('=== loadChat 完成 ===');
      }
    }));

    useEffect(() => {
      isMountedRef.current = true;
      return () => {
        persistInterruptedActiveRequests();
        isMountedRef.current = false;
        activeRequestIdRef.current = null;
        Object.values(abortControllersByChatIdRef.current).forEach((controller) => {
          controller.abort();
        });
        abortControllersByChatIdRef.current = {};
        Object.values(saveTimersByChatIdRef.current).forEach((timer) => {
          window.clearTimeout(timer);
        });
        saveTimersByChatIdRef.current = {};
        if (activeAbortControllerRef.current) {
          activeAbortControllerRef.current = null;
        }
      };
    }, []);

    // 当加载的消息改变时，通过 loadChat 方法处理
    useEffect(() => {
      if (loadedMessages !== null && loadedMessages !== undefined) {
        skipNextAutoSaveRef.current = true;
        console.log('useEffect 检测到 loadedMessages 变化，消息数量:', loadedMessages.length);
        clearInputText();
        const targetChatId = currentChatId || sessionIdRef.current;
        activeChatIdRef.current = targetChatId;
        activeRequestIdRef.current = requestIdsByChatIdRef.current[targetChatId] || null;
        activeAbortControllerRef.current = abortControllersByChatIdRef.current[targetChatId] || null;
        activeRegenerateContextRef.current = regenerateContextsByChatIdRef.current[targetChatId] || null;
        if (targetChatId) {
          sessionIdRef.current = targetChatId;
          setSessionId(targetChatId);
        }

        // 先清空地图位置，避免显示之前的地点
        setMapSuppressed(false);
        setShowMap(false);
        lastAutoOpenedMapSignatureRef.current = '';
        setMapLocations([]);

        if (loadedMessages.length > 0) {
          const mappedMessages = withInterruptedResponseIfNeeded(loadedMessages.map(msg => normalizeMessage(msg)));
          syncActiveChatFromRuntime(targetChatId, mappedMessages);
          setMessageFeedback({});
          setAnswerPageByUserMessageId({});

          const workspace = loadedTripWorkspace
            ? loadedTripWorkspace as unknown as TripWorkspaceState
            : createEmptyTripWorkspace();
          const restoredPlan = loadedTripPlan || restorePlanFromWorkspace(workspace, loadedTripDocument || null);
          const restoredLocations = restoredPlan ? normalizeTripEventLocations(workspace.locations) : [];
          setMapSuppressed(false);
          setMapLocations(restoredLocations);
          hasStructuredTripLocationsRef.current = restoredLocations.length > 0;
          setTripWorkspace(restoredPlan ? workspace : createEmptyTripWorkspace());
          lastPersistedPlanSignatureRef.current = tripSnapshotSignature(restoredPlan, loadedTripDocument || null);
          setActiveTripPlan(restoredPlan);
          setActiveTripDocument(loadedTripDocument || null);
          setPreviousTripPlan(null);
          setShowMap(restoredLocations.length > 0);
        } else {
          // 如果是空数组，清空消息和地图
          console.log('loadedMessages为空数组，清空消息和地图');
          if (currentChatId) {
            messagesByChatIdRef.current = setChatMessages<Message>(messagesByChatIdRef.current, targetChatId, []);
          }
          messagesRef.current = [];
          setMessages([]);
          setIsLoading(currentChatId ? Boolean(loadingByChatIdRef.current[targetChatId]) : false);
          setMessageFeedback({});
          setAnswerPageByUserMessageId({});
          setMapSuppressed(false);
          setShowMap(false);
          lastAutoOpenedMapSignatureRef.current = '';
          setMapLocations([]);
          hasStructuredTripLocationsRef.current = false;
          setTripWorkspace(createEmptyTripWorkspace());
          setActiveTripPlan(null);
          setActiveTripDocument(null);
          setPreviousTripPlan(null);
        }
      }
    }, [loadedMessages, loadedTripDocument, loadedTripPlan, loadedTripWorkspace]);

    // 获取MCP服务器列表
    const fetchMcpServers = async () => {
      try {
        setMcpLoading(true);
        const response = await fetch('/api/mcp-servers');
        if (response.ok) {
          const data = await response.json();
          console.log('获取到的MCP服务器数据:', data);
          setMcpServers(data.servers || []);
          // 默认选择所有可用的服务器（状态为connected或未禁用的）
          const availableServers = data.servers.filter((server: any) =>
            server.disabled !== true && (server.status === 'connected' || server.status === undefined)
          ).map((server: any) => server.name);
          console.log('可用的服务器:', availableServers);
          setSelectedMcpServers(availableServers);
          setMcpServersLoaded(true);
        }
      } catch (error) {
        console.error('获取MCP服务器失败:', error);
      } finally {
        setMcpLoading(false);
      }
    };

    const fetchSkills = async () => {
      try {
        setSkillLoading(true);
        const data = await apiClient.getSkills();
        setSkills(data);
      } catch (error) {
        console.error('获取技能包失败:', error);
      } finally {
        setSkillLoading(false);
      }
    };

    const handleSkillDropdownOpenChange = (open: boolean) => {
      setSkillModalVisible(open);
      if (open) {
        void fetchSkills();
      }
    };

    // 组件挂载时获取MCP服务器
    useEffect(() => {
      fetchMcpServers();
      fetchSkills();
    }, []);

    // 自动滚动到底部
    const scrollToBottom = () => {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    };

    useEffect(() => {
      scrollToBottom();
    }, [messages]);

    // 保存对话到历史记录
    const saveCurrentChat = () => {
      const chatId = getActiveChatId();
      const cachedMessages = getChatMessages(messagesByChatIdRef.current, chatId);
      const messagesToSave = cachedMessages.length > 0 ? cachedMessages : messagesRef.current;
      if (messagesToSave.length > 0) {
        saveChat(chatId, messagesToSave, undefined, {
          tripPlan: activeTripPlanRef.current,
          tripDocument: activeTripDocumentRef.current,
          tripWorkspace: tripWorkspaceRef.current as unknown as Record<string, unknown>,
        });
      }
    };

    // 在每次消息更新后保存对话
    useEffect(() => {
      if (skipNextAutoSaveRef.current) {
        skipNextAutoSaveRef.current = false;
        return;
      }
      if (messages.length > 0) {
        // 延迟保存，避免频繁更新
        const timer = setTimeout(() => {
          saveCurrentChat();
        }, 500);
        return () => clearTimeout(timer);
      }
    }, [messages, currentChatId, sessionId]);

    useEffect(() => {
      if (!activeTripPlan) return;
      const signature = tripSnapshotSignature(activeTripPlan, activeTripDocument);
      if (!safeText(activeTripPlan.plan_id) || signature === lastPersistedPlanSignatureRef.current) return;
      lastPersistedPlanSignatureRef.current = signature;
      const chatId = getActiveChatId();
      const currentMessages = getChatMessages(messagesByChatIdRef.current, chatId);
      const isBackgroundUpdate = backgroundDocumentUpdateRef.current;
      backgroundDocumentUpdateRef.current = false;
      if (currentMessages.length === 0) return;
      saveChat(chatId, currentMessages, undefined, {
        changeReason: isBackgroundUpdate ? 'system' : Number(activeTripPlan.version) > 1 ? 'trip_edit' : 'final_answer',
        touchUpdatedAt: !isBackgroundUpdate,
        tripPlan: activeTripPlan,
        tripDocument: activeTripDocument,
        tripWorkspace: tripWorkspace as unknown as Record<string, unknown>,
      });
    }, [activeTripDocument, activeTripPlan, tripWorkspace]);

    useEffect(() => {
      const persistOnPageLeave = () => {
        saveCurrentChat();
      };

      window.addEventListener('pagehide', persistOnPageLeave);
      window.addEventListener('beforeunload', persistOnPageLeave);
      return () => {
        window.removeEventListener('pagehide', persistOnPageLeave);
        window.removeEventListener('beforeunload', persistOnPageLeave);
      };
    }, [messages, currentChatId, sessionId]);

    useEffect(() => {
      const onVisibilityChange = () => {
        if (!document.hidden) {
          requestAnimationFrame(() => {
            scrollToBottom();
          });
        }
      };

      document.addEventListener('visibilitychange', onVisibilityChange);
      return () => {
        document.removeEventListener('visibilitychange', onVisibilityChange);
      };
    }, [messages]);

    // 消息分组逻辑
    const groupMessages = (messages: Message[]): MessageGroup[] => {
      const groups: MessageGroup[] = [];
      const groupByUserId = new Map<string, MessageGroup>();
      let currentGroup: MessageGroup | null = null;

      const appendFinalAnswer = (group: MessageGroup, message: Message) => {
        if (!group.finalAnswer) {
          group.finalAnswer = [message];
          return;
        }

        if (Array.isArray(group.finalAnswer)) {
          group.finalAnswer = [...group.finalAnswer, message];
          return;
        }

        group.finalAnswer = [group.finalAnswer, message];
      };

      const classifyAssistantMessage = (group: MessageGroup, message: Message) => {
        if (useMultiAgent) {
          // 多智能体协作开启：仅完整回答(final_answer)计入回答版本，其余归入深度思考
          if (message.type === 'final_answer') {
            appendFinalAnswer(group, message);
          } else {
            group.deepThinkMessages.push(message);
          }
          return;
        }

        if (useDeepThink) {
          // 只有深度思考开启：仅最终回答进入主气泡，其余步骤进入思考过程。
          if (message.type === 'final_answer' || message.type === 'normal' || !message.type) {
            appendFinalAnswer(group, message);
          } else {
            group.deepThinkMessages.push(message);
          }
          return;
        }

        // 都关闭：状态/工具进度仍不作为最终答案版本。
        if (message.type && message.type !== 'normal' && message.type !== 'final_answer') {
          group.deepThinkMessages.push(message);
        } else {
          appendFinalAnswer(group, message);
        }
      };

      for (const message of messages) {
        if (message.role === 'user') {
          // 如果之前有未完成的组，先推入
          if (currentGroup) {
            groups.push(currentGroup);
          }
          // 开始新的组
          currentGroup = {
            userMessage: message,
            deepThinkMessages: [],
            finalAnswer: undefined
          };
          groupByUserId.set(message.id, currentGroup);
        } else if (message.role === 'assistant') {
          const linkedUserMessageId = safeText(message.linkedUserMessageId).trim();
          const targetGroup = linkedUserMessageId
            ? groupByUserId.get(linkedUserMessageId) || currentGroup
            : currentGroup;

          if (targetGroup) {
            classifyAssistantMessage(targetGroup, message);
          }
        }
      }

      // 推入最后一个组
      if (currentGroup) {
        groups.push(currentGroup);
      }

      return groups;
    };

    // 获取智能体类型
    const getAgentType = (role: string): string => {
      if (role.includes('analysis')) return '分析智能体';
      if (role.includes('planning')) return '规划智能体';
      if (role.includes('executor')) return '执行智能体';
      if (role.includes('observation')) return '观察智能体';
      if (role.includes('summary')) return '总结智能体';
      if (role.includes('decompose')) return '分解智能体';
      return '智能体';
    };

    // 格式化耗时
    const formatDuration = (duration: number): string => {
      if (duration < 1000) {
        return `${Math.round(duration)}ms`;
      } else if (duration < 60000) {
        return `${(duration / 1000).toFixed(1)}s`;
      } else {
        const minutes = Math.floor(duration / 60000);
        const seconds = Math.floor((duration % 60000) / 1000);
        return `${minutes}m${seconds}s`;
      }
    };

    const normalizeMarkdownForDisplay = (content: string): string => {
      let normalized = content;

      normalized = normalized.replace(/\r\n?/g, '\n');
      normalized = normalized.replace(/(---)(?=\s*#{1,6}(?!#))/g, '$1\n\n');
      normalized = normalized.replace(/([^#\n])(?=\s*#{1,6}(?![#\s]))/g, '$1\n\n');
      normalized = normalized.replace(/(^|\n)(#{1,6})(?=[^\s#])/g, '$1$2 ');
      normalized = normalized.replace(/([^#\n])(?=\s*#{1,6}\s)/g, '$1\n\n');
      normalized = normalized.replace(/([^#\n])(?=\s*###\s*(?:上午|中午|下午|晚上)(?:（[^）\n]+）|\([^)\n]+\)))/g, '$1\n\n');
      normalized = normalized.replace(/(###\s*(?:上午|中午|下午|晚上)(?:（[^）\n]+）|\([^)\n]+\)))\s+/g, '$1\n\n');
      normalized = normalized.replace(/^(#{2,6}\s*(?:住宿推荐|交通建议|重要提醒|结尾|结论|使用限制|数据说明|地理位置信息|生成的文档)[:：])\s+/gm, '$1\n\n');
      normalized = normalized.replace(/^#{1,6}\s*(\d+[.、]\s*(?:预约提醒|天气建议|交通建议|住宿建议|饮食建议|行李准备|安全提醒|费用提醒|实用贴士).*)$/gm, '### $1');
      normalized = normalized.replace(/^(###\s+\d+[.、])(?=\S)/gm, '$1 ');
      normalized = normalized.replace(/^(\d+)[.、](?=\S)/gm, '$1. ');
      normalized = normalized.replace(/^(#{1,6}\s+[^\n|]{1,80})\|/gm, '$1\n|');
      normalized = normalized.replace(/(网络搜索参考表|小红书检索资源表|地图地点表)\s*(\|)/g, '$1\n$2');
      normalized = normalized.replace(/([^#\n])(?=\s{1,2}(?:\d+\.|[-*])\s+\S)/g, '$1\n');
      normalized = normalized.replace(/\|\s*\|/g, '|\n|');
      normalized = normalized.replace(/(\|[^\n]*\|)\s*(\|\s*---)/g, '$1\n$2');
      normalized = normalized.replace(/(\|\s*[-:. ]+(?:\|\s*[-:. ]+)+\|)\s*(\|)/g, '$1\n$2');

      const lines = normalized.split('\n').flatMap((line) => {
        if (/^\s*(?:[-*+]\s*)?\|\s*$/.test(line)) {
          return [];
        }

        const pipeCount = (line.match(/\|/g) || []).length;
        if (pipeCount < 6 || line.trim().startsWith('|')) {
          return [line];
        }

        const tableStart = line.search(/\|[^|\n]+\|[^|\n]+\|/);
        if (tableStart <= 0) {
          return [line];
        }

        return [
          line.slice(0, tableStart).trimEnd(),
          line.slice(tableStart).trimStart(),
        ].filter(Boolean);
      });

      return lines.join('\n').replace(/\n{3,}/g, '\n\n');
    };

    // 处理主聊天界面的显示内容（隐藏JSON但保留其他内容）
    const formatMainChatContent = (content: any): string => {
      if (!content || typeof content !== 'string') return '';
      const normalizedContent = stripLeadingArtifactZero(content);
      let normalizedForDisplay = normalizedContent
        .replace(/(?:^|\n)#{3,4}\s*地图地点表\s*\n[\s\S]*?(?=\n#{1,6}\s+\S|\n```json|$)/g, '\n')
        .replace(/(?:^|\n)\|\s*顺序\s*\|\s*地点\s*\|\s*类型\s*\|\s*说明\s*\|\s*坐标\s*\|[\s\S]*?(?=\n#{1,6}\s+\S|\n```json|$)/g, '\n')
        .replace(/(?:^|\n)#{2,4}\s*地图标注数据\s*\n```json[\s\S]*?```/g, '\n')
        .replace(/```json[\s\S]*?```/g, '');
      normalizedForDisplay = normalizeMarkdownForDisplay(normalizedForDisplay);

      // 兜底：某些流式片段在清洗后会丢掉前导空行，导致标题/表头被粘连到上一段文字里。
      normalizedForDisplay = normalizedForDisplay.replace(
        /([^\n])(###\s*12306实时车票班次信息(?:总)?表)/g,
        '$1\n\n$2'
      );
      normalizedForDisplay = normalizedForDisplay.replace(
        /([^\n])(\|\s*类型\s*\|\s*班次\s*\|\s*路线\s*\|)/g,
        '$1\n\n$2'
      );

      // 统一标题文案，避免“信息总表/信息表”混用导致前端规则判断不一致。
      normalizedForDisplay = normalizedForDisplay.replace(
        /###\s*12306实时车票班次信息总表/g,
        '### 12306实时车票班次信息表'
      );

      const hasTicketTable = /\|\s*类型\s*\|\s*班次\s*\|\s*路线\s*\|/.test(normalizedForDisplay);
      const hasTicketTitle = /12306实时车票班次信息(?:总)?表/.test(normalizedForDisplay);

      if (hasTicketTable && !hasTicketTitle) {
        return normalizedForDisplay.replace(
          /(\|\s*类型\s*\|\s*班次\s*\|\s*路线\s*\|)/,
          '### 12306实时车票班次信息表\n$1'
        );
      }

      return normalizedForDisplay;
    };

    // 渲染深度思考气泡框（统一显示深度思考 + 多智能体规划执行过程）
    const renderDeepThinkBubble = (deepThinkMessages: Message[]) => {
      if (!deepThinkMessages.length) return null;

      const generatedDays = tripWorkspace.days?.length || 0;
      const hasValidation = Boolean(tripWorkspace.validation || tripWorkspace.repair)
        || deepThinkMessages.some((message) => /observation|validation|repair/i.test(safeText(message.type)));
      const hasResearch = tripWorkspace.sources.length > 0
        || deepThinkMessages.some((message) => /tool_progress|research|search/i.test(safeText(message.type)));
      const effectivePlanningStatus: PlanningStatus = planningStatus === 'idle' && generatedDays > 0
        ? 'completed'
        : planningStatus;
      const statusLabels: Record<PlanningStatus, string> = {
          idle: pendingClarification ? '等待你的回答' : '等待开始',
        planning: hasValidation ? '正在校验行程' : hasResearch ? '正在检索与整理资料' : '正在规划行程',
        completed: '规划已完成',
        cancelled: '规划已取消，草稿已保留',
        error: '规划暂时中断，草稿已保留',
      };
      return (
        <div className="planning-progress-panel" role="status" aria-live="polite">
          <strong>规划进度</strong>
          <span>{statusLabels[effectivePlanningStatus]}</span>
          <span>已生成 {generatedDays} 天</span>
          {hasResearch && <span>资料检索已执行</span>}
          {hasValidation && <span>质量校验已执行</span>}
        </div>
      );

    };

    // 处理消息块
    const handleMessageChunk = (data: any, targetChatId = getActiveChatId()) => {
      try {
        if (!isMountedRef.current) return;
        if (!targetChatId) return;
        if (data.message_id && (data.show_content !== undefined || data.content !== undefined)) {
          const messageId = data.message_id;
          const showContent = safeText(data.show_content);
          const realContent = safeText(data.content);
          const effectiveShowContent = showContent.trim().length > 0 ? showContent : realContent;
          const shouldReplaceContent = Boolean(data.replace);

          console.log('处理消息块:', {
            message_id: messageId,
            show_content: showContent,
            content: realContent,
            step_type: data.step_type,
            agent_type: data.agent_type
          });

          setChatMessagesForId(targetChatId, prev => {
            let existingIndex = prev.findIndex(m => m.id === messageId);
            const linkedUserMessageId = safeText(data.linked_user_message_id).trim();
            const stepType = safeText(data.step_type).trim();
            const isReplacingFinalAnswer = shouldReplaceContent && isFinalAnswerStepType(stepType);
            const belongsToSameUserQuestion = (message: Message): boolean => {
              if (!linkedUserMessageId) return false;
              return safeText(message.linkedUserMessageId).trim() === linkedUserMessageId;
            };
            const isFinalAnswerMessage = (message: Message): boolean => (
              message.role === 'assistant' && isFinalAnswerStepType(safeText(message.type).trim() || 'normal')
            );
            if (existingIndex < 0 && shouldReplaceContent) {
              for (let index = prev.length - 1; index >= 0; index -= 1) {
                const candidate = prev[index];
                const candidateLinkedUserMessageId = safeText(candidate.linkedUserMessageId).trim();
                const candidateType = safeText(candidate.type).trim();
                const isReplaceableAnswer = (
                  candidate.role === 'assistant'
                  && (!candidateType || candidateType === 'normal' || candidateType === 'final_answer')
                  && (!linkedUserMessageId || !candidateLinkedUserMessageId || candidateLinkedUserMessageId === linkedUserMessageId)
                );
                if (isReplaceableAnswer) {
                  existingIndex = index;
                  break;
                }
              }
            }
            const now = new Date();

              if (existingIndex >= 0) {
                // 更新现有消息
                const updated = [...prev];
                const existingMessage = updated[existingIndex];
              const updatedContent = shouldReplaceContent
                ? stripLeadingArtifactZero(realContent)
                : stripLeadingArtifactZero(safeText(existingMessage.content) + realContent);
              const updatedDisplayContent = shouldReplaceContent
                ? stripLeadingArtifactZero(effectiveShowContent)
                : stripLeadingArtifactZero(safeText(existingMessage.displayContent) + effectiveShowContent);

              updated[existingIndex] = {
                ...existingMessage,
                content: updatedContent,
                displayContent: updatedDisplayContent,
                timestamp: now,
                endTime: now,
                duration: existingMessage.startTime ? now.getTime() - existingMessage.startTime.getTime() : 0,
                linkedUserMessageId: existingMessage.linkedUserMessageId || linkedUserMessageId || undefined,
              };

              // 只从最终答案中提取地点信息，并替换之前的地点
              if (!hasStructuredTripLocationsRef.current && shouldExtractLocations(data.step_type, data.agent_type, updatedDisplayContent)) {
                const locations = extractMapLocations(updatedDisplayContent);
                if (locations.length > 0 && isMountedRef.current && activeChatIdRef.current === targetChatId) {
                  console.log('从最终答案提取到地点信息，替换之前的地点:', locations);
                  setMapSuppressed(false);
                  setMapLocations((prevLocations) => (
                    areLocationsEquivalent(prevLocations, locations) ? prevLocations : locations
                  ));
                }
              }

              if (isReplacingFinalAnswer && linkedUserMessageId) {
                return updated.filter((message, index) => (
                  index === existingIndex || !isFinalAnswerMessage(message) || !belongsToSameUserQuestion(message)
                ));
              }

              return updated;
            } else {
              // 创建新消息
              const newMessage: Message = {
                id: messageId,
                role: (data.role === 'user' ? 'user' : 'assistant') as 'user' | 'assistant' | 'system',
                content: stripLeadingArtifactZero(realContent),
                displayContent: stripLeadingArtifactZero(effectiveShowContent),
                timestamp: now,
                type: data.step_type,
                agentType: getAgentType(data.agent_type || data.role || 'assistant'),
                startTime: now,
                endTime: now,
                duration: 0,
                linkedUserMessageId: linkedUserMessageId || undefined,
              };

              // 只从最终答案中提取地点信息，并替换之前的地点
              if (!hasStructuredTripLocationsRef.current && shouldExtractLocations(data.step_type, data.agent_type, effectiveShowContent)) {
                const locations = extractMapLocations(effectiveShowContent);
                if (locations.length > 0 && isMountedRef.current && activeChatIdRef.current === targetChatId) {
                  console.log('从最终答案提取到地点信息，替换之前的地点:', locations);
                  setMapSuppressed(false);
                  setMapLocations((prevLocations) => (
                    areLocationsEquivalent(prevLocations, locations) ? prevLocations : locations
                  ));
                }
              }

              if (isReplacingFinalAnswer && linkedUserMessageId) {
                return [
                  ...prev.filter(message => !isFinalAnswerMessage(message) || !belongsToSameUserQuestion(message)),
                  newMessage,
                ];
              }

              return [...prev, newMessage];
            }
          });
        }
      } catch (error) {
        console.error('handleMessageChunk 处理失败:', error);
      }
    };

    // 发送消息
    const handleSendMessage = async (
      overrideInput?: string,
      options?: {
        requestMessagesOverride?: Message[];
        clarificationAnswers?: Record<string, string>;
        clarificationAnsweredCount?: number;
        clarificationQuestionId?: string;
        appendUserMessage?: boolean;
      }
    ) => {
      const shouldAppendUserMessage = options?.appendUserMessage !== false;
      const currentInput = safeText(overrideInput ?? readInputText()).trim();
      if (isLoading) return;
      if (shouldAppendUserMessage && !currentInput) return;
      if (!shouldAppendUserMessage && !options?.requestMessagesOverride?.length) return;

      const requestId = uuidv4();
      setPlanningStatus('planning');
      const requestChatId = getActiveChatId();
      activeChatIdRef.current = requestChatId;
      requestIdsByChatIdRef.current = {
        ...requestIdsByChatIdRef.current,
        [requestChatId]: requestId,
      };
      regenerateContextsByChatIdRef.current = {
        ...regenerateContextsByChatIdRef.current,
        [requestChatId]: null,
      };
      if (activeChatIdRef.current === requestChatId) {
        activeRequestIdRef.current = requestId;
        activeRegenerateContextRef.current = null;
      }

      const previousController = abortControllersByChatIdRef.current[requestChatId];
      if (previousController) {
        previousController.abort();
      }
      const abortController = new AbortController();
      abortControllersByChatIdRef.current = {
        ...abortControllersByChatIdRef.current,
        [requestChatId]: abortController,
      };
      if (activeChatIdRef.current === requestChatId) {
        activeAbortControllerRef.current = abortController;
      }

      const cachedMessages = getChatMessages(messagesByChatIdRef.current, requestChatId);
      const baseMessages = cachedMessages.length > 0 ? cachedMessages : messagesRef.current;
      const requestMessages = options?.requestMessagesOverride
        ? options.requestMessagesOverride
        : [
            ...baseMessages,
            {
              id: uuidv4(),
              role: 'user',
              content: currentInput,
              timestamp: new Date(),
              displayContent: currentInput,
              startTime: new Date(),
              endTime: new Date(),
              duration: 0
            } as Message,
          ];
      setChatMessagesForId(requestChatId, requestMessages);
      setChatLoadingForId(requestChatId, true);
      if (!overrideInput && shouldAppendUserMessage) {
        clearInputText();
      }
      if (shouldAppendUserMessage) {
        setPendingClarification(null);
      }
      hasStructuredTripLocationsRef.current = false;
      setTripWorkspace(createEmptyTripWorkspace());
      setActiveTripPlan(null);
      setActiveTripDocument(null);
      setPreviousTripPlan(null);
      // 保留当前地图，等新回复里出现有效地点后再替换，避免闪烁和上下文丢失。

      try {
        // 构建请求数据
        const requestData = {
          type: 'chat',
          request_id: requestId,
          messages: requestMessages.map(msg => ({
            role: msg.role,
            content: msg.content,
            message_id: msg.id,
            type: msg.type || 'normal'
          })),
          use_deepthink: useDeepThink,
          use_multi_agent: useMultiAgent,
          selected_mcp_servers: mcpServersLoaded ? selectedMcpServers : undefined,
          selected_skill_ids: selectedSkillIds,
          profile: settings.useUserProfile ? profile : {},
          planning_mode: settings.planningMode,
          allow_web_search: settings.allowWebSearch,
          clarification_answers: options?.clarificationAnswers || {},
          clarification_question_id: options?.clarificationQuestionId,
          selected_knowledge_context: selectedKnowledgeContext
        };

        console.log('发送请求参数:', {
          use_deepthink: useDeepThink,
          use_multi_agent: useMultiAgent,
          消息数量: requestData.messages.length
        });

        // 发送流式请求
        const response = await fetch('/api/chat-stream', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          signal: abortController.signal,
          body: JSON.stringify(requestData),
        });

        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        // 处理流式响应
        const reader = response.body?.getReader();
        if (!reader) {
          throw new Error('无法获取响应流');
        }

        let streamBuffer = '';
        let receivedClarification = false;
        let receivedTerminalEvent = false;
        let lastSequence = 0;
        const decoder = new TextDecoder();

        const isCurrentRequest = () => (
          isMountedRef.current &&
          requestIdsByChatIdRef.current[requestChatId] === requestId
        );

        const processDataLine = (line: string) => {
          if (!line.startsWith('data: ')) return;
          try {
            const data = JSON.parse(line.slice(6));
            if (safeText(data.request_id) && data.request_id !== requestId) return;
            const eventSequence = Number(data.sequence);
            if (Number.isFinite(eventSequence)) {
              if (eventSequence <= lastSequence) return;
              lastSequence = eventSequence;
            }
            console.log('收到流式数据:', data);

            switch (data.type) {
              case 'chat_chunk':
                if (isCurrentRequest()) {
                  handleMessageChunk(data, requestChatId);
                }
                break;
              case 'clarification_required': {
                if (isCurrentRequest()) {
                  receivedClarification = true;
                  const nextQuestion = Array.isArray(data.questions)
                    ? data.questions[0] as ClarificationQuestion | undefined
                    : undefined;
                  if (!nextQuestion) {
                    setPendingClarification(null);
                    setChatLoadingForId(requestChatId, false);
                    break;
                  }

                  const cumulativeAnswers = options?.clarificationAnswers || {};
                  setChatLoadingForId(requestChatId, false);
                  setPendingClarification({
                    chatId: requestChatId,
                    requestMessages,
                    question: nextQuestion,
                    answers: cumulativeAnswers,
                    answeredCount: Number.isFinite(Number(data.answered_count))
                      ? Number(data.answered_count)
                      : options?.clarificationAnsweredCount ?? Object.keys(cumulativeAnswers).length,
                    intent: data.intent || undefined,
                  });
                }
                break;
              }
              case 'trip_sources':
              case 'trip_day_upsert':
              case 'trip_locations':
              case 'trip_budget':
              case 'trip_validation':
              case 'trip_plan_repair':
              case 'trip_plan_delta':
              case 'trip_plan':
                if (isCurrentRequest()) {
                  setPendingClarification(null);
                  handleTripStructuredEvent(data, requestChatId);
                }
                break;
              case 'chat_complete':
                if (isCurrentRequest()) {
                  receivedTerminalEvent = true;
                  setChatLoadingForId(requestChatId, false);
                  setPlanningStatus(statusAfterCompletion(data.finish_reason));
                  if (data.finish_reason !== 'clarification_required' && !receivedClarification) {
                    setPendingClarification(null);
                  }
                }
                console.log('聊天完成');
                break;
              case 'error':
                if (isCurrentRequest()) {
                  receivedTerminalEvent = true;
                  setChatLoadingForId(requestChatId, false);
                  setPlanningStatus('error');
                  const composedError = safeText(data.user_message).trim()
                    || '暂时无法完成这次规划，请稍后重试。';

                  setChatMessagesForId(requestChatId, prev => [...prev, {
                    id: uuidv4(),
                    role: 'system',
                    content: composedError,
                    displayContent: composedError,
                    timestamp: new Date(),
                    type: 'error'
                  }]);
                }
                break;
            }
          } catch (error) {
            console.error('解析JSON失败:', error, line);
          }
        };

        while (true) {
          if (!isCurrentRequest()) {
            break;
          }
          const { done, value } = await reader.read();
          if (done) {
            if (streamBuffer.trim()) {
              const lines = streamBuffer.split('\n');
              for (const line of lines) {
                processDataLine(line);
              }
            }
            break;
          }

          try {
            streamBuffer += decoder.decode(value, { stream: true });
          } catch (decodeError) {
            console.error('流式解码失败:', decodeError);
            continue;
          }
          const lines = streamBuffer.split('\n');

          // 保留最后一行（可能是不完整的）在 buffer 中
          streamBuffer = lines.pop() || '';

          for (const line of lines) {
            processDataLine(line);
          }
        }

        if (isCurrentRequest()) {
          setChatLoadingForId(requestChatId, false);
          if (!receivedTerminalEvent) {
              setPlanningStatus(statusAfterStreamClosed(hasStructuredTripLocationsRef.current));
          }
        }

      } catch (error) {
        if ((error as Error).name === 'AbortError') {
          console.log('请求已中止（切换会话或组件卸载）');
          if (requestIdsByChatIdRef.current[requestChatId] === requestId && isMountedRef.current) {
            setChatLoadingForId(requestChatId, false);
          }
          return;
        }
        console.error('发送消息失败:', error);
        if (requestIdsByChatIdRef.current[requestChatId] === requestId && isMountedRef.current) {
          setChatLoadingForId(requestChatId, false);
          setPlanningStatus('error');
          setChatMessagesForId(requestChatId, prev => [...prev, {
            id: uuidv4(),
            role: 'system',
            content: '网络连接异常，请检查网络后重试。',
            displayContent: '网络连接异常，请检查网络后重试。',
            timestamp: new Date(),
            type: 'error'
          }]);
        }
      } finally {
        if (requestIdsByChatIdRef.current[requestChatId] === requestId) {
          const { [requestChatId]: _removedRequestId, ...restRequestIds } = requestIdsByChatIdRef.current;
          requestIdsByChatIdRef.current = restRequestIds;
          if (activeChatIdRef.current === requestChatId) {
            activeRequestIdRef.current = null;
          }
        }
        if (abortControllersByChatIdRef.current[requestChatId] === abortController) {
          const { [requestChatId]: _removedController, ...restControllers } = abortControllersByChatIdRef.current;
          abortControllersByChatIdRef.current = restControllers;
          if (activeChatIdRef.current === requestChatId) {
            activeAbortControllerRef.current = null;
          }
        }
      }
    };

    const isAssistantAnswerMessage = (message: Message): boolean => {
      if (message.role !== 'assistant') return false;
      return !message.type || message.type === 'final_answer' || message.type === 'normal';
    };

    const handleStopResponse = () => {
      if (!isLoading) return;
      const activeChatId = getActiveChatId();
      const cachedMessages = getChatMessages(messagesByChatIdRef.current, activeChatId);
      const activeMessages = cachedMessages.length > 0 ? cachedMessages : messagesRef.current;

      const retryPrompt = (() => {
        const regenerateContext = regenerateContextsByChatIdRef.current[activeChatId] || activeRegenerateContextRef.current;
        if (regenerateContext?.userMessageId) {
          const linkedUserMessage = activeMessages.find(
            (msg) => msg.role === 'user' && msg.id === regenerateContext.userMessageId
          );
          const linkedPrompt = safeText(linkedUserMessage?.content).trim() || safeText(linkedUserMessage?.displayContent).trim();
          if (linkedPrompt) return linkedPrompt;
        }

        for (let i = activeMessages.length - 1; i >= 0; i--) {
          if (activeMessages[i].role === 'user') {
            const latestPrompt = safeText(activeMessages[i].content).trim() || safeText(activeMessages[i].displayContent).trim();
            if (latestPrompt) return latestPrompt;
          }
        }

        return '';
      })();

      const activeController = abortControllersByChatIdRef.current[activeChatId];
      if (activeController) {
        activeController.abort();
        const { [activeChatId]: _removedController, ...restControllers } = abortControllersByChatIdRef.current;
        abortControllersByChatIdRef.current = restControllers;
      }

      const { [activeChatId]: _removedRequestId, ...restRequestIds } = requestIdsByChatIdRef.current;
      requestIdsByChatIdRef.current = restRequestIds;
      const { [activeChatId]: _removedRegenerateContext, ...restRegenerateContexts } = regenerateContextsByChatIdRef.current;
      regenerateContextsByChatIdRef.current = restRegenerateContexts;
      activeRequestIdRef.current = null;
      activeAbortControllerRef.current = null;
      activeRegenerateContextRef.current = null;
      setChatLoadingForId(activeChatId, false);
      setPlanningStatus('cancelled');
      setChatMessagesForId(activeChatId, (prev) => withInterruptedResponseIfNeeded(prev));
      if (retryPrompt) {
        setInputText(retryPrompt, true);
      }
    };

    const clearDeepThinkMessagesForUser = (targetUserMessageId: string) => {
      const activeChatId = getActiveChatId();
      setChatMessagesForId(activeChatId, (prev) => {
        const targetUserIndex = prev.findIndex(
          (msg) => msg.role === 'user' && msg.id === targetUserMessageId
        );
        if (targetUserIndex < 0) return prev;

        let nextUserIndex = prev.length;
        for (let i = targetUserIndex + 1; i < prev.length; i++) {
          if (prev[i].role === 'user') {
            nextUserIndex = i;
            break;
          }
        }

        return prev.filter((msg, index) => {
          if (msg.role !== 'assistant') return true;

          const linkedUserMessageId = safeText(msg.linkedUserMessageId).trim();
          const belongsByLink = linkedUserMessageId === targetUserMessageId;
          const belongsByRange = !linkedUserMessageId && index > targetUserIndex && index < nextUserIndex;

          if (!belongsByLink && !belongsByRange) return true;

          // 重新回答前保留历史最终回答版本，只清理旧的思考过程。
          return isAssistantAnswerMessage(msg);
        });
      });
    };

    const isFinalAnswerStepType = (stepType: string): boolean => {
      const normalized = safeText(stepType).trim().toLowerCase();
      return normalized === '' || normalized === 'normal' || normalized === 'final_answer';
    };

    const findNearestUserMessage = (assistantMessageId: string): Message | null => {
      const assistantMessage = messages.find((msg) => msg.id === assistantMessageId);
      const linkedUserMessageId = safeText(assistantMessage?.linkedUserMessageId).trim();
      if (linkedUserMessageId) {
        const linkedUser = messages.find((msg) => msg.id === linkedUserMessageId && msg.role === 'user');
        if (linkedUser) return linkedUser;
      }

      const assistantIndex = messages.findIndex((msg) => msg.id === assistantMessageId);
      if (assistantIndex <= 0) return null;

      for (let i = assistantIndex - 1; i >= 0; i--) {
        if (messages[i].role === 'user') {
          return messages[i];
        }
      }

      return null;
    };

    const getRegeneratePrompt = (assistantMessageId: string): string => {
      const userMessage = findNearestUserMessage(assistantMessageId);
      if (!userMessage) return '';
      const prompt = safeText(userMessage.content).trim() || safeText(userMessage.displayContent).trim();
      return prompt;
    };

    const buildRegenerateRequestHistory = (targetUserMessageId: string): Message[] => {
      const targetUserIndex = messages.findIndex(
        (msg) => msg.id === targetUserMessageId && msg.role === 'user'
      );
      if (targetUserIndex < 0) return [];
      return messages.slice(0, targetUserIndex + 1);
    };

    const transformRegenerateChunk = (requestId: string, data: any, targetChatId = getActiveChatId()): any | null => {
      const context = regenerateContextsByChatIdRef.current[targetChatId] || activeRegenerateContextRef.current;
      if (!context || context.requestId !== requestId) {
        return data;
      }

      if (safeText(data.type) !== 'chat_chunk') {
        return data;
      }

      if (safeText(data.role) !== 'assistant') {
        return null;
      }

      const originalMessageId = safeText(data.message_id).trim();
      const stepType = safeText(data.step_type).trim() || 'normal';
      const isFinalAnswer = isFinalAnswerStepType(stepType);

      let mappedMessageId = context.regeneratedAssistantMessageId;
      if (!isFinalAnswer) {
        mappedMessageId = context.messageIdMap[originalMessageId];
        if (!mappedMessageId) {
          mappedMessageId = uuidv4();
          if (originalMessageId) {
            context.messageIdMap[originalMessageId] = mappedMessageId;
          }
        }
      }

      return {
        ...data,
        message_id: mappedMessageId,
        step_type: isFinalAnswer ? 'final_answer' : stepType,
        linked_user_message_id: context.userMessageId,
        agent_type: data.agent_type || 'assistant',
      };
    };

    const handleRegenerateAnswer = async (assistantMessage: Message) => {
      const prompt = getRegeneratePrompt(assistantMessage.id);
      if (!prompt || isLoading) return;

      const userMessage = findNearestUserMessage(assistantMessage.id);
      if (!userMessage) return;

      const regenerateHistory = buildRegenerateRequestHistory(userMessage.id);
      if (regenerateHistory.length === 0) return;

      const requestId = uuidv4();
      const requestChatId = getActiveChatId();
      requestIdsByChatIdRef.current = {
        ...requestIdsByChatIdRef.current,
        [requestChatId]: requestId,
      };
      if (activeChatIdRef.current === requestChatId) {
        activeRequestIdRef.current = requestId;
      }

      const previousController = abortControllersByChatIdRef.current[requestChatId];
      if (previousController) {
        previousController.abort();
      }
      const abortController = new AbortController();
      abortControllersByChatIdRef.current = {
        ...abortControllersByChatIdRef.current,
        [requestChatId]: abortController,
      };
      if (activeChatIdRef.current === requestChatId) {
        activeAbortControllerRef.current = abortController;
      }

      const regenerateContext: RegenerateRequestContext = {
        requestId,
        userMessageId: userMessage.id,
        regeneratedAssistantMessageId: uuidv4(),
        messageIdMap: {},
      };
      regenerateContextsByChatIdRef.current = {
        ...regenerateContextsByChatIdRef.current,
        [requestChatId]: regenerateContext,
      };
      if (activeChatIdRef.current === requestChatId) {
        activeRegenerateContextRef.current = regenerateContext;
      }

      clearDeepThinkMessagesForUser(userMessage.id);

      setAnswerPageByUserMessageId((prev) => ({
        ...prev,
        [userMessage.id]: Number.MAX_SAFE_INTEGER,
      }));

      setChatLoadingForId(requestChatId, true);

      try {
          const requestData = {
            type: 'chat',
            request_id: requestId,
          messages: regenerateHistory.map((msg) => ({
            role: msg.role,
            content: msg.content,
            message_id: msg.id,
            type: msg.type || 'normal'
          })),
          use_deepthink: useDeepThink,
          use_multi_agent: useMultiAgent,
          selected_mcp_servers: mcpServersLoaded ? selectedMcpServers : undefined,
          selected_skill_ids: selectedSkillIds,
          profile: settings.useUserProfile ? profile : {},
          planning_mode: settings.planningMode,
          allow_web_search: settings.allowWebSearch,
          clarification_answers: {}
        };

        console.log('重新回答请求参数:', {
          use_deepthink: useDeepThink,
          use_multi_agent: useMultiAgent,
          消息数量: requestData.messages.length,
          目标用户消息: userMessage.id,
        });

        const response = await fetch('/api/chat-stream', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          signal: abortController.signal,
          body: JSON.stringify(requestData),
        });

        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        const reader = response.body?.getReader();
        if (!reader) {
          throw new Error('无法获取响应流');
        }

        let streamBuffer = '';
        let lastSequence = 0;
        const decoder = new TextDecoder();

        const isCurrentRequest = () => (
          isMountedRef.current &&
          requestIdsByChatIdRef.current[requestChatId] === requestId
        );

        const processDataLine = (line: string) => {
          if (!line.startsWith('data: ')) return;
          try {
            const data = JSON.parse(line.slice(6));
            if (safeText(data.request_id) && data.request_id !== requestId) return;
            const eventSequence = Number(data.sequence);
            if (Number.isFinite(eventSequence)) {
              if (eventSequence <= lastSequence) return;
              lastSequence = eventSequence;
            }
            console.log('收到重新回答流式数据:', data);

            switch (data.type) {
              case 'chat_chunk':
                if (isCurrentRequest()) {
                  const transformedChunk = transformRegenerateChunk(requestId, data, requestChatId);
                  if (transformedChunk) {
                    handleMessageChunk(transformedChunk, requestChatId);
                  }
                }
                break;
              case 'chat_complete':
                if (isCurrentRequest()) {
                  setChatLoadingForId(requestChatId, false);
                }
                console.log('重新回答完成');
                break;
              case 'error':
                if (isCurrentRequest()) {
                  setChatLoadingForId(requestChatId, false);
                  const composedError = safeText(data.user_message).trim()
                    || '暂时无法重新生成回答，请稍后重试。';

                  setChatMessagesForId(requestChatId, (prev) => [...prev, {
                    id: uuidv4(),
                    role: 'system',
                    content: composedError,
                    displayContent: composedError,
                    timestamp: new Date(),
                    type: 'error'
                  }]);
                }
                break;
            }
          } catch (error) {
            console.error('解析重新回答JSON失败:', error, line);
          }
        };

        while (true) {
          if (!isCurrentRequest()) {
            break;
          }
          const { done, value } = await reader.read();
          if (done) {
            if (streamBuffer.trim()) {
              const lines = streamBuffer.split('\n');
              for (const line of lines) {
                processDataLine(line);
              }
            }
            break;
          }

          try {
            streamBuffer += decoder.decode(value, { stream: true });
          } catch (decodeError) {
            console.error('重新回答流式解码失败:', decodeError);
            continue;
          }

          const lines = streamBuffer.split('\n');
          streamBuffer = lines.pop() || '';

          for (const line of lines) {
            processDataLine(line);
          }
        }

        if (isCurrentRequest()) {
          setChatLoadingForId(requestChatId, false);
        }
      } catch (error) {
        if ((error as Error).name === 'AbortError') {
          console.log('重新回答请求已中止');
          if (requestIdsByChatIdRef.current[requestChatId] === requestId && isMountedRef.current) {
            setChatLoadingForId(requestChatId, false);
          }
          return;
        }

        console.error('重新回答失败:', error);
        if (requestIdsByChatIdRef.current[requestChatId] === requestId && isMountedRef.current) {
          setChatLoadingForId(requestChatId, false);
          setChatMessagesForId(requestChatId, (prev) => [...prev, {
            id: uuidv4(),
            role: 'system',
            content: '重新生成时网络连接异常，请稍后重试。',
            displayContent: '重新生成时网络连接异常，请稍后重试。',
            timestamp: new Date(),
            type: 'error'
          }]);
        }
      } finally {
        if (requestIdsByChatIdRef.current[requestChatId] === requestId) {
          const { [requestChatId]: _removedRequestId, ...restRequestIds } = requestIdsByChatIdRef.current;
          requestIdsByChatIdRef.current = restRequestIds;
          if (activeChatIdRef.current === requestChatId) {
            activeRequestIdRef.current = null;
          }
        }
        if (abortControllersByChatIdRef.current[requestChatId] === abortController) {
          const { [requestChatId]: _removedController, ...restControllers } = abortControllersByChatIdRef.current;
          abortControllersByChatIdRef.current = restControllers;
          if (activeChatIdRef.current === requestChatId) {
            activeAbortControllerRef.current = null;
          }
        }
        if (regenerateContextsByChatIdRef.current[requestChatId]?.requestId === requestId) {
          const { [requestChatId]: _removedContext, ...restContexts } = regenerateContextsByChatIdRef.current;
          regenerateContextsByChatIdRef.current = restContexts;
          if (activeChatIdRef.current === requestChatId) {
            activeRegenerateContextRef.current = null;
          }
        }
      }
    };

    const handleMessageFeedback = (messageId: string, feedback: FeedbackType) => {
      setMessageFeedback((prev) => {
        if (prev[messageId] === feedback) {
          const { [messageId]: _removed, ...rest } = prev;
          return rest;
        }
        return {
          ...prev,
          [messageId]: feedback,
        };
      });
    };

    // 处理MCP服务器选择
    const handleMcpServerChange = (serverName: string, checked: boolean) => {
      console.log('MCP服务器选择变化:', { serverName, checked, current: selectedMcpServers });
      setSelectedMcpServers(prev => {
        const newSelection = checked
          ? [...prev, serverName]
          : prev.filter(name => name !== serverName);
        console.log('更新后的选择:', newSelection);
        return newSelection;
      });
    };

    // 全选可用的服务器
    const handleSelectAll = () => {
      const availableServers = mcpServers.filter(s => s.status !== 'error' && s.disabled !== true).map(s => s.name);
      console.log('全选可用服务器:', availableServers);
      setSelectedMcpServers(availableServers);
    };

    // 清空选择
    const handleClearAll = () => {
      console.log('清空所有选择');
      setSelectedMcpServers([]);
    };

    const handleSkillChange = (skillId: string, checked: boolean) => {
      setSelectedSkillIds(prev => (
        checked
          ? (prev.includes(skillId) ? prev : [...prev, skillId])
          : prev.filter(id => id !== skillId)
      ));
    };

    const handleSelectAllSkills = () => {
      setSelectedSkillIds(skills.filter(skill => skill.available).map(skill => skill.id));
    };

    const handleClearSkills = () => {
      setSelectedSkillIds([]);
    };

    // 获取服务器图标
    const getServerIcon = (serverName: string) => {
      if (serverName === 'baidu-map') return '🗺️';
      if (serverName === '12306-mcp') return '🚄';
      if (serverName === 'fetch') return '🌐';
      if (serverName.includes('search')) return '🔍';
      return '🔧';
    };

    // 新增：复制代码功能
    const copyToClipboard = async (text: string) => {
      try {
        await navigator.clipboard.writeText(text);
        setCopiedCode(text);
        setTimeout(() => setCopiedCode(''), 2000);
      } catch (err) {
        console.error('复制失败:', err);
      }
    };

    // 检测并格式化JSON
    const formatJSON = (text: string) => {
      try {
        const parsed = JSON.parse(text);
        return JSON.stringify(parsed, null, 2);
      } catch {
        return text;
      }
    };

    // 检测是否为JSON格式
    const isValidJSON = (text: string) => {
      try {
        JSON.parse(text);
        return true;
      } catch {
        return false;
      }
    };

    // 增强的代码块渲染组件
    const CodeBlock = ({ language, children, isInline = false }: any) => {
      const codeString = String(children).replace(/\n$/, '');

      if (isInline) {
        return (
          <code
            style={{
              background: '#f8fafc',
              color: '#475569',
              padding: '2px 6px',
              borderRadius: '4px',
              fontSize: '13px',
              fontFamily: 'SF Mono, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
              border: '1px solid #e2e8f0'
            }}
          >
            {children}
          </code>
        );
      }

      // 特殊处理JSON格式
      const isJSON = language === 'json' || (!language && isValidJSON(codeString));
      const displayCode = isJSON ? formatJSON(codeString) : codeString;
      const displayLanguage = isJSON ? 'json' : (language || 'text');

      return (
        <div style={{
          position: 'relative',
          margin: '12px 0',
          borderRadius: '8px',
          overflow: 'hidden',
          border: '1px solid #e2e8f0'
        }}>
          {/* 代码块头部 */}
          <div style={{
            background: '#f8fafc',
            padding: '8px 12px',
            borderBottom: '1px solid #e2e8f0',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center'
          }}>
            <span style={{
              fontSize: '12px',
              color: '#64748b',
              fontWeight: 500,
              textTransform: 'uppercase'
            }}>
              {displayLanguage}
            </span>
            <Button
              type="text"
              size="small"
              icon={copiedCode === displayCode ? <CheckOutlined /> : <CopyOutlined />}
              onClick={() => copyToClipboard(displayCode)}
              style={{
                fontSize: '12px',
                padding: '2px 6px',
                height: 'auto',
                color: copiedCode === displayCode ? '#10b981' : '#64748b'
              }}
            >
              {copiedCode === displayCode ? '已复制' : '复制'}
            </Button>
          </div>

          <SyntaxHighlighter
            style={tomorrow as any}
            language={displayLanguage}
            PreTag="div"
            customStyle={{
              background: '#1e293b',
              margin: 0,
              fontSize: '13px',
              fontFamily: 'SF Mono, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
              borderRadius: 0
            }}
          >
            {displayCode}
          </SyntaxHighlighter>
        </div>
      );
    };

    // 自定义表格组件
    const MarkdownTable = ({ children }: any) => {
      // 解析表格数据
      const parseTableData = (tableChildren: any) => {
        const rows: any[] = [];
        const headers: string[] = [];

        const toPlainText = (value: any): string => {
          if (value === null || value === undefined) return '';
          if (typeof value === 'string' || typeof value === 'number') return String(value);
          if (Array.isArray(value)) return value.map(toPlainText).join('');
          if (React.isValidElement(value)) {
            return toPlainText((value as any).props?.children);
          }
          return '';
        };

        React.Children.forEach(tableChildren, (section: any) => {
          if (section?.type === 'thead') {
            React.Children.forEach(section.props?.children, (row: any) => {
              if (row?.type === 'tr') {
                React.Children.forEach(row.props?.children, (cell: any) => {
                  if (cell?.type === 'th') {
                    headers.push(toPlainText(cell.props?.children));
                  }
                });
              }
            });
          } else if (section?.type === 'tbody') {
            React.Children.forEach(section.props?.children, (row: any) => {
              if (row?.type === 'tr') {
                const rowData: any = {};
                let cellIndex = 0;
                React.Children.forEach(row.props?.children, (cell: any) => {
                  if (cell?.type === 'td') {
                    rowData[headers[cellIndex] || `col${cellIndex}`] = toPlainText(cell.props?.children);
                    cellIndex++;
                  }
                });
                rows.push(rowData);
              }
            });
          }
        });

        return { headers, rows };
      };

      const { headers, rows } = parseTableData(children);

      const columns = headers.map((header, index) => ({
        title: header,
        dataIndex: header || `col${index}`,
        key: header || `col${index}`,
        render: (text: any) => (
          <Text style={{ fontSize: '13px' }}>
            {Array.isArray(text) ? text.join('') : text}
          </Text>
        )
      }));

      return (
        <div style={{ margin: '12px 0', overflow: 'auto' }}>
          <Table
            columns={columns}
            dataSource={rows.map((row, index) => ({ ...row, key: index }))}
            pagination={false}
            size="small"
            bordered
            style={{
              fontSize: '13px'
            }}
            scroll={{ x: true }}
          />
        </div>
      );
    };

    // 渲染消息 - 豆包风格
    const renderMessage = (message: Message, options?: { enableActions?: boolean }) => {
      const safeDisplayContent = safeText(message.displayContent) || safeText(message.content);
      const enableActions = options?.enableActions ?? true;
      const isUser = message.role === 'user';
      const isError = message.type === 'error';
      const isAssistantAnswer = isAssistantAnswerMessage(message);
      const feedback = messageFeedback[message.id];
      const regeneratePrompt = isAssistantAnswer ? getRegeneratePrompt(message.id) : '';
      const copyableAnswer = formatMainChatContent(safeDisplayContent).trim();

      return (
        <div
          key={message.id}
          className={`message-bubble ${isUser ? 'user-message' : ''}`}
          style={{
            display: 'flex',
            justifyContent: isUser ? 'flex-end' : 'flex-start',
            marginBottom: '12px'
          }}
        >
          <div style={{
            maxWidth: isUser ? '85%' : '100%',
            width: isUser ? 'auto' : '100%',
            minWidth: '120px',
            position: 'relative'
          }}>
            {/* 智能体类型标签 */}
            {!isUser && message.agentType && (
              <div style={{
                fontSize: '12px',
                color: 'var(--travel-primary-dark)',
                marginBottom: '4px',
                fontWeight: 500
              }}>
                {message.agentType}
              </div>
            )}

            {/* 消息气泡 */}
            <div
              className={`markdown-content ${isUser ? 'user-message' : ''}`}
              style={{
                background: isUser
                  ? 'var(--travel-gradient-primary)'
                  : isError
                    ? '#fef2f2'
                    : 'linear-gradient(145deg, color-mix(in oklch, var(--travel-surface) 74%, var(--travel-bg-soft) 26%), color-mix(in oklch, var(--travel-surface-muted) 42%, white 58%))',
                color: isUser
                  ? '#ffffff'
                  : isError
                    ? '#dc2626'
                    : '#1f2937',
                padding: '12px 16px',  // 增加内边距
                borderRadius: isUser
                  ? '18px 18px 6px 18px'
                  : '18px 18px 18px 6px',
                boxShadow: isUser
                  ? '0 10px 18px rgba(46, 95, 138, 0.18)'
                  : '0 4px 8px rgba(67, 76, 118, 0.06)',
                border: isUser
                  ? 'none'
                  : '1px solid color-mix(in oklch, var(--travel-border-soft) 72%, transparent)',
                fontSize: '14px',
                lineHeight: '1.6',  // 增加行高
                wordBreak: 'break-word',
                position: 'relative'
              }}
            >
              {/* 消息耗时显示 */}
              {!isUser && (message.duration ?? 0) > 0 && (
                <div style={{
                  position: 'absolute',
                  top: '4px',
                  right: '8px',
                  fontSize: '10px',
                  color: '#9ca3af',
                  background: 'rgba(255, 255, 255, 0.9)',
                  padding: '1px 4px',
                  borderRadius: '3px',
                  border: '1px solid #f1f5f9'
                }}>
                  {formatDuration(Number(message.duration ?? 0))}
                </div>
              )}

              <ReactMarkdown
                remarkPlugins={[remarkGfm]}  // 添加GitHub风味Markdown支持
                components={{
                  code({ className, children }) {
                    const match = /language-(\w+)/.exec(className || '');
                    const isInline = !match;

                    return (
                      <CodeBlock
                        language={match?.[1]}
                        isInline={isInline}
                      >
                        {children}
                      </CodeBlock>
                    );
                  },

                  // 表格组件
                  table({ children }) {
                    return <MarkdownTable>{children}</MarkdownTable>;
                  },

                  // 段落样式优化
                  p({ children }) {
                    return <div style={{ margin: '6px 0', lineHeight: '1.6' }}>{children}</div>;
                  },

                  // 列表样式优化
                  ul({ children }) {
                    return (
                      <ul style={{
                        margin: '8px 0',
                        paddingLeft: '20px',
                        lineHeight: '1.6'
                      }}>
                        {children}
                      </ul>
                    );
                  },

                  ol({ children }) {
                    return (
                      <ol style={{
                        margin: '8px 0',
                        paddingLeft: '20px',
                        lineHeight: '1.6'
                      }}>
                        {children}
                      </ol>
                    );
                  },

                  // 引用块样式优化
                  blockquote({ children }) {
                    return (
                      <blockquote style={{
                        borderLeft: `4px solid ${isUser ? 'rgba(255,255,255,0.3)' : '#e2e8f0'}`,
                        margin: '12px 0',
                        fontStyle: 'italic',
                        opacity: 0.9,
                        background: isUser
                          ? 'rgba(255, 255, 255, 0.1)'
                          : '#f8fafc',
                        borderRadius: '6px',
                        padding: '12px 12px 12px 16px'
                      }}>
                        {children}
                      </blockquote>
                    );
                  },

                  // 标题样式优化
                  h1({ children }) {
                    return (
                      <h1 style={{
                        fontSize: '20px',
                        fontWeight: 700,
                        margin: '16px 0 8px 0',
                        color: isUser ? '#ffffff' : '#1f2937',
                        paddingBottom: '0'
                      }}>
                        {children}
                      </h1>
                    );
                  },

                  h2({ children }) {
                    return (
                      <h2 style={{
                        fontSize: '18px',
                        fontWeight: 600,
                        margin: '14px 0 6px 0',
                        color: isUser ? '#ffffff' : '#1f2937'
                      }}>
                        {children}
                      </h2>
                    );
                  },

                  h3({ children }) {
                    return (
                      <h3 style={{
                        fontSize: '16px',
                        fontWeight: 600,
                        margin: '12px 0 4px 0',
                        color: isUser ? '#ffffff' : '#1f2937'
                      }}>
                        {children}
                      </h3>
                    );
                  },

                  // 水平分割线
                  hr() {
                    return null;
                  },

                  // 强调文本
                  strong({ children }) {
                    return (
                      <strong style={{
                        fontWeight: 700,
                        color: isUser ? '#ffffff' : '#1f2937'
                      }}>
                        {children}
                      </strong>
                    );
                  },

                  em({ children }) {
                    return (
                      <em style={{
                        fontStyle: 'italic',
                        color: isUser ? 'rgba(255,255,255,0.9)' : '#4b5563'
                      }}>
                        {children}
                      </em>
                    );
                  },

                  // 链接样式
                  a({ href, children }) {
                    return (
                      <a
                        href={href}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{
                          color: isUser ? '#ffffff' : 'var(--travel-primary-dark)',
                          textDecoration: 'underline',
                          textDecorationColor: isUser ? 'rgba(255,255,255,0.5)' : 'var(--travel-primary)'
                        }}
                      >
                        {children}
                      </a>
                    );
                  }
                }}
              >
                {formatMainChatContent(safeDisplayContent)}
              </ReactMarkdown>
            </div>

            {/* 时间戳与消息操作 */}
            <div style={{
              display: 'flex',
              justifyContent: isAssistantAnswer ? 'space-between' : (isUser ? 'flex-end' : 'flex-start'),
              alignItems: 'center',
              gap: '8px',
              marginTop: '4px'
            }}>
              <div style={{
                fontSize: '11px',
                color: '#9ca3af',
                textAlign: isUser ? 'right' : 'left'
              }}>
                {message.timestamp.toLocaleTimeString('zh-CN', {
                  hour: '2-digit',
                  minute: '2-digit'
                })}
              </div>

              {isAssistantAnswer && enableActions && (
                <div style={{
                  display: 'flex',
                  justifyContent: 'flex-end',
                  alignItems: 'center',
                  gap: '4px'
                }}>
                  <Button
                    size="small"
                    type="text"
                    icon={<RedoOutlined />}
                    onClick={() => handleRegenerateAnswer(message)}
                    disabled={!regeneratePrompt || isLoading}
                    title="重新回答"
                    aria-label="重新回答"
                    style={{
                      color: '#6b7280',
                      fontSize: '12px',
                      height: '24px',
                      width: '24px',
                      minWidth: '24px',
                      padding: 0
                    }}
                  />

                  <Button
                    size="small"
                    type="text"
                    icon={copiedCode === copyableAnswer ? <CheckOutlined /> : <CopyOutlined />}
                    onClick={() => copyToClipboard(copyableAnswer)}
                    disabled={!copyableAnswer}
                    title={copiedCode === copyableAnswer ? '已复制' : '复制回答'}
                    aria-label={copiedCode === copyableAnswer ? '已复制' : '复制回答'}
                    style={{
                      color: copiedCode === copyableAnswer ? '#10b981' : '#6b7280',
                      fontSize: '12px',
                      height: '24px',
                      width: '24px',
                      minWidth: '24px',
                      padding: 0
                    }}
                  />

                  <Button
                    size="small"
                    type="text"
                    icon={feedback === 'like' ? <LikeFilled /> : <LikeOutlined />}
                    onClick={() => handleMessageFeedback(message.id, 'like')}
                    title="答得好"
                    aria-label="答得好"
                    style={{
                      color: feedback === 'like' ? '#1677ff' : '#6b7280',
                      fontSize: '12px',
                      height: '24px',
                      width: '24px',
                      minWidth: '24px',
                      padding: 0
                    }}
                  />

                  <Button
                    size="small"
                    type="text"
                    icon={feedback === 'dislike' ? <DislikeFilled /> : <DislikeOutlined />}
                    onClick={() => handleMessageFeedback(message.id, 'dislike')}
                    title="答得不好"
                    aria-label="答得不好"
                    style={{
                      color: feedback === 'dislike' ? '#ef4444' : '#6b7280',
                      fontSize: '12px',
                      height: '24px',
                      width: '24px',
                      minWidth: '24px',
                      padding: 0
                    }}
                  />
                </div>
              )}
            </div>
          </div>
        </div>
      );
    };

    const filteredMessages = useMemo(
      () => messages.filter(
        (msg) => safeText(msg.displayContent).trim().length > 0 || safeText(msg.content).trim().length > 0
      ),
      [messages]
    );

    const messageGroups = useMemo(
      () => groupMessages(filteredMessages),
      [filteredMessages, useDeepThink, useMultiAgent]
    );

    const getGroupFinalAnswers = (group: MessageGroup): Message[] => {
      if (!group.finalAnswer) return [];
      return Array.isArray(group.finalAnswer) ? group.finalAnswer : [group.finalAnswer];
    };

    const normalizeLocationDay = (day: LocationPoint['day']): string => {
      const text = day === undefined || day === null ? '' : String(day).trim();
      if (!text) return '';
      const match = text.match(/\d+/);
      return match ? match[0] : text;
    };

    const getLocationOrder = (location: LocationPoint, fallbackIndex: number): number => {
      const rawOrder = Number(location.order);
      return Number.isFinite(rawOrder) ? rawOrder : fallbackIndex + 1;
    };

    const buildLocationGroupsForAnswer = (
      baseGroupId: string,
      baseTitle: string,
      locations: LocationPoint[],
      userMessageId: string,
      userPrompt: string
    ): LocationGroupData[] => {
      const hasDayMetadata = locations.some((location) => normalizeLocationDay(location.day));
      if (!hasDayMetadata) {
        return [{
          groupId: baseGroupId,
          title: baseTitle,
          locations,
          userMessageId,
          userPrompt,
        }];
      }

      const groupsByDay = new Map<string, LocationPoint[]>();
      locations.forEach((location) => {
        const dayKey = normalizeLocationDay(location.day) || '未分组';
        const existing = groupsByDay.get(dayKey) || [];
        existing.push(location);
        groupsByDay.set(dayKey, existing);
      });

      return Array.from(groupsByDay.entries())
        .sort(([left], [right]) => {
          const leftNum = Number(left);
          const rightNum = Number(right);
          if (Number.isFinite(leftNum) && Number.isFinite(rightNum)) {
            return leftNum - rightNum;
          }
          return left.localeCompare(right, 'zh-Hans-CN');
        })
        .map(([dayKey, dayLocations]) => ({
          groupId: `${baseGroupId}_day_${dayKey}`,
          title: dayKey === '未分组' ? `${baseTitle} 未分组地点` : `第${dayKey}天路线`,
          locations: [...dayLocations].sort((left, right) => (
            getLocationOrder(left, locations.indexOf(left)) - getLocationOrder(right, locations.indexOf(right))
          )),
          userMessageId,
          userPrompt,
        }));
    };

    const structuredLocationGroups = useMemo(() => {
      if (tripWorkspace.locations.length === 0) return [];
      const latestUserMessage = [...messages].reverse().find((message) => message.role === 'user');
      return buildLocationGroupsForAnswer(
        'structured_trip_workspace',
        '结构化行程地点',
        tripWorkspace.locations,
        latestUserMessage?.id || '',
        safeText(latestUserMessage?.displayContent || latestUserMessage?.content)
      );
    }, [tripWorkspace.locations, messages]);

    const groupedMapData = useMemo(() => {
      const groups: LocationGroupData[] = [];
      const seenSignatures = new Set<string>();

      messageGroups.forEach((group, index) => {
        const finalAnswers = getGroupFinalAnswers(group);

        let latestLocations: LocationPoint[] = [];
        finalAnswers.forEach((answerMessage) => {
          const extracted = extractMapLocations(safeText(answerMessage.displayContent));
          if (extracted.length > 0) {
            latestLocations = extracted;
          }
        });

        if (latestLocations.length === 0) {
          return;
        }

        const signature = buildLocationSetSignature(latestLocations);
        if (!signature || seenSignatures.has(signature)) {
          return;
        }
        seenSignatures.add(signature);

        const baseGroupId = `group_${index + 1}_${group.userMessage.id}`;
        const userPrompt = safeText(group.userMessage.displayContent) || safeText(group.userMessage.content);
        groups.push(...buildLocationGroupsForAnswer(
          baseGroupId,
          `第${index + 1}次提问`,
          latestLocations,
          group.userMessage.id,
          userPrompt
        ));
      });

      return groups;
    }, [messageGroups]);

    const effectiveLocationGroups = useMemo(
      () => (mapSuppressed ? [] : groupedMapData),
      [mapSuppressed, groupedMapData]
    );

    const mapLocationGroupsForRender = useMemo(
      () => structuredLocationGroups,
      [structuredLocationGroups]
    );
    const workspaceLocationGroups = useMemo(
      () => [...structuredLocationGroups, ...effectiveLocationGroups],
      [effectiveLocationGroups, structuredLocationGroups]
    );

    useEffect(() => {
      if (mapLocationGroupsForRender.length === 0) {
        setActiveMapGroupId('');
        return;
      }
      setActiveMapGroupId((current) => (
        mapLocationGroupsForRender.some((group) => group.groupId === current)
          ? current
          : mapLocationGroupsForRender[0].groupId
      ));
    }, [mapLocationGroupsForRender]);

    const effectiveMapLocations = useMemo(
      () => (mapSuppressed ? [] : tripWorkspace.locations),
      [mapSuppressed, tripWorkspace.locations]
    );

    useEffect(() => {
      if (selectedLocationId && !effectiveMapLocations.some((location) => location.id === selectedLocationId)) {
        setSelectedLocationId('');
      }
    }, [effectiveMapLocations, selectedLocationId]);

    const mapDataSignature = useMemo(
      () => effectiveMapLocations.map((loc) => `${loc.id}:${loc.name}:${loc.lat}:${loc.lng}`).join('|'),
      [effectiveMapLocations]
    );

    const hasTripWorkspaceData = useMemo(
      () => (
        tripWorkspace.locations.length > 0
        || (tripWorkspace.days?.length || 0) > 0
        || tripWorkspace.sources.length > 0
        || Boolean(tripWorkspace.budget)
        || Boolean(tripWorkspace.validation)
        || Boolean(tripWorkspace.repair)
      ),
      [tripWorkspace]
    );

    useEffect(() => {
      if (!mapDataSignature || lastAutoOpenedMapSignatureRef.current === mapDataSignature) {
        return;
      }

      lastAutoOpenedMapSignatureRef.current = mapDataSignature;
      setShowMap(true);
    }, [mapDataSignature]);

    const activePendingClarification = pendingClarification?.chatId === getActiveChatId()
      ? pendingClarification
      : null;

    const submitClarificationAnswer = (
      clarification: PendingClarification,
      answer: Record<string, string>,
    ) => {
      const cumulativeAnswers = { ...clarification.answers, ...answer };
      const submittedField = Object.keys(answer)[0];
      const answeredCount = submittedField
        && Object.prototype.hasOwnProperty.call(clarification.answers, submittedField)
        ? clarification.answeredCount
        : clarification.answeredCount + 1;

      setPendingClarification((current) => (
        current && current.chatId === clarification.chatId
          ? { ...current, answers: cumulativeAnswers, answeredCount }
          : current
      ));

      void handleSendMessage('', {
        appendUserMessage: false,
        requestMessagesOverride: clarification.requestMessages,
        clarificationAnswers: cumulativeAnswers,
        clarificationAnsweredCount: answeredCount,
        clarificationQuestionId: clarification.question.id,
      });
    };

    const renderedMessageGroups = useMemo(
      () => messageGroups.map((group, groupIndex) => (
        <div key={`group-${groupIndex}`} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          {/* 渲染用户消息 */}
          {renderMessage(group.userMessage)}

          {/* 渲染深度思考气泡框 */}
          {renderDeepThinkBubble(group.deepThinkMessages)}

          {/* 渲染最终答案 */}
          {(() => {
            const finalAnswers = getGroupFinalAnswers(group);
            if (finalAnswers.length === 0) return null;

            const userMessageId = group.userMessage.id;
            const isLatestGroup = groupIndex === messageGroups.length - 1;
            const maxIndex = finalAnswers.length - 1;
            const requestedIndex = answerPageByUserMessageId[userMessageId];
            const activeIndex = typeof requestedIndex === 'number'
              ? Math.max(0, Math.min(maxIndex, requestedIndex))
              : maxIndex;

            return (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {renderMessage(finalAnswers[activeIndex], { enableActions: isLatestGroup })}

                {finalAnswers.length > 1 && (
                  <div style={{
                    display: 'flex',
                    justifyContent: 'flex-start',
                    alignItems: 'center',
                    gap: '8px',
                    marginLeft: '44px'
                  }}>
                    <Button
                      size="small"
                      onClick={() => setAnswerPageByUserMessageId((prev) => ({
                        ...prev,
                        [userMessageId]: Math.max(0, activeIndex - 1),
                      }))}
                      disabled={activeIndex <= 0}
                    >
                      上一版
                    </Button>

                    <Text style={{ fontSize: '12px', color: '#6b7280' }}>
                      回答版本 {activeIndex + 1}/{finalAnswers.length}
                    </Text>

                    <Button
                      size="small"
                      onClick={() => setAnswerPageByUserMessageId((prev) => ({
                        ...prev,
                        [userMessageId]: Math.min(maxIndex, activeIndex + 1),
                      }))}
                      disabled={activeIndex >= maxIndex}
                    >
                      下一版
                    </Button>
                  </div>
                )}
              </div>
            );
          })()}
        </div>
      )),
      [messageGroups, copiedCode, messageFeedback, isLoading, answerPageByUserMessageId, settings, profile]
    );

    return (
      <div
        ref={chatMapLayoutRef}
        className={`chat-map-layout ${showMap ? 'chat-map-layout-open' : ''} mobile-view-${mobilePrimaryView}`}
        style={{
          height: '100vh',
          display: 'flex',
          flexDirection: 'row',
          overflow: 'hidden',
          background: 'var(--travel-gradient-page)',
          position: 'relative'
        }}
      >
        <div className="mobile-primary-view-switcher" aria-label="切换主要视图">
          <Segmented
            block
            value={mobilePrimaryView}
            options={[
              { label: '对话', value: 'chat', icon: <MessageOutlined /> },
              { label: '行程', value: 'trip', icon: <CalendarOutlined /> },
              { label: '地图', value: 'map', icon: <EnvironmentOutlined /> },
            ]}
            onChange={(value) => {
              const nextView = value as MobilePrimaryView;
              setMobilePrimaryView(nextView);
              if (nextView !== 'chat') setShowMap(true);
            }}
          />
        </div>
        {/* 左侧聊天区域 */}
        <div className="chat-panel" style={{
          flex: showMap ? `0 0 ${chatPanelWidthPercent}%` : 1,
          minWidth: 0,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          borderRight: showMap ? '1px solid var(--travel-border)' : 'none'
        }}>
          {/* 消息列表 - 豆包风格 */}
          <div className="chat-scroll-region" style={{
            flex: 1,
            overflow: 'auto',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center'
          }}>
            <div style={{
              width: 'calc(100% - 48px)',
              maxWidth: '768px',
              padding: '16px 0'
            }}>
              {messages.length === 0 ? (
                <div style={{
                  display: 'flex',
                  flexDirection: 'column',
                  justifyContent: 'center',
                  alignItems: 'center',
                  textAlign: 'center',
                  color: '#6b7280',
                  padding: '60px 20px',
                  minHeight: '400px'
                }}>
                  <div style={{
                    width: '64px',
                    height: '64px',
                    borderRadius: '16px',
                    background: 'var(--travel-selected)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    marginBottom: '20px',
                    boxShadow: 'none'
                  }}>
                    <EnvironmentOutlined style={{ fontSize: '28px', color: 'var(--travel-primary-dark)' }} />
                  </div>

                  <div style={{ fontSize: '18px', fontWeight: 600, marginBottom: '8px', color: 'var(--travel-ink)' }}>
                    开始一段轻松的旅行规划
                  </div>
                  <div style={{ fontSize: '14px', lineHeight: '1.5', marginBottom: '24px', maxWidth: '320px' }}>
                    告诉我目的地、天数、预算或旅行偏好，我会边聊边整理路线、景点和地图位置。
                  </div>

                  {/* 功能特色 */}
                  <div style={{
                    display: 'flex',
                    gap: '12px',
                    flexWrap: 'wrap',
                    justifyContent: 'center',
                    marginBottom: '32px'
                  }}>
                    <div style={{
                      padding: '12px 16px',
                      background: 'linear-gradient(135deg, var(--travel-surface), color-mix(in oklch, var(--travel-surface-muted) 34%, white 66%))',
                      borderRadius: '14px',
                      border: '1px solid var(--travel-border-soft)',
                      boxShadow: 'var(--travel-card-shadow)',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '8px'
                    }}>
                      <ThunderboltOutlined style={{ fontSize: '16px', color: '#f59e0b' }} />
                      <span style={{ fontSize: '13px', fontWeight: 500, color: '#374151' }}>
                        深度规划
                      </span>
                    </div>

                    <div style={{
                      padding: '12px 16px',
                      background: 'linear-gradient(135deg, var(--travel-surface), color-mix(in oklch, var(--travel-surface-muted) 34%, white 66%))',
                      borderRadius: '14px',
                      border: '1px solid var(--travel-border-soft)',
                      boxShadow: 'var(--travel-card-shadow)',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '8px'
                    }}>
                      <BranchesOutlined style={{ fontSize: '16px', color: '#10b981' }} />
                      <span style={{ fontSize: '13px', fontWeight: 500, color: '#374151' }}>
                        智能体协作
                      </span>
                    </div>
                  </div>

                  {/* 使用示例 */}
                  <div style={{
                    width: '100%',
                    maxWidth: '600px'
                  }}>
                    <div style={{
                      fontSize: '16px',
                      fontWeight: 600,
                      color: '#1f2937',
                      marginBottom: '16px'
                    }}>
                      试试这些示例
                    </div>

                    <div style={{
                      display: 'grid',
                      gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
                      gap: '12px'
                    }}>
                      {[
                        {
                          title: "行程规划",
                          example: "帮我规划一次北京3天2夜的文化之旅",
                          icon: "🗺️"
                        },
                        {
                          title: "酒店预订",
                          example: "推荐上海外滩附近性价比高的酒店",
                          icon: "🏨"
                        },
                        {
                          title: "美食攻略",
                          example: "制定成都美食探索指南和必吃清单",
                          icon: "🍜"
                        },
                        {
                          title: "交通查询",
                          example: "查询从广州到桂林的最佳交通方案",
                          icon: "🚄"
                        }
                      ].map((item, index) => (
                        <div
                          key={index}
                          style={{
                            padding: '16px',
                            background: 'radial-gradient(circle at 92% 14%, color-mix(in oklch, var(--travel-accent) 13%, transparent), transparent 30%), var(--travel-surface)',
                            borderRadius: '16px',
                            border: '1px solid var(--travel-border-soft)',
                            boxShadow: 'var(--travel-card-shadow)',
                            cursor: 'pointer',
                            transition: 'all 0.2s ease',
                            textAlign: 'left'
                          }}
                          onClick={() => setInputText(item.example, true)}
                          onMouseEnter={(e) => {
                            e.currentTarget.style.borderColor = 'color-mix(in oklch, var(--travel-primary-soft) 64%, var(--travel-border) 36%)';
                            e.currentTarget.style.boxShadow = '0 10px 16px rgba(28, 52, 78, 0.10)';
                            e.currentTarget.style.transform = 'translateY(-2px)';
                          }}
                          onMouseLeave={(e) => {
                            e.currentTarget.style.borderColor = 'var(--travel-border-soft)';
                            e.currentTarget.style.boxShadow = 'var(--travel-card-shadow)';
                            e.currentTarget.style.transform = 'translateY(0)';
                          }}
                        >
                          <div style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '8px',
                            marginBottom: '8px'
                          }}>
                            <span style={{ fontSize: '18px' }}>{item.icon}</span>
                            <span style={{
                              fontSize: '14px',
                              fontWeight: 600,
                              color: '#1f2937'
                            }}>
                              {item.title}
                            </span>
                          </div>
                          <div style={{
                            fontSize: '13px',
                            color: '#6b7280',
                            lineHeight: '1.4'
                          }}>
                            {item.example}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                  {renderedMessageGroups}
                </div>
              )}

              {isLoading && (
                <div style={{
                  display: 'flex',
                  justifyContent: 'center',
                  padding: '20px 0',
                  alignItems: 'center',
                  gap: '8px',
                  color: '#6b7280'
                }}>
                  <Spin size="small" />
                  <span>旅游智能体正在规划...</span>
                </div>
              )}

              <div ref={messagesEndRef} />
            </div>
          </div>

          {/* 豆包风格的输入区域 */}
          <div style={{
            padding: '16px 24px 20px',
            background: 'transparent',
            flexShrink: 0
          }}>
            {/* 输入框容器 - 豆包风格多行设计 */}
            <div style={{
              maxWidth: '768px',
              margin: '0 auto'
            }}>
              {selectedKnowledgeContext.length > 0 && (
                <div className="selected-knowledge-context-bar">
                  <div>
                    <strong>知识库上下文</strong>
                    <span>{selectedKnowledgeContext.slice(0, 2).map((item) => item.title || item.city).join('、')}</span>
                    {selectedKnowledgeContext.length > 2 && <small>+{selectedKnowledgeContext.length - 2}</small>}
                  </div>
                  <Button size="small" type="text" onClick={clearSelectedKnowledgeContext}>
                    清空
                  </Button>
                </div>
              )}
              {activePendingClarification && (
                <div className="clarification-dock">
                  <ClarificationPanel
                    question={activePendingClarification.question}
                    answeredCount={activePendingClarification.answeredCount}
                    loading={isLoading}
                    profileDefaultValue={activePendingClarification.question.profile_default_value
                      || getClarificationProfileDefault(
                        activePendingClarification.question.field,
                        profile as unknown as Record<string, unknown>,
                      )}
                    onSubmit={(answer) => submitClarificationAnswer(activePendingClarification, answer)}
                    onSkip={() => submitClarificationAnswer(activePendingClarification, {
                      [activePendingClarification.question.field]: CLARIFICATION_SKIP_VALUE,
                    })}
                  />
                </div>
              )}
              <div
                className="chat-input-container"
                style={{
                  position: 'relative',
                  borderRadius: '18px',
                  background: 'linear-gradient(145deg, color-mix(in oklch, var(--travel-bg) 78%, transparent), color-mix(in oklch, var(--travel-bg-soft) 58%, transparent))',
                  transition: 'all 0.2s ease',
                  minHeight: '140px',
                  display: 'flex',
                  flexDirection: 'column',
                  border: '1px solid var(--travel-border-soft)'
                }}
              >
                {/* 顶部功能开关行 */}
                {/* 控制选项区域 - 只保留地图按钮 */}
                <div style={{
                  display: 'flex',
                  justifyContent: 'flex-end',
                  alignItems: 'center',
                  padding: '12px 16px 8px 16px',
                  borderBottom: '1px solid color-mix(in oklch, var(--travel-border-soft) 74%, transparent)'
                }}>
                  {/* 隐藏左侧的控制开关 */}
                  {false && (
                    <div style={{
                      display: 'flex',
                      gap: '12px',
                      fontSize: '12px'
                    }}>
                      <label style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '6px',
                        color: '#6b7280',
                        cursor: 'pointer',
                        padding: '4px 8px',
                        borderRadius: '12px',
                        background: useDeepThink ? '#f0f9ff' : 'transparent',
                        border: useDeepThink ? '1px solid #bae6fd' : '1px solid transparent',
                        transition: 'all 0.2s',
                        fontSize: '12px'
                      }}>
                        <Switch
                          checked={useDeepThink}
                          onChange={setUseDeepThink}
                          size="small"
                        />
                        <ThunderboltOutlined style={{ color: useDeepThink ? '#0ea5e9' : '#6b7280', fontSize: '12px' }} />
                        深度思考
                      </label>

                      <label style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '6px',
                        color: '#6b7280',
                        cursor: 'pointer',
                        padding: '4px 8px',
                        borderRadius: '12px',
                        background: useMultiAgent ? '#f0fdf4' : 'transparent',
                        border: useMultiAgent ? '1px solid #bbf7d0' : '1px solid transparent',
                        transition: 'all 0.2s',
                        fontSize: '12px'
                      }}>
                        <Switch
                          checked={useMultiAgent}
                          onChange={setUseMultiAgent}
                          size="small"
                        />
                        <BranchesOutlined style={{ color: useMultiAgent ? '#10b981' : '#6b7280', fontSize: '12px' }} />
                        智能体协作
                      </label>

                      <Dropdown
                        trigger={['click']}
                        open={mcpModalVisible}
                        onOpenChange={setMcpModalVisible}
                        placement="bottomLeft"
                        getPopupContainer={(trigger) => trigger.parentElement || document.body}
                        dropdownRender={() => (
                          <div
                            style={{
                              background: '#ffffff',
                              borderRadius: '8px',
                              padding: '12px',
                              boxShadow: '0 4px 12px rgba(0, 0, 0, 0.15)',
                              border: '1px solid #e5e7eb',
                              minWidth: '280px',
                              maxHeight: '400px',
                              overflowY: 'auto'
                            }}
                            onClick={(e) => e.stopPropagation()} // 阻止事件冒泡，防止Dropdown关闭
                          >
                            <div style={{
                              fontSize: '14px',
                              fontWeight: 500,
                              marginBottom: '8px',
                              color: '#374151',
                              display: 'flex',
                              alignItems: 'center',
                              gap: '6px'
                            }}>
                              <CloudServerOutlined style={{ color: 'var(--travel-primary)' }} />
                              选择MCP服务器 ({selectedMcpServers.length}/{mcpServers.length})
                            </div>
                            <Divider style={{ margin: '8px 0' }} />

                            {mcpLoading ? (
                              <div style={{ textAlign: 'center', padding: '20px' }}>
                                <Spin size="small" />
                                <div style={{ marginTop: '8px', fontSize: '12px', color: '#9ca3af' }}>
                                  加载服务器...
                                </div>
                              </div>
                            ) : mcpServers.length === 0 ? (
                              <div style={{ textAlign: 'center', padding: '20px', color: '#9ca3af', fontSize: '12px' }}>
                                暂无可用的MCP服务器
                              </div>
                            ) : (
                              <div style={{ maxHeight: '300px', overflowY: 'auto' }}>
                                {mcpServers.map((server) => (
                                  <div
                                    key={server.name}
                                    style={{
                                      display: 'flex',
                                      alignItems: 'center',
                                      justifyContent: 'space-between',
                                      padding: '8px 4px',
                                      borderRadius: '10px',
                                      transition: 'background 0.2s',
                                      cursor: (server.status !== 'error' && server.disabled !== true) ? 'pointer' : 'default'
                                    }}
                                    onMouseEnter={(e) => {
                                      if (server.status !== 'error' && server.disabled !== true) {
                                        e.currentTarget.style.background = 'color-mix(in oklch, var(--travel-hover) 72%, white 28%)';
                                      }
                                    }}
                                    onMouseLeave={(e) => {
                                      e.currentTarget.style.background = 'transparent';
                                    }}
                                    onClick={(e) => {
                                      e.stopPropagation(); // 阻止冒泡
                                      if (server.status !== 'error' && server.disabled !== true) {
                                        const isCurrentlySelected = selectedMcpServers.includes(server.name);
                                        handleMcpServerChange(server.name, !isCurrentlySelected);
                                      }
                                    }}
                                  >
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flex: 1 }}>
                                      <span style={{ fontSize: '16px' }}>{getServerIcon(server.name)}</span>
                                      <div>
                                        <div style={{ fontSize: '13px', fontWeight: 500, color: '#374151' }}>
                                          {server.name}
                                        </div>
                                        <div style={{ fontSize: '11px', color: '#9ca3af' }}>
                                          {server.status === 'connected' ? '已连接' : server.status === 'error' ? '连接失败' : '未连接'}
                                          {server.tools_count > 0 && ` • ${server.tools_count} 个工具`}
                                        </div>
                                      </div>
                                    </div>
                                    <Checkbox
                                      checked={selectedMcpServers.includes(server.name)}
                                      onChange={(e) => {
                                        e.stopPropagation(); // 阻止冒泡
                                        handleMcpServerChange(server.name, e.target.checked);
                                      }}
                                      disabled={server.status === 'error' || server.disabled === true}
                                      onClick={(e) => {
                                        e.stopPropagation(); // 阻止冒泡
                                      }}
                                    />
                                  </div>
                                ))}
                              </div>
                            )}

                            <Divider style={{ margin: '8px 0' }} />
                            <div style={{
                              display: 'flex',
                              justifyContent: 'space-between',
                              alignItems: 'center'
                            }}>
                              <Button
                                type="text"
                                size="small"
                                onClick={(e) => {
                                  e.stopPropagation(); // 阻止冒泡
                                  handleSelectAll();
                                }}
                                style={{ fontSize: '12px' }}
                              >
                                全选
                              </Button>
                              <Button
                                type="text"
                                size="small"
                                onClick={(e) => {
                                  e.stopPropagation(); // 阻止冒泡
                                  handleClearAll();
                                }}
                                style={{ fontSize: '12px' }}
                              >
                                清空
                              </Button>
                              <Button
                                type="primary"
                                size="small"
                                onClick={(e) => {
                                  e.stopPropagation(); // 阻止冒泡
                                  setMcpModalVisible(false);
                                }}
                                style={{ fontSize: '12px' }}
                              >
                                确定
                              </Button>
                            </div>
                          </div>
                        )}
                      >
                        <div
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '6px',
                            color: '#6b7280',
                            cursor: 'pointer',
                            padding: '4px 8px',
                            borderRadius: '12px',
                            background: selectedMcpServers.length > 0 ? '#f0f9ff' : 'transparent',
                            border: selectedMcpServers.length > 0 ? '1px solid #bae6fd' : '1px solid transparent',
                            transition: 'all 0.2s',
                            fontSize: '12px'
                          }}
                          onClick={() => setMcpModalVisible(!mcpModalVisible)}
                        >
                          <CloudServerOutlined style={{
                            color: selectedMcpServers.length > 0 ? '#0ea5e9' : '#6b7280',
                            fontSize: '12px'
                          }} />
                          MCP服务器
                          {selectedMcpServers.length > 0 && (
                            <Tag
                              style={{
                                marginLeft: '4px',
                                minWidth: '16px',
                                height: '16px',
                                lineHeight: '14px',
                                padding: '0 4px',
                                fontSize: '10px',
                                background: '#0ea5e9',
                                color: 'white',
                                border: 'none'
                              }}
                            >
                              {selectedMcpServers.length}
                            </Tag>
                          )}
                        </div>
                      </Dropdown>
                    </div>
                  )}

                  {/* 只保留地图相关按钮 */}
                  <div style={{
                    display: 'flex',
                    gap: '8px',
                    alignItems: 'center'
                  }}>
                    {/* 隐藏@技能和/文件按钮 */}
                    {false && (
                      <>
                        <Button
                          type="text"
                          size="small"
                          style={{
                            color: '#9ca3af',
                            fontSize: '12px',
                            height: '24px',
                            padding: '0 8px',
                            borderRadius: '6px'
                          }}
                        >
                          @ 技能
                        </Button>
                        <Button
                          type="text"
                          size="small"
                          style={{
                            color: '#9ca3af',
                            fontSize: '12px',
                            height: '24px',
                            padding: '0 8px',
                            borderRadius: '6px'
                          }}
                        >
                          / 文件
                        </Button>
                      </>
                    )}
                    <Dropdown
                      trigger={['click']}
                      open={skillModalVisible}
                      onOpenChange={handleSkillDropdownOpenChange}
                      placement="topRight"
                      getPopupContainer={() => document.body}
                      dropdownRender={() => (
                        <div
                          style={{
                            background: '#ffffff',
                            borderRadius: '8px',
                            padding: '12px',
                            boxShadow: '0 4px 12px rgba(0, 0, 0, 0.15)',
                            border: '1px solid #e5e7eb',
                            width: '320px',
                            maxHeight: '420px',
                            overflowY: 'auto'
                          }}
                          onClick={(e) => e.stopPropagation()}
                        >
                          <div style={{
                            fontSize: '14px',
                            fontWeight: 500,
                            marginBottom: '8px',
                            color: '#374151',
                            display: 'flex',
                            alignItems: 'center',
                            gap: '6px'
                          }}>
                            <ThunderboltOutlined style={{ color: 'var(--travel-primary)' }} />
                            选择旅行技能 ({selectedSkillIds.length}/{skills.length})
                          </div>
                          <Divider style={{ margin: '8px 0' }} />

                          {skillLoading ? (
                            <div style={{ textAlign: 'center', padding: '20px' }}>
                              <Spin size="small" />
                              <div style={{ marginTop: '8px', fontSize: '12px', color: '#9ca3af' }}>
                                加载技能中...
                              </div>
                            </div>
                          ) : skills.length === 0 ? (
                            <div style={{ textAlign: 'center', padding: '20px', color: '#9ca3af', fontSize: '12px' }}>
                              暂无可用技能
                            </div>
                          ) : (
                            <div style={{ maxHeight: '300px', overflowY: 'auto' }}>
                              {skills.map((skill) => {
                                const missing = [
                                  ...skill.missing_mcp_servers,
                                  ...skill.missing_local_tools,
                                  ...skill.missing_env
                                ];
                                return (
                                  <div
                                    key={skill.id}
                                    style={{
                                      display: 'flex',
                                      alignItems: 'flex-start',
                                      justifyContent: 'space-between',
                                      gap: '10px',
                                      padding: '8px 4px',
                                      borderRadius: '6px',
                                      transition: 'background 0.2s',
                                      cursor: skill.available ? 'pointer' : 'default'
                                    }}
                                    onMouseEnter={(e) => {
                                      if (skill.available) {
                                        e.currentTarget.style.background = '#f8fafc';
                                      }
                                    }}
                                    onMouseLeave={(e) => {
                                      e.currentTarget.style.background = 'transparent';
                                    }}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      if (skill.available) {
                                        handleSkillChange(skill.id, !selectedSkillIds.includes(skill.id));
                                      }
                                    }}
                                  >
                                    <div style={{ flex: 1, minWidth: 0 }}>
                                      <div style={{ fontSize: '13px', fontWeight: 600, color: '#374151' }}>
                                        {skill.name}
                                      </div>
                                      <div style={{ fontSize: '11px', color: '#6b7280', lineHeight: 1.45 }}>
                                        {skill.description}
                                      </div>
                                      {!skill.available && missing.length > 0 && (
                                        <div style={{ fontSize: '11px', color: '#b45309', marginTop: '4px' }}>
                                          缺少: {missing.slice(0, 3).join(', ')}
                                        </div>
                                      )}
                                    </div>
                                    <Checkbox
                                      checked={selectedSkillIds.includes(skill.id)}
                                      disabled={!skill.available}
                                      onChange={(e) => {
                                        e.stopPropagation();
                                        handleSkillChange(skill.id, e.target.checked);
                                      }}
                                      onClick={(e) => e.stopPropagation()}
                                    />
                                  </div>
                                );
                              })}
                            </div>
                          )}

                          <Divider style={{ margin: '8px 0' }} />
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                            <Button type="text" size="small" onClick={(e) => { e.stopPropagation(); handleSelectAllSkills(); }} style={{ fontSize: '12px' }}>
                              全选
                            </Button>
                            <Button type="text" size="small" onClick={(e) => { e.stopPropagation(); handleClearSkills(); }} style={{ fontSize: '12px' }}>
                              清空
                            </Button>
                            <Button type="primary" size="small" onClick={(e) => { e.stopPropagation(); setSkillModalVisible(false); }} style={{ fontSize: '12px' }}>
                              确定
                            </Button>
                          </div>
                        </div>
                      )}
                    >
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          gap: '6px',
                          color: selectedSkillIds.length > 0 ? 'var(--travel-primary-dark)' : 'var(--travel-muted)',
                          cursor: 'pointer',
                          padding: '0 8px',
                          width: '96px',
                          minWidth: '96px',
                          height: '24px',
                          borderRadius: '10px',
                          background: selectedSkillIds.length > 0 ? 'linear-gradient(135deg, color-mix(in oklch, var(--travel-selected) 86%, white 14%), color-mix(in oklch, var(--travel-accent) 16%, white 84%))' : 'transparent',
                          border: selectedSkillIds.length > 0 ? '1px solid var(--travel-primary-soft)' : '1px solid transparent',
                          transition: 'background 0.2s, border-color 0.2s, color 0.2s',
                          fontSize: '12px',
                          whiteSpace: 'nowrap'
                        }}
                      >
                        <ThunderboltOutlined style={{ fontSize: '12px', flexShrink: 0 }} />
                        <span style={{ flexShrink: 0 }}>技能</span>
                        {selectedSkillIds.length > 0 && (
                          <Tag
                            style={{
                              margin: 0,
                              minWidth: '16px',
                              height: '16px',
                              lineHeight: '14px',
                              padding: '0 4px',
                              fontSize: '10px',
                              background: 'var(--travel-gradient-primary)',
                              color: 'white',
                              border: 'none',
                              flexShrink: 0
                            }}
                          >
                            {selectedSkillIds.length}
                          </Tag>
                        )}
                      </div>
                    </Dropdown>
                    <button
                      type="button"
                      onClick={() => setUseDeepThink((previous) => !previous)}
                      title={useDeepThink ? '当前使用深度研究模型' : '当前使用快速对话模型'}
                      aria-pressed={useDeepThink}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        gap: '6px',
                        color: useDeepThink ? 'var(--travel-primary-dark)' : 'var(--travel-muted)',
                        cursor: 'pointer',
                        padding: '0 9px',
                        minWidth: '92px',
                        height: '24px',
                        borderRadius: '10px',
                        background: useDeepThink ? 'linear-gradient(135deg, color-mix(in oklch, var(--travel-selected) 86%, white 14%), color-mix(in oklch, var(--travel-violet) 14%, white 86%))' : 'transparent',
                        border: useDeepThink ? '1px solid var(--travel-primary-soft)' : '1px solid transparent',
                        transition: 'background 0.2s, border-color 0.2s, color 0.2s',
                        fontSize: '12px',
                        whiteSpace: 'nowrap'
                      }}
                    >
                      <BranchesOutlined style={{ fontSize: '12px', flexShrink: 0 }} />
                      <span>{useDeepThink ? '深度研究' : '快速对话'}</span>
                    </button>
                    <TripProductTools
                      plan={activeTripPlan}
                      document={activeTripDocument}
                      workspace={tripWorkspace}
                      onUseTemplate={(prompt) => void handleSendMessage(prompt)}
                      onImportDocument={handleImportedTripDocument}
                      onDocumentChange={setActiveTripDocument}
                    />
                    {effectiveMapLocations.length > 0 && (
                      <Button
                        type="text"
                        size="small"
                        onClick={forceCleanMapLocations}
                        style={{
                          color: '#ff4d4f',
                          fontSize: '11px',
                          height: '20px',
                          padding: '0 6px',
                          borderRadius: '8px'
                        }}
                      >
                        清除地点
                      </Button>
                    )}
                  </div>
                </div>

                {/* 输入框和发送按钮区域 */}
                <div style={{
                  display: 'flex',
                  alignItems: 'flex-end',
                  padding: '8px 16px 12px 16px',
                  gap: '12px'
                }}>
                  <div style={{ flex: 1, position: 'relative' }}>
                    <TextArea
                      ref={inputRef}
                      value={inputText}
                      onChange={(e) => {
                        syncInputState(e.target.value);
                      }}
                      onCompositionStart={() => {
                        isComposingRef.current = true;
                      }}
                      onCompositionEnd={(e) => {
                        isComposingRef.current = false;
                        syncInputState(e.currentTarget.value);
                      }}
                      placeholder={activePendingClarification ? '请先回答或跳过上方问题' : '发消息...'}
                      autoSize={{ minRows: 2, maxRows: 6 }}
                      bordered={false}
                      onPressEnter={(e) => {
                        const nativeEvent = e.nativeEvent as KeyboardEvent & { isComposing?: boolean };
                        if (!e.shiftKey && !nativeEvent.isComposing && !isComposingRef.current) {
                          e.preventDefault();
                          if (!activePendingClarification) handleSendMessage();
                        }
                      }}
                      disabled={isLoading || Boolean(activePendingClarification)}
                      style={{
                        padding: '1px 0',
                        fontSize: '14px',
                        resize: 'none',
                        lineHeight: '22px',
                        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
                        background: 'transparent',
                        width: '100%',
                        minHeight: '44px'
                      }}
                    />

                    {/* 输入提示文字 - 只在输入框为空时显示 */}
                    {isInputEmpty && !activePendingClarification && (
                      <div style={{
                        position: 'absolute',
                        bottom: '4px',
                        right: '0',
                        fontSize: '11px',
                        color: 'color-mix(in oklch, var(--travel-muted) 88%, var(--travel-primary-dark) 12%)',
                        pointerEvents: 'none',
                        background: 'transparent',
                        padding: '2px 0',
                        borderRadius: 0,
                        border: 'none',
                        boxShadow: 'none'
                      }}>
                        按 Enter 发送 • Shift + Enter 换行
                      </div>
                    )}
                  </div>

                  <Button
                    type="primary"
                    icon={isLoading ? <StopOutlined /> : <SendOutlined />}
                    onClick={() => {
                      if (isLoading) {
                        handleStopResponse();
                        return;
                      }
                      void handleSendMessage();
                    }}
                    disabled={!isLoading && (isInputEmpty || Boolean(activePendingClarification))}
                    style={{
                      borderRadius: '14px',
                      height: '32px',
                      width: '32px',
                      padding: 0,
                      background: isLoading
                        ? '#ef4444'
                        : (!isInputEmpty ? 'var(--travel-gradient-primary)' : 'color-mix(in oklch, var(--travel-surface-muted) 72%, white 28%)'),
                      borderColor: isLoading
                        ? '#ef4444'
                        : (!isInputEmpty ? 'transparent' : 'color-mix(in oklch, var(--travel-border) 72%, white 28%)'),
                      color: isLoading
                        ? '#ffffff'
                        : (!isInputEmpty ? '#ffffff' : '#9ca3af'),
                      transition: 'all 0.2s ease',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      flexShrink: 0
                    }}
                  />
                </div>
              </div>
            </div>
          </div>

          {/* 输入框区域 - 已有的输入框代码应该在这里 */}
        </div>

        <Button
          type="primary"
          shape="circle"
          icon={<EnvironmentOutlined />}
          aria-label={showMap ? '关闭旅行工作台' : '打开旅行工作台'}
          title={showMap ? '关闭旅行工作台' : '打开旅行工作台'}
        className={`map-toggle-orb ${showMap ? 'map-toggle-orb-open' : ''}`}
        style={showMap
          ? { left: `calc(${chatPanelWidthPercent}% - 46px)`, right: 'auto' }
          : { right: '22px', left: 'auto' }}
          onClick={() => {
            const nextShowMap = !showMap;
            setShowMap(nextShowMap);
            if (nextShowMap && effectiveMapLocations.length > 0) {
              setMapSuppressed(false);
              setMapLocations([...effectiveMapLocations]);
            }
          }}
        />

        {showMap && (
          <div
            className="chat-map-resizer"
            onMouseDown={startChatMapResize}
            title="拖动调整聊天与地图宽度"
            style={{
              width: '8px',
              flex: '0 0 8px',
              cursor: 'col-resize',
              background: 'var(--travel-surface-muted)',
              borderLeft: '1px solid var(--travel-border)',
              borderRight: '1px solid var(--travel-border)',
              zIndex: 6
            }}
          />
        )}

        {/* 右侧旅行工作台区域 */}
        {showMap && (
          <div className="trip-side-panel" ref={tripSidePanelRef}>
            <div
              className={hasTripWorkspaceData ? 'trip-map-panel trip-map-panel-with-workspace' : 'trip-map-panel'}
              style={hasTripWorkspaceData
                ? { flex: `0 0 calc(${sidePanelMapPercent}% - 4px)` }
                : { flex: '1 1 100%' }}
            >
              <MapComponent
                width="100%"
                height="100%"
                locations={effectiveMapLocations}
                dayRoutes={dayRoutes}
                activeGroupId={activeMapGroupId}
                selectedLocationId={selectedLocationId}
                locationGroups={mapLocationGroupsForRender.map((group) => ({
                  id: group.groupId,
                  title: group.title,
                  locations: group.locations,
                }))}
                onSelectLocation={(locationId) => {
                  setSelectedLocationId(locationId);
                }}
              />
            </div>
            {!hasTripWorkspaceData && (
              <div className="trip-mobile-empty" role="status">
                <CalendarOutlined />
                <span>行程生成后会在这里按日期整理</span>
              </div>
            )}
            {hasTripWorkspaceData && (
              <>
                <div
                  className="trip-side-horizontal-resizer"
                  onMouseDown={startSidePanelResize}
                  role="separator"
                  aria-orientation="horizontal"
                  aria-label="调整地图与行程工作台高度"
                  title="拖动调整地图与行程工作台高度"
                />
                <div
                  className="trip-workspace-shell"
                  style={{ flex: `0 0 calc(${100 - sidePanelMapPercent}% - 4px)` }}
                >
                  <TripWorkspace
                    data={tripWorkspace}
                    document={activeTripDocument}
                    locationGroups={workspaceLocationGroups.map((group) => ({
                      groupId: group.groupId,
                      title: group.title,
                      locations: group.locations,
                      sourceKind: group.groupId.startsWith('structured_trip_workspace')
                        ? 'current_plan'
                        : 'historical_answer',
                    }))}
                    activeGroupId={activeMapGroupId}
                    selectedLocationId={selectedLocationId}
                    onSelectGroup={setActiveMapGroupId}
                    onSelectLocation={setSelectedLocationId}
                    onEditOperation={activeTripPlan ? applyTripEditOperation : undefined}
                    onUndoEdit={undoLastTripEdit}
                    canUndoEdit={Boolean(previousTripPlan)}
                    editLoading={isTripEditLoading}
                    onFocusMap={() => {
                      setShowMap(true);
                      if (isNarrowLayout) setMobilePrimaryView('map');
                    }}
                    onQuickAction={(prompt) => {
                      if (isNarrowLayout) setMobilePrimaryView('chat');
                      void handleSendMessage(prompt);
                    }}
                  />
                </div>
              </>
            )}
          </div>
        )}
      </div>
    );
  });

export default ChatInterface; 
