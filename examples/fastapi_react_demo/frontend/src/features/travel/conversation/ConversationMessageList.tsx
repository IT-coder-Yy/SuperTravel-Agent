import { Button, Typography } from 'antd';
import AgentRunTimeline, { type AgentRunTimelineState } from '../agent/AgentRunTimeline';
import type { PlanningStatus } from '../../../components/planningState';
import type { Message, MessageGroup } from '../../../components/ChatInterface';

const { Text } = Typography;

interface ConversationMessageListProps {
  messageGroups: MessageGroup[];
  activeAgentTimeline: AgentRunTimelineState | null;
  historicalAgentTimelineUnavailable?: boolean;
  planningStatus: PlanningStatus;
  answerPageByUserMessageId: Record<string, number>;
  renderMessage: (message: Message, options?: { enableActions?: boolean }) => React.ReactNode;
  renderLegacyPlanningProgress: (messages: Message[]) => React.ReactNode;
  getFinalAnswers: (group: MessageGroup) => Message[];
  onAnswerPageChange: (userMessageId: string, pageIndex: number) => void;
}

const ConversationMessageList = ({
  messageGroups,
  activeAgentTimeline,
  historicalAgentTimelineUnavailable = false,
  planningStatus,
  answerPageByUserMessageId,
  renderMessage,
  renderLegacyPlanningProgress,
  getFinalAnswers,
  onAnswerPageChange,
}: ConversationMessageListProps) => (
  <>
    {messageGroups.map((group, groupIndex) => {
      const finalAnswers = getFinalAnswers(group);
      const isLatestGroup = groupIndex === messageGroups.length - 1;
      const userMessageId = group.userMessage?.id || finalAnswers[0]?.id || `history-${groupIndex}`;
      const maxIndex = finalAnswers.length - 1;
      const requestedIndex = answerPageByUserMessageId[userMessageId];
      const activeIndex = typeof requestedIndex === 'number'
        ? Math.max(0, Math.min(maxIndex, requestedIndex))
        : maxIndex;

      return (
        <div key={`group-${groupIndex}`} style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          {group.userMessage && renderMessage(group.userMessage)}

          {isLatestGroup && activeAgentTimeline ? (
            <AgentRunTimeline timeline={activeAgentTimeline} planningStatus={planningStatus} />
          ) : isLatestGroup && historicalAgentTimelineUnavailable ? (
            <section className="agent-timeline-unavailable" aria-label="智能体规划过程">
              <Text strong style={{ color: 'var(--travel-ink)' }}>智能体规划过程</Text>
              <Text style={{ fontSize: '12px', color: 'var(--travel-muted)' }}>该旅程在阶段摘要功能上线前生成，暂无可复看的阶段记录；后续重新规划会自动保留五个阶段的安全摘要。</Text>
            </section>
          ) : renderLegacyPlanningProgress(group.deepThinkMessages)}

          {finalAnswers.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {renderMessage(finalAnswers[activeIndex], { enableActions: isLatestGroup })}

              {finalAnswers.length > 1 && (
                <div style={{
                  display: 'flex',
                  justifyContent: 'flex-start',
                  alignItems: 'center',
                  gap: '8px',
                  marginLeft: '44px',
                }}>
                  <Button
                    size="small"
                    onClick={() => onAnswerPageChange(userMessageId, Math.max(0, activeIndex - 1))}
                    disabled={activeIndex <= 0}
                  >
                    上一版
                  </Button>

                  <Text style={{ fontSize: '12px', color: '#6b7280' }}>
                    回答版本 {activeIndex + 1}/{finalAnswers.length}
                  </Text>

                  <Button
                    size="small"
                    onClick={() => onAnswerPageChange(userMessageId, Math.min(maxIndex, activeIndex + 1))}
                    disabled={activeIndex >= maxIndex}
                  >
                    下一版
                  </Button>
                </div>
              )}
            </div>
          )}
        </div>
      );
    })}
  </>
);

export default ConversationMessageList;
