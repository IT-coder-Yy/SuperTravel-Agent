import type { ThemeConfig } from 'antd';

export const travelAccessibilityColors = {
  focus: '#353b9d',
  success: '#237a52',
  warning: '#8a4b00',
  error: '#b42318',
} as const;

export const travelTheme: ThemeConfig = {
  token: {
    colorPrimary: '#5b63d3',
    colorSuccess: travelAccessibilityColors.success,
    colorWarning: travelAccessibilityColors.warning,
    colorError: travelAccessibilityColors.error,
    colorInfo: '#5b63d3',
    colorBgLayout: '#f3f5ff',
    colorBgContainer: '#fcfdff',
    colorBgElevated: '#ffffff',
    colorText: '#20293d',
    colorTextSecondary: '#59657a',
    colorBorder: '#cfd7e8',
    colorBorderSecondary: '#e4e8f2',
    borderRadius: 14,
    borderRadiusLG: 18,
    controlHeight: 42,
    fontFamily: '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI Variable", "Segoe UI", "PingFang SC", "Microsoft YaHei UI", sans-serif',
    fontSize: 16,
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
      borderRadiusLG: 18,
      boxShadowTertiary: '0 10px 26px rgba(57, 64, 119, 0.09)',
    },
    DatePicker: {
      activeBorderColor: '#5b63d3',
      hoverBorderColor: '#747bdf',
      activeShadow: '0 0 0 3px rgba(91, 99, 211, 0.16)',
    },
    Input: {
      borderRadius: 14,
      activeBorderColor: '#5b63d3',
      hoverBorderColor: '#747bdf',
      activeShadow: '0 0 0 3px rgba(91, 99, 211, 0.16)',
    },
    InputNumber: {
      borderRadius: 14,
      activeBorderColor: '#5b63d3',
      hoverBorderColor: '#747bdf',
      activeShadow: '0 0 0 3px rgba(91, 99, 211, 0.16)',
    },
    Menu: {
      itemBorderRadius: 12,
      itemSelectedBg: '#e4e7fb',
      itemSelectedColor: '#353b9d',
      itemHoverBg: '#eef0fd',
    },
    Tabs: {
      inkBarColor: '#6269d8',
      itemSelectedColor: '#353b9d',
      itemHoverColor: '#6269d8',
    },
  },
};
