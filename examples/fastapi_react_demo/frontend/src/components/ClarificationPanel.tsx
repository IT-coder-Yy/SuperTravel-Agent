import React, { useEffect, useRef, useState } from 'react';
import { Button, Input, Space, Tag } from 'antd';
import { CheckOutlined, LoadingOutlined, SendOutlined } from '@ant-design/icons';

export interface ClarificationQuestion {
  id: string;
  field: string;
  question: string;
  reason: string;
  options: string[];
  allow_custom: boolean;
  allow_skip?: boolean;
  profile_default_value?: string | null;
}

export interface ClarificationPanelProps {
  question: ClarificationQuestion;
  answeredCount?: number;
  loading?: boolean;
  profileDefaultValue?: string | null;
  onSubmit: (answer: Record<string, string>) => void;
  onSkip?: () => void;
}

const ClarificationPanel: React.FC<ClarificationPanelProps> = ({
  question,
  answeredCount = 0,
  loading = false,
  profileDefaultValue,
  onSubmit,
  onSkip,
}) => {
  const [selectedAnswer, setSelectedAnswer] = useState('');
  const [customAnswer, setCustomAnswer] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const submissionLockedRef = useRef(false);
  const interactionLocked = loading || isSubmitting;
  const normalizedProfileDefaultValue = profileDefaultValue?.trim() || '';

  useEffect(() => {
    setSelectedAnswer('');
    setCustomAnswer('');
    setIsSubmitting(false);
    submissionLockedRef.current = false;
  }, [question.id, question.field]);

  useEffect(() => {
    if (!loading) {
      setIsSubmitting(false);
      submissionLockedRef.current = false;
    }
  }, [loading]);

  const beginSubmission = (submit: () => void) => {
    if (loading || submissionLockedRef.current) return;

    submissionLockedRef.current = true;
    setIsSubmitting(true);
    submit();
  };

  const submitAnswer = (value: string) => {
    const normalizedValue = value.trim();
    if (!normalizedValue) return;

    beginSubmission(() => onSubmit({ [question.field]: normalizedValue }));
  };

  const submitCustomAnswer = () => {
    submitAnswer(customAnswer);
  };

  const titleId = `clarification-title-${question.id}`;
  const reasonId = `clarification-reason-${question.id}`;
  const customAnswerLabel = `${question.question}的自定义答案`;

  return (
    <section
      className="clarification-panel"
      aria-labelledby={titleId}
      aria-describedby={reasonId}
      aria-busy={interactionLocked}
    >
      <div className="clarification-panel-header">
        <div>
          <h3>补充关键信息</h3>
          <p>每次只确认一项；补充后继续分析，必要时在这里显示下一项。</p>
        </div>
        {answeredCount > 0 && <Tag color="blue">已补充 {answeredCount} 项</Tag>}
      </div>

      <div
        key={question.id}
        className="clarification-question-card clarification-question-card-enter"
      >
        <div id={titleId} className="clarification-question-title">
          {question.question}
        </div>
        <div id={reasonId} className="clarification-question-reason">
          {question.reason}
        </div>

        {question.options.length > 0 && (
          <div
            className="clarification-option-row"
            role="group"
            aria-label={`${question.question}的推荐选项`}
          >
            {question.options.map((option) => {
              const active = selectedAnswer === option;
              return (
                <Button
                  key={option}
                  size="middle"
                  type={active ? 'primary' : 'default'}
                  icon={active ? <CheckOutlined /> : undefined}
                  disabled={interactionLocked}
                  aria-pressed={active}
                  aria-label={`选择“${option}”`}
                  onClick={() => {
                    setSelectedAnswer(option);
                    setCustomAnswer('');
                    submitAnswer(option);
                  }}
                  block
                >
                  {option}
                </Button>
              );
            })}
          </div>
        )}

        {question.allow_custom && (
          <div className="clarification-custom-answer">
            <Input
              value={customAnswer}
              placeholder="输入其他答案"
              aria-label={customAnswerLabel}
              disabled={interactionLocked}
              onChange={(event) => {
                setSelectedAnswer('');
                setCustomAnswer(event.target.value);
              }}
              onPressEnter={(event) => {
                if (!event.nativeEvent.isComposing) {
                  submitCustomAnswer();
                }
              }}
            />
            <Button
              className="clarification-submit"
              type="primary"
              icon={<SendOutlined />}
              loading={interactionLocked}
              disabled={!customAnswer.trim() || interactionLocked}
              aria-label="提交自定义答案并继续分析"
              onClick={submitCustomAnswer}
            >
              使用此答案
            </Button>
          </div>
        )}

        <Space size={4} wrap aria-label="其他回答方式">
          {normalizedProfileDefaultValue && (
            <Button
              type="text"
              size="small"
              disabled={interactionLocked}
              aria-label={`按常用偏好回答：${normalizedProfileDefaultValue}`}
              onClick={() => submitAnswer(normalizedProfileDefaultValue)}
            >
              按常用偏好（{normalizedProfileDefaultValue}）
            </Button>
          )}
          {onSkip && (
            <Button
              type="text"
              size="small"
              disabled={interactionLocked}
              aria-label={`跳过问题：${question.question}`}
              onClick={() => beginSubmission(onSkip)}
            >
              跳过此项
            </Button>
          )}
        </Space>
      </div>

      <div className="clarification-panel-status" role="status" aria-live="polite">
        {interactionLocked ? (
          <>
            <LoadingOutlined aria-hidden="true" />
            <span>正在确认这项信息...</span>
          </>
        ) : (
          <>
            <span className="clarification-panel-status-dot" aria-hidden="true" />
            <span>选择推荐答案，或输入更准确的信息</span>
          </>
        )}
      </div>
    </section>
  );
};

export default ClarificationPanel;
