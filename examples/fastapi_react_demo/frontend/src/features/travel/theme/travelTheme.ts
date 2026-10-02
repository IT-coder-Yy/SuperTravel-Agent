import type { ThemeConfig } from 'antd';

export const travelAccessibilityColors = {
  focus: '#254b96',
  success: '#237a52',
  warning: '#8a4b00',
  error: '#b42318',
} as const;

export const travelTheme: ThemeConfig = {
  token: {
    colorPrimary: '#3567cb',
    colorSuccess: travelAccessibilityColors.success,
    colorWarning: travelAccessibilityColors.warning,
    colorError: travelAccessibilityColors.error,
    colorInfo: '#3567cb',
    colorBgLayout: '#f5f7fb',
    colorBgContainer: '#ffffff',
    colorBgElevated: '#ffffff',
    colorText: '#233247',
    colorTextSecondary: '#59657a',
    colorBorder: '#cfd7e8',
    colorBorderSecondary: '#e4e8f2',
    borderRadius: 14,
    borderRadiusLG: 16,
    controlHeight: 42,
    fontFamily: '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI Variable", "Segoe UI", "PingFang SC", "Microsoft YaHei UI", sans-serif',
    fontSize: 14,
    boxShadow: '0 10px 26px rgba(57, 64, 119, 0.09)',
    motionEaseOut: 'cubic-bezier(0.16, 1, 0.3, 1)',
  },
  components: {
    Button: {
      controlHeight: 42,
      borderRadius: 14,
      primaryShadow: 'none',
      fontWeight: 600,
    },
    Card: {
      borderRadiusLG: 16,
      boxShadowTertiary: '0 10px 26px rgba(57, 64, 119, 0.09)',
    },
    DatePicker: {
      activeBorderColor: '#3567cb',
      hoverBorderColor: '#527ccc',
      activeShadow: '0 0 0 3px rgba(91, 99, 211, 0.16)',
    },
    Input: {
      borderRadius: 14,
      activeBorderColor: '#3567cb',
      hoverBorderColor: '#527ccc',
      activeShadow: '0 0 0 3px rgba(91, 99, 211, 0.16)',
    },
    InputNumber: {
      borderRadius: 14,
      activeBorderColor: '#3567cb',
      hoverBorderColor: '#527ccc',
      activeShadow: '0 0 0 3px rgba(91, 99, 211, 0.16)',
    },
    Menu: {
      itemBorderRadius: 12,
      itemSelectedBg: '#eaf0fb',
      itemSelectedColor: '#254b96',
      itemHoverBg: '#f2f5fa',
    },
    Tabs: {
      inkBarColor: '#3567cb',
      itemSelectedColor: '#254b96',
      itemHoverColor: '#3567cb',
    },
  },
};
