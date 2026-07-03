import React from 'react';
import { Alert, Button, Space } from 'antd';

interface ChatErrorBoundaryProps {
    children: React.ReactNode;
}

interface ChatErrorBoundaryState {
    hasError: boolean;
    errorMessage: string;
}

class ChatErrorBoundary extends React.Component<ChatErrorBoundaryProps, ChatErrorBoundaryState> {
    constructor(props: ChatErrorBoundaryProps) {
        super(props);
        this.state = {
            hasError: false,
            errorMessage: ''
        };
    }

    static getDerivedStateFromError(error: Error): ChatErrorBoundaryState {
        return {
            hasError: true,
            errorMessage: error?.message || '未知前端错误'
        };
    }

    componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
        console.error('ChatErrorBoundary 捕获到渲染错误:', error, errorInfo);
    }

    private handleRetry = () => {
        this.setState({ hasError: false, errorMessage: '' });
    };

    render() {
        if (this.state.hasError) {
            return (
                <div style={{ padding: 16 }}>
                    <Alert
                        type="error"
                        showIcon
                        message="页面渲染出现异常，已阻止白屏"
                        description={
                            <Space direction="vertical" size={12} style={{ width: '100%' }}>
                                <div>{this.state.errorMessage}</div>
                                <Space>
                                    <Button type="primary" onClick={this.handleRetry}>重试渲染</Button>
                                    <Button onClick={() => window.location.reload()}>刷新页面</Button>
                                </Space>
                            </Space>
                        }
                    />
                </div>
            );
        }

        return this.props.children;
    }
}

export default ChatErrorBoundary;
