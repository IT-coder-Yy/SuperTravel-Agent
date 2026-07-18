# 🗺️ 百度地图MCP服务器集成指南

本文档介绍如何在Sage FastAPI React演示中集成和使用百度地图MCP服务器。

## 📋 概述

百度地图MCP服务器提供了丰富的地理位置服务API，包括：

- 🌍 **地理编码与逆地理编码** - 地址与坐标互转
- 🔍 **地点检索与周边搜索** - 查找附近的兴趣点
- 🚗 **路线规划与导航** - 多种出行方式的路径规划
- 🏛️ **行政区划查询** - 获取行政区域信息
- 📐 **坐标转换** - 不同坐标系间的转换
- 📏 **距离计算** - 两点间距离测算

## 🚀 快速开始

### 1. 安装依赖

确保您的系统已安装 Node.js 和 npm：

```bash
# 检查Node.js版本
node --version

# 检查npm版本  
npm --version
```

如果未安装，请访问 [https://nodejs.org/](https://nodejs.org/) 下载安装。

### 2. 运行安装脚本

在 `examples/fastapi_react_demo` 目录下运行：

```bash
chmod +x install_baidu_mcp.sh
./install_baidu_mcp.sh
```

### 3. 获取百度地图API密钥

1. 访问 [百度地图开放平台](https://lbsyun.baidu.com/)
2. 注册账号并创建应用
3. 获取API密钥（AK）

### 4. 配置API密钥

在 `backend/config.yaml` 文件中找到百度地图配置：

```yaml
mcp:
  servers:
    baidu-map:
      command: "npx"
      args: ["-y", "@baidumap/mcp-server-baidu-map"]
      env:
        BAIDU_MAP_API_KEY: "YOUR_API_KEY_HERE"  # 替换为您的API密钥
      disabled: false
      description: "百度地图MCP服务器，提供地理编码、逆地理编码、地点检索、路线规划等10个地图相关API接口"
```

将 `YOUR_API_KEY_HERE` 替换为您的实际API密钥。

### 5. 启动服务

启动后端服务：

```bash
python start_backend.py
```

启动前端服务：

```bash
cd frontend
npm install
npm run dev
```

## 🎯 使用方法

### 在Web界面中查看

1. 访问 [http://localhost:8080](http://localhost:8080)
2. 在左侧边栏点击 "MCP服务器" 
3. 查看百度地图服务器的状态和可用工具

### 在对话中使用

您可以向智能体提出各种地理位置相关的问题，例如：

#### 地理编码示例
```
"请帮我查找北京市朝阳区三里屯的坐标"
```

#### 周边搜索示例  
```
"帮我找一下天安门附近的餐厅"
```

#### 路线规划示例
```
"请规划从北京站到北京大学的驾车路线"
```

#### 逆地理编码示例
```
"坐标116.404,39.915是什么地方？"
```

## 🔧 配置说明

### MCP服务器配置结构

```yaml
mcp:
  servers:
    baidu-map:                          # 服务器名称
      command: "npx"                    # 启动命令
      args: ["-y", "@baidumap/mcp-server-baidu-map"]  # 命令参数
      env:                              # 环境变量
        BAIDU_MAP_API_KEY: "your_key"  # 百度地图API密钥
      disabled: false                   # 是否禁用
      description: "服务描述"           # 服务描述
```

### 环境变量配置

您也可以通过环境变量设置API密钥：

```bash
export BAIDU_MAP_API_KEY="your_api_key_here"
export BAIDU_MAP_MIN_INTERVAL_SECONDS="2"
export BAIDU_MAP_COOLDOWN_INTERVAL_SECONDS="5"
export BAIDU_MAP_NETWORK_TIMEOUT_SECONDS="35"
export BAIDU_MAP_QUEUE_TIMEOUT_SECONDS="300"
```

### 全局MCP配置

项目根目录的 `mcp_servers/mcp_setting.json` 也包含了百度地图配置：

```json
{
  "mcpServers": {
    "baidu-map": {
      "command": "npx",
      "args": ["-y", "@baidumap/mcp-server-baidu-map"],
      "env": {
        "BAIDU_MAP_API_KEY": "your_key_here"
      },
      "disabled": false,
      "description": "百度地图MCP服务器，提供地理编码、逆地理编码、地点检索、路线规划等10个地图相关API接口"
    }
  }
}
```

## 🛠️ 可用工具API

百度地图MCP服务器提供以下工具：

| 工具名称 | 功能描述 | 使用场景 |
|---------|---------|---------|
| `map_geocoding` | 地理编码 | 地址转坐标 |
| `map_reverse_geocoding` | 逆地理编码 | 坐标转地址 |
| `map_place_search` | 地点搜索 | 查找兴趣点 |
| `map_nearby_search` | 周边搜索 | 查找附近POI |
| `map_route_planning` | 路线规划 | 路径导航 |
| `map_district_search` | 行政区划 | 区域查询 |
| `map_coordinate_convert` | 坐标转换 | 坐标系转换 |
| `map_distance_calculate` | 距离计算 | 两点距离 |

## 🔍 故障排除

### 常见问题

1. **服务器未连接**
   - 检查API密钥是否正确设置
   - 确认网络连接正常
   - 查看后端日志输出

2. **Node.js相关错误**
   ```bash
   # 清除npm缓存
   npm cache clean --force
   
   # 重新安装包
   npm install -g @baidumap/mcp-server-baidu-map
   ```

3. **权限错误**
   ```bash
   # 使用sudo安装（Linux/Mac）
   sudo npm install -g @baidumap/mcp-server-baidu-map
   ```

### 日志查看

查看详细的MCP服务器日志：

```bash
# 启动后端时查看日志
python start_backend.py
```

### 测试连接

您可以在MCP服务器面板中查看：
- 服务器状态（已连接/未连接）
- 可用工具数量
- 配置信息

## 📚 API参考

### 地理编码

将地址转换为地理坐标：

```json
{
  "tool": "map_geocoding",
  "parameters": {
    "address": "北京市朝阳区三里屯"
  }
}
```

### 周边搜索

搜索指定位置周边的兴趣点：

```json
{
  "tool": "map_nearby_search", 
  "parameters": {
    "location": "116.404,39.915",
    "radius": 1000,
    "query": "餐厅"
  }
}
```

### 路线规划

规划两地间的行驶路线：

```json
{
  "tool": "map_route_planning",
  "parameters": {
    "origin": "北京站",
    "destination": "北京大学", 
    "mode": "driving"
  }
}
```

## 🔗 相关链接

- [百度地图开放平台](https://lbsyun.baidu.com/)
- [百度地图MCP服务器GitHub](https://github.com/baidu-maps/mcp)
- [MCP协议规范](https://modelcontextprotocol.io/)
- [Sage多智能体框架](https://github.com/ZHangZHengEric/Sage)

## 📄 许可证

本集成遵循项目原有的MIT许可证。百度地图API的使用需遵循百度地图开放平台的服务条款。

---

如有问题，请查看项目主README或提交Issue。
