import { useEffect, useMemo, useState } from 'react';
import dayjs, { type Dayjs } from 'dayjs';
import { Button, Checkbox, DatePicker, Form, Input, InputNumber, Radio } from 'antd';
import { CompassOutlined, EnvironmentOutlined, LoadingOutlined } from '@ant-design/icons';
import { apiClient } from '../../../services/apiClient';
import { useUserTravelProfile } from '../../../hooks/useUserTravelProfile';
import type { TripCreateRequest } from './tripCreateTypes';
import {
  MAX_TRIP_DAYS,
  buildTripCreationPrompt,
  validateTripCreateRequest,
} from './tripCreateValidation';
import './TripCreateForm.css';

const { RangePicker } = DatePicker;

interface TripCreateFormValues {
  origin: string;
  destination: string;
  dates: [Dayjs, Dayjs];
  adults: number;
  children: number;
  seniors: number;
  budget: number;
  partyType?: TripCreateRequest['partyType'];
  preferences: string[];
}

type LocationState = 'locating' | 'resolved' | 'fallback' | 'manual';

export interface TripCreateFormProps {
  disabled?: boolean;
  initialTemplate?: Partial<TripCreateRequest> & { days?: number };
  onCreate: (request: TripCreateRequest, prompt: string) => Promise<void> | void;
}

const preferenceOptions = [
  '人文历史',
  '自然风光',
  '当地美食',
  '轻松慢游',
  '亲子体验',
  '摄影打卡',
];

const partyTypeOptions: Array<{ label: string; value: NonNullable<TripCreateRequest['partyType']> }> = [
  { label: '独自旅行', value: '独自' },
  { label: '情侣', value: '情侣' },
  { label: '朋友', value: '朋友' },
  { label: '亲子', value: '亲子' },
  { label: '家庭', value: '家庭' },
];

