import React, { useState } from 'react';
import { Button, Card, Form, Input, InputNumber, Segmented, Space, Switch, message } from 'antd';
import { ApiOutlined, SettingOutlined } from '@ant-design/icons';
import { useSystem } from '../context/SystemContext';
import { apiClient } from '../services/apiClient';
import { useAppSettings } from '../hooks/useAppSettings';

interface SystemConfigProps {
  embedded?: boolean;
}

const buildModelInitialValues = (config: any) => ({
  apiKey: config.apiKey,
  modelName: config.modelName,
  baseUrl: config.baseUrl,
  maxTokens: config.maxTokens,
  temperature: config.temperature,
});

const renderModelFields = () => (
  <>
    <Form.Item
      label="API 密钥"
      name="apiKey"
      extra="留空会沿用后端当前模型连接；输入新密钥时才更新运行配置。"
    >
      <Input.Password placeholder="请输入模型 API 密钥" prefix={<ApiOutlined />} />
    </Form.Item>

    <Form.Item
      label="模型名称"
      name="modelName"
      rules={[{ required: true, message: '请输入模型名称' }]}
    >
      <Input placeholder="例如：deepseek-chat、gpt-4o" />
    </Form.Item>

    <Form.Item
      label="API 基础URL"
      name="baseUrl"
      rules={[{ required: true, message: '请输入 API 基础 URL' }]}
    >
      <Input placeholder="例如: https://api.deepseek.com/v1" />
    </Form.Item>

    <div className="model-config-grid">
      <Form.Item
        label="最大Token数"
        name="maxTokens"
        rules={[{ required: true, message: '请输入最大 Token 数' }]}
      >
        <InputNumber min={1} max={8192} style={{ width: '100%' }} placeholder="4096" />
      </Form.Item>

      <Form.Item
        label="温度参数"
        name="temperature"
        rules={[{ required: true, message: '请输入温度参数' }]}
      >
        <InputNumber min={0} max={2} step={0.1} style={{ width: '100%' }} placeholder="0.7" />
      </Form.Item>
    </div>
  </>
);

const SystemConfig: React.FC<SystemConfigProps> = ({ embedded = false }) => {
  const { state, dispatch } = useSystem();
  const { settings, setSettings } = useAppSettings();
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (values: any) => {
    const hasNewApiKey = Boolean(String(values.apiKey || '').trim());
    setLoading(true);
    try {
      const configData = {
        api_key: values.apiKey,
        model_name: values.modelName,
        base_url: values.baseUrl,
        max_tokens: values.maxTokens,
        temperature: values.temperature
      };

      setSettings({
        planningMode: values.planningMode,
        showMapDefault: Boolean(values.showMapDefault),
        useUserProfile: Boolean(values.useUserProfile),
        allowWebSearch: Boolean(values.allowWebSearch),
      });
      if (hasNewApiKey) {
        await apiClient.configureSystem(configData);
        dispatch({
          type: 'SET_CONFIG',
          payload: {
            apiKey: values.apiKey,
            modelName: values.modelName,
            baseUrl: values.baseUrl,
            maxTokens: values.maxTokens,
            temperature: values.temperature,
          }
        });
      }
      try {
        const status = await apiClient.getSystemStatus();
        dispatch({
          type: 'SET_STATUS',
          payload: {
            status: status.status,
            agentsCount: status.agents_count,
            toolsCount: status.tools_count,
            activeSessions: status.active_sessions,
            version: status.version,
          },
        });
        dispatch({ type: 'SET_CONNECTED', payload: status.status === 'running' });
        message.success(hasNewApiKey ? '设置与模型连接已更新' : '规划偏好已保存，模型连接保持不变');
      } catch (_statusError) {
        message.warning(hasNewApiKey ? '模型配置已更新，但暂时无法刷新系统状态' : '规划偏好已保存，但暂时无法刷新系统状态');
      }
    } catch (error: unknown) {
      const errorMessage = error instanceof Error ? error.message : '未知错误';
      message.error(`${hasNewApiKey ? '规划偏好已保存，但模型配置失败' : '设置保存失败'}: ${errorMessage}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className={embedded ? 'settings-section' : 'travel-page'} style={embedded ? undefined : { maxWidth: 980, margin: '0 auto' }}>
      <Card
        title={
          <Space>
            <SettingOutlined />
            模型配置
          </Space>
        }
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            ...buildModelInitialValues(state.config),
            planningMode: settings.planningMode,
            showMapDefault: settings.showMapDefault,
            useUserProfile: settings.useUserProfile,
            allowWebSearch: settings.allowWebSearch,
          }}
          onFinish={handleSubmit}
        >
          <div className="planning-config-panel">
            <Form.Item label="默认规划模式" name="planningMode">
              <Segmented
                block
                options={[
                  { label: '快速对话', value: 'fast_chat' },
                  { label: '标准规划', value: 'standard_plan' },
                  { label: '深度研究', value: 'deep_research' },
                ]}
              />
            </Form.Item>

            <div className="planning-config-switches">
              <Form.Item label="默认显示地图" name="showMapDefault" valuePropName="checked">
                <Switch />
              </Form.Item>
              <Form.Item label="使用用户画像" name="useUserProfile" valuePropName="checked">
                <Switch />
              </Form.Item>
              <Form.Item label="允许网页与社区检索" name="allowWebSearch" valuePropName="checked">
                <Switch />
              </Form.Item>
            </div>
          </div>

          <section className="model-config-panel">
            <div className="model-config-panel-header">
              <ApiOutlined />
              <div>
                <h3>运行模型</h3>
                <p>规划模式控制推理和多智能体策略，模型连接配置在三种模式间共用。</p>
              </div>
            </div>
            {renderModelFields()}
          </section>

          <Form.Item style={{ marginBottom: 0, marginTop: 18 }}>
            <Button type="primary" htmlType="submit" loading={loading} size="large" block>
              保存并应用
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
};

export default SystemConfig;
