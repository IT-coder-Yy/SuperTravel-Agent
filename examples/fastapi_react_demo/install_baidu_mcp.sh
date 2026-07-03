#!/bin/bash

# 百度地图MCP服务器安装脚本
# 此脚本用于安装和配置百度地图MCP服务器

echo "🗺️ 正在安装百度地图MCP服务器..."

# 检查Node.js和npm是否已安装
if ! command -v node &> /dev/null; then
    echo "❌ 错误: 未找到Node.js，请先安装Node.js"
    echo "📥 下载地址: https://nodejs.org/"
    exit 1
fi

if ! command -v npm &> /dev/null; then
    echo "❌ 错误: 未找到npm，请先安装npm"
    exit 1
fi

echo "✅ Node.js 版本: $(node --version)"
echo "✅ npm 版本: $(npm --version)"

# 全局安装百度地图MCP服务器
echo "📦 正在安装 @baidumap/mcp-server-baidu-map..."

if npm install -g @baidumap/mcp-server-baidu-map; then
    echo "✅ 百度地图MCP服务器安装成功！"
else
    echo "❌ 安装失败，尝试使用npx方式..."
    echo "💡 系统将使用npx -y @baidumap/mcp-server-baidu-map 方式运行"
fi

# 检查配置文件
echo "🔧 检查配置文件..."

CONFIG_FILE="backend/config.yaml"
if [ -f "$CONFIG_FILE" ]; then
    echo "✅ 找到配置文件: $CONFIG_FILE"
    
    # 检查是否已配置百度地图
    if grep -q "baidu-map" "$CONFIG_FILE"; then
        echo "✅ 百度地图MCP服务器已在配置文件中配置"
    else
        echo "⚠️ 百度地图MCP服务器未在配置文件中找到"
        echo "📝 请在 $CONFIG_FILE 中添加百度地图配置"
    fi
else
    echo "⚠️ 未找到配置文件: $CONFIG_FILE"
fi

# 检查全局MCP配置
GLOBAL_MCP_CONFIG="../../mcp_servers/mcp_setting.json"
if [ -f "$GLOBAL_MCP_CONFIG" ]; then
    echo "✅ 找到全局MCP配置: $GLOBAL_MCP_CONFIG"
    
    if grep -q "baidu-map" "$GLOBAL_MCP_CONFIG"; then
        echo "✅ 百度地图MCP服务器已在全局配置中配置"
    else
        echo "⚠️ 百度地图MCP服务器未在全局配置中找到"
    fi
else
    echo "⚠️ 未找到全局MCP配置: $GLOBAL_MCP_CONFIG"
fi

echo ""
echo "🎯 安装完成！接下来的步骤："
echo "1. 获取百度地图API密钥: https://lbsyun.baidu.com/"
echo "2. 在配置文件中设置 BAIDU_MAP_API_KEY 环境变量"
echo "3. 启动FastAPI后端: python start_backend.py"
echo "4. 启动React前端: cd frontend && npm run dev"
echo "5. 访问 http://localhost:8080 并导航到 'MCP服务器' 页面查看状态"
echo ""
echo "📖 百度地图MCP服务器提供的功能："
echo "   • 地理编码与逆地理编码"
echo "   • 地点检索与周边搜索"
echo "   • 路线规划与导航"
echo "   • 行政区划查询"
echo "   • 坐标转换"
echo "   • 距离计算"
echo ""
echo "🔗 更多信息: https://github.com/baidu-maps/mcp" 