const TripCreateForm = ({ disabled = false, initialTemplate, onCreate }: TripCreateFormProps) => {
  const [form] = Form.useForm<TripCreateFormValues>();
  const { profile } = useUserTravelProfile();
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState('');
  const [locationState, setLocationState] = useState<LocationState>('locating');
  const today = useMemo(() => dayjs().startOf('day'), []);
  const profileOrigin = profile.home_city?.trim() || '';
  const initialValues = useMemo<TripCreateFormValues>(() => ({
    origin: initialTemplate?.origin?.trim() || profileOrigin,
    destination: initialTemplate?.destination?.trim() || '',
    dates: [today, today.add(Math.max(1, Math.min(MAX_TRIP_DAYS, Number(initialTemplate?.days) || 3)) - 1, 'day')],
    adults: Number(initialTemplate?.adults) || 2,
    children: Number(initialTemplate?.children) || 0,
    seniors: Number(initialTemplate?.seniors) || 0,
    budget: 6000,
    preferences: (initialTemplate?.preferences || []).filter((item) => preferenceOptions.includes(item)),
  }), [initialTemplate, profileOrigin, today]);

  useEffect(() => {
    let active = true;
    const applyProfileFallback = () => {
      if (!form.isFieldTouched('origin') && profileOrigin) {
        form.setFieldValue('origin', profileOrigin);
      }
      setLocationState(profileOrigin ? 'fallback' : 'manual');
    };

    if (!navigator.geolocation) {
      applyProfileFallback();
      return () => { active = false; };
    }

    setLocationState('locating');
    navigator.geolocation.getCurrentPosition(
      async ({ coords }) => {
        try {
          const result = await apiClient.reverseGeocode(coords.latitude, coords.longitude);
          if (!active) return;
          if (!form.isFieldTouched('origin')) {
            form.setFieldValue('origin', result.city);
          }
          setLocationState('resolved');
        } catch (_error) {
          if (active) applyProfileFallback();
        }
      },
      () => {
        if (active) applyProfileFallback();
      },
      { enableHighAccuracy: false, timeout: 8000, maximumAge: 600000 },
    );

    return () => { active = false; };
  }, [form, profileOrigin]);

  const submit = async (values: TripCreateFormValues) => {
    const request: TripCreateRequest = {
      origin: values.origin,
      destination: values.destination,
      startDate: values.dates[0].format('YYYY-MM-DD'),
      endDate: values.dates[1].format('YYYY-MM-DD'),
      adults: Number(values.adults) || 0,
      children: Number(values.children) || 0,
      seniors: Number(values.seniors) || 0,
      budget: Number(values.budget) || 0,
      partyType: values.partyType,
      preferences: values.preferences || [],
    };
    const validation = validateTripCreateRequest(request, today);
    if (!validation.valid) {
      setFormError(Object.values(validation.errors)[0] || '请检查旅行信息');
      return;
    }

    setFormError('');
    setSubmitting(true);
    try {
      await onCreate(request, buildTripCreationPrompt(request));
    } finally {
      setSubmitting(false);
    }
  };

  const locationHint = {
    locating: '正在获取你当前所在的城市…',
    resolved: '已按当前位置填写，你仍可手动修改。',
    fallback: '定位不可用，已使用用户画像中的常住地。',
    manual: '未获取当前位置，请手动输入出发地。',
  }[locationState];

  return (
    <section className="trip-create-form-shell" aria-labelledby="trip-create-heading">
      <header className="trip-create-heading">
        <span className="trip-create-heading-icon" aria-hidden="true"><CompassOutlined /></span>
        <div className="trip-create-heading-copy">
          <h1 id="trip-create-heading">创建一趟新旅程</h1>
          <p>填写关键条件后直接开始规划，交通、地图和预算会在生成过程中逐步核验。</p>
        </div>
      </header>

      <Form
        form={form}
        layout="vertical"
        initialValues={initialValues}
        onFinish={submit}
        onValuesChange={() => setFormError('')}
        requiredMark={false}
        disabled={disabled || submitting}
        className="trip-create-form"
      >
        <div className="trip-create-route-row">
          <div className="trip-create-origin-field">
            <Form.Item
              name="origin"
              label="出发地"
              rules={[{ required: true, whitespace: true, message: '请输入出发地' }]}
            >
              <Input
                aria-label="出发地"
                aria-busy={locationState === 'locating'}
                placeholder="例如：上海"
                autoComplete="address-level2"
                suffix={locationState === 'locating'
                  ? <LoadingOutlined spin aria-hidden="true" />
                  : <EnvironmentOutlined aria-hidden="true" />}
              />
            </Form.Item>
            <span className={`trip-create-location-hint is-${locationState}`}>{locationHint}</span>
          </div>
          <Form.Item
            name="destination"
            label="目的地"
            dependencies={['origin']}
            rules={[
              { required: true, whitespace: true, message: '请输入目的地' },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  return value?.trim() && value.trim() === getFieldValue('origin')?.trim()
                    ? Promise.reject(new Error('目的地需要与出发地不同'))
                    : Promise.resolve();
                },
              }),
            ]}
          >
            <Input aria-label="目的地" placeholder="例如：杭州" autoComplete="address-level2" />
          </Form.Item>
        </div>

        <div className="trip-create-schedule-row">
          <Form.Item
            name="dates"
            label="旅行日期"
            rules={[
              { required: true, message: '请选择明确的出发和返程日期' },
              {
                validator(_, value?: [Dayjs, Dayjs]) {
                  if (!value?.[0] || !value?.[1]) return Promise.resolve();
                  const days = value[1].startOf('day').diff(value[0].startOf('day'), 'day') + 1;
                  return days > MAX_TRIP_DAYS
                    ? Promise.reject(new Error(`行程最多支持 ${MAX_TRIP_DAYS} 天`))
                    : Promise.resolve();
                },
              },
            ]}
          >
            <RangePicker
              aria-label="旅行日期"
              allowClear={false}
              format="YYYY年M月D日"
              disabledDate={(current) => current.startOf('day').isBefore(today)}
            />
          </Form.Item>

          <Form.Item
            name="budget"
            label="总预算"
            rules={[{ required: true, type: 'number', min: 1, message: '请输入大于 0 元的总预算' }]}
            className="trip-create-budget"
          >
            <InputNumber
              aria-label="总预算"
              min={1}
              max={1000000}
              precision={0}
              addonBefore="¥"
              addonAfter="元"
            />
          </Form.Item>
        </div>

        <fieldset className="trip-create-travelers">
          <legend>出行人数</legend>
          <div className="trip-create-travelers-row">
            <div className="trip-create-traveler-counts">
              <Form.Item name="adults" label="成人" rules={[{ type: 'number', min: 0, message: '人数不能小于 0' }]}>
                <InputNumber aria-label="成人数量" min={0} max={20} precision={0} />
              </Form.Item>
              <Form.Item name="children" label="儿童" rules={[{ type: 'number', min: 0, message: '人数不能小于 0' }]}>
                <InputNumber aria-label="儿童数量" min={0} max={20} precision={0} />
              </Form.Item>
              <Form.Item name="seniors" label="老人" rules={[{ type: 'number', min: 0, message: '人数不能小于 0' }]}>
                <InputNumber aria-label="老人数量" min={0} max={20} precision={0} />
              </Form.Item>
            </div>

            <Form.Item name="partyType" label="同行关系（可选）" className="trip-create-party-type">
              <Radio.Group
                options={partyTypeOptions}
                optionType="button"
                buttonStyle="solid"
                aria-label="同行关系"
              />
            </Form.Item>
          </div>
        </fieldset>

        <Form.Item name="preferences" label="旅行偏好（可选）" className="trip-create-preferences">
          <Checkbox.Group options={preferenceOptions} />
        </Form.Item>

        {formError && <p className="trip-create-error" role="alert">{formError}</p>}

        <div className="trip-create-submit-row">
          <span>支持 1～7 天明确行程；完整表单将直接进入规划。</span>
          <Button type="primary" htmlType="submit" loading={submitting} disabled={disabled}>
            创建并开始规划
          </Button>
        </div>
      </Form>

      <div className="trip-create-natural-divider" aria-hidden="true" />
    </section>
  );
};

export default TripCreateForm;
