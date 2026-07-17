import React, { useEffect, useMemo, useState } from 'react';
import { Button, Popconfirm, Tag, Typography } from 'antd';
import {
  CheckOutlined,
  CompassOutlined,
  DeleteOutlined,
  SaveOutlined,
  UserOutlined,
} from '@ant-design/icons';
import {
  defaultUserTravelProfile,
  normalizeUserTravelProfile,
  UserTravelProfile,
  useUserTravelProfile,
} from '../hooks/useUserTravelProfile';

const { Title, Paragraph } = Typography;

type ProfileGroupKey =
  | 'travel_preference'
  | 'budget'
  | 'people'
  | 'travel_style'
  | 'food'
  | 'pace'
  | 'hotel'
  | 'transport'
  | 'disliked'
  | 'accessibility';

interface ProfileGroup {
  key: ProfileGroupKey;
  title: string;
  description: string;
  mode: 'single' | 'multiple';
  options: string[];
}

const profileGroups: ProfileGroup[] = [
  {
    key: 'travel_preference',
    title: '旅游偏好',
    description: '可多选，决定行程内容侧重点。',
    mode: 'multiple',
    options: ['自然风光', '历史文化', '美食', '亲子', '拍照', '购物', '博物馆'],
  },
  {
    key: 'budget',
    title: '预算',
    description: '单选，作为未明确预算时的长期倾向。',
    mode: 'single',
    options: ['经济', '标准', '舒适', '高端'],
  },
  {
    key: 'people',
    title: '人员类别',
    description: '单选，选择最常见的同行方式。',
    mode: 'single',
    options: ['独行', '情侣', '朋友', '亲子', '带老人', '商务'],
  },
  {
    key: 'travel_style',
    title: '旅游风格',
    description: '可多选，用于调整路线组织方式。',
    mode: 'multiple',
    options: ['深度游', '打卡游', '慢旅行', '小众路线', '网红路线'],
  },
  {
    key: 'food',
    title: '饮食偏好',
    description: '可多选，帮助筛选餐厅和当地体验。',
    mode: 'multiple',
    options: ['清淡', '辣', '素食', '清真', '海鲜', '咖啡甜品'],
  },
  {
    key: 'pace',
    title: '旅行节奏',
    description: '单选，影响每日活动密度。',
    mode: 'single',
    options: ['轻松', '适中', '紧凑'],
  },
  {
    key: 'hotel',
    title: '住宿偏好',
    description: '单选，影响住宿区域和类型推荐。',
    mode: 'single',
    options: ['地铁附近', '景区附近', '亲子酒店', '民宿', '高星酒店'],
  },
  {
    key: 'transport',
    title: '交通偏好',
    description: '单选，影响城市内路线与换乘安排。',
    mode: 'single',
    options: ['公共交通优先', '自驾优先', '打车优先', '步行骑行优先'],
  },
  {
    key: 'disliked',
    title: '避免项',
    description: '可多选，规划时会尽量规避。',
    mode: 'multiple',
    options: ['早起', '排队', '爬山', '夜生活', '长距离步行'],
  },
  {
    key: 'accessibility',
    title: '无障碍需求',
    description: '可多选，用于筛选路线、场所和交通。',
    mode: 'multiple',
    options: ['无障碍通道', '轮椅友好', '少走楼梯', '视听辅助', '婴儿车友好'],
  },
];

const travelPreferenceOptions = profileGroups.find((group) => group.key === 'travel_preference')?.options || [];
const travelStyleOptions = profileGroups.find((group) => group.key === 'travel_style')?.options || [];
const knownTravelStyleValues = new Set([...travelPreferenceOptions, ...travelStyleOptions]);

const valuesForGroup = (profile: UserTravelProfile, key: ProfileGroupKey): string[] => {
  switch (key) {
    case 'travel_preference':
      return profile.travel_style.filter((item) => travelPreferenceOptions.includes(item));
    case 'travel_style':
      return profile.travel_style.filter((item) => travelStyleOptions.includes(item));
    case 'budget':
      return profile.preferred_budget_level ? [profile.preferred_budget_level] : [];
    case 'people':
      return profile.default_people_type ? [profile.default_people_type] : [];
    case 'food':
      return profile.dietary_preferences;
    case 'pace':
      return profile.pace ? [profile.pace] : [];
    case 'hotel':
      return profile.hotel_preference ? [profile.hotel_preference] : [];
    case 'transport':
      return profile.transport_preference ? [profile.transport_preference] : [];
    case 'disliked':
      return profile.disliked_items;
    case 'accessibility':
      return profile.accessibility_needs;
  }
};

const updateList = (items: string[], option: string, checked: boolean) => (
  checked ? Array.from(new Set([...items, option])) : items.filter((item) => item !== option)
);

const updateGroup = (
  profile: UserTravelProfile,
  key: ProfileGroupKey,
  option: string,
  checked: boolean
): UserTravelProfile => {
  const next = { ...profile };

  switch (key) {
    case 'travel_preference':
    case 'travel_style':
      next.travel_style = updateList(profile.travel_style, option, checked);
      break;
    case 'budget':
      next.preferred_budget_level = checked ? option : undefined;
      break;
    case 'people':
      next.default_people_type = checked ? option : undefined;
      break;
    case 'food':
      next.dietary_preferences = updateList(profile.dietary_preferences, option, checked);
      break;
    case 'pace':
      next.pace = checked ? option : undefined;
      break;
    case 'hotel':
      next.hotel_preference = checked ? option : undefined;
      break;
    case 'transport':
      next.transport_preference = checked ? option : undefined;
      break;
    case 'disliked':
      next.disliked_items = updateList(profile.disliked_items, option, checked);
      break;
    case 'accessibility':
      next.accessibility_needs = updateList(profile.accessibility_needs, option, checked);
      break;
  }

  return normalizeUserTravelProfile(next);
};

const comparableProfile = (profile: UserTravelProfile) => JSON.stringify({
  ...normalizeUserTravelProfile(profile),
  updated_at: undefined,
});

const selectedValues = (profile: UserTravelProfile) => [
  ...profile.travel_style,
  profile.preferred_budget_level,
  profile.default_people_type,
  ...profile.dietary_preferences,
  profile.pace,
  profile.hotel_preference,
  profile.transport_preference,
  ...profile.disliked_items,
  ...profile.accessibility_needs,
].filter((item): item is string => Boolean(item));

const UserProfile: React.FC = () => {
  const { profile, setProfile, clearProfile } = useUserTravelProfile();
  const [draft, setDraft] = useState<UserTravelProfile>(() => normalizeUserTravelProfile(profile));
  const [feedback, setFeedback] = useState('');

  useEffect(() => {
    setDraft(normalizeUserTravelProfile(profile));
  }, [profile]);

  const summaryValues = useMemo(() => selectedValues(draft), [draft]);
  const legacyTravelValues = useMemo(
    () => draft.travel_style.filter((item) => !knownTravelStyleValues.has(item)),
    [draft.travel_style]
  );
  const isDirty = comparableProfile(draft) !== comparableProfile(profile);

  const handleChoiceChange = (group: ProfileGroup, option: string, checked: boolean) => {
    setDraft((previous) => updateGroup(previous, group.key, option, checked));
    setFeedback('');
  };

  const handleSave = () => {
    setProfile(draft);
    setFeedback('画像已保存，将用于后续行程规划。');
  };

  const handleClear = () => {
    clearProfile();
    setDraft(normalizeUserTravelProfile(defaultUserTravelProfile));
    setFeedback('画像已清空，后续规划不会预设个人偏好。');
  };

  return (
    <div className="travel-page profile-page">
      <div className="travel-page-header">
        <div>
          <Title level={2}>用户画像</Title>
          <Paragraph>只保存长期稳定的偏好；本次对话中的明确要求始终优先。</Paragraph>
        </div>
        <div className="profile-header-actions">
          <Popconfirm
            title="清空用户画像？"
            description="所有已保存的旅行偏好都会被移除。"
            okText="确认清空"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={handleClear}
          >
            <Button danger icon={<DeleteOutlined />}>清空画像</Button>
          </Popconfirm>
          <Button type="primary" icon={<SaveOutlined />} disabled={!isDirty} onClick={handleSave}>
            保存画像
          </Button>
        </div>
      </div>

      <div className="profile-summary" aria-live="polite">
        <div className="profile-avatar" aria-hidden="true"><UserOutlined /></div>
        <div className="profile-summary-copy">
          <h3>{summaryValues.length > 0 ? '默认旅行画像' : '尚未设置旅行画像'}</h3>
          <p>
            {summaryValues.length > 0
              ? `当前草稿包含 ${summaryValues.length} 项偏好，保存后用于下一次规划。`
              : '新用户不会自动预选任何预算、人员类别、风格或节奏。'}
          </p>
          {feedback && <span className="profile-feedback" role="status">{feedback}</span>}
        </div>
        <CompassOutlined className="profile-summary-mark" aria-hidden="true" />
      </div>

      <div className="profile-grid">
        {profileGroups.map((group) => {
          const selected = valuesForGroup(draft, group.key);
          return (
            <fieldset className="profile-card" key={group.key}>
              <legend>{group.title}</legend>
              <p>{group.description}</p>
              <div className="profile-option-row">
                {group.options.map((option) => {
                  const active = selected.includes(option);
                  const inputId = `profile-${group.key}-${option}`;
                  return (
                    <label className={active ? 'profile-option active' : 'profile-option'} key={option} htmlFor={inputId}>
                      <input
                        className="profile-choice-input"
                        id={inputId}
                        type={group.mode === 'single' ? 'radio' : 'checkbox'}
                        name={`profile-${group.key}`}
                        checked={active}
                        onChange={(event) => handleChoiceChange(group, option, event.target.checked)}
                      />
                      <CheckOutlined className="profile-option-check" aria-hidden="true" />
                      <span>{option}</span>
                    </label>
                  );
                })}
              </div>
            </fieldset>
          );
        })}
      </div>

      <section className="profile-selection-summary" aria-labelledby="profile-selection-title">
        <div className="profile-selection-heading">
          <div>
            <h3 id="profile-selection-title">当前选择</h3>
            <p>{isDirty ? '存在未保存的修改。' : '当前选择已与本地保存内容同步。'}</p>
          </div>
          <span className={isDirty ? 'profile-save-state pending' : 'profile-save-state'}>
            {isDirty ? '未保存' : '已保存'}
          </span>
        </div>
        <div className="profile-tag-row">
          {summaryValues.length > 0
            ? summaryValues.map((item, index) => <Tag key={`${item}-${index}`}>{item}</Tag>)
            : <span className="profile-empty-text">暂无偏好</span>}
        </div>
        {legacyTravelValues.length > 0 && (
          <p className="profile-legacy-note">已保留旧版画像中的自定义偏好：{legacyTravelValues.join('、')}</p>
        )}
      </section>
    </div>
  );
};

export default UserProfile;
