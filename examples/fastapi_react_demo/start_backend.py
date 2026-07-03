#!/usr/bin/env python3
"""
Sage FastAPI + React Demo 后端启动脚本

便捷启动脚本，自动检查依赖并启动后端服务器
"""

import sys
import subprocess
import os
import socket
import ipaddress
import shutil
from pathlib import Path


os.environ.setdefault("PYTHONIOENCODING", "utf-8")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def load_env_file(env_path: Path):
    """Load KEY=VALUE pairs from .env file into process environment."""
    if not env_path.exists():
        return

    override_dotenv = os.getenv("SAGE_DOTENV_OVERRIDE", "1").strip().lower() in {"1", "true", "yes", "on"}
    loaded_keys = 0
    overridden_keys = 0
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        should_override = override_dotenv and key.startswith("SAGE_")
        if key and (key not in os.environ or should_override):
            if key in os.environ and os.environ.get(key) != value:
                overridden_keys += 1
            os.environ[key] = value
            loaded_keys += 1

    if loaded_keys > 0:
        print(f"🔐 已从 .env 加载 {loaded_keys} 个环境变量")
    if overridden_keys > 0:
        print(f"🔄 已按 .env 覆盖 {overridden_keys} 个 SAGE_* 环境变量 (可用 SAGE_DOTENV_OVERRIDE=0 关闭)")

def check_dependencies():
    """检查必需的依赖"""
    required_packages = [
        'fastapi',
        'uvicorn',
        'websockets',
        'pydantic'
    ]
    
    missing_packages = []
    
    for package in required_packages:
        try:
            __import__(package)
        except ImportError:
            missing_packages.append(package)
    
    if missing_packages:
        print(f"❌ 缺少以下依赖包: {', '.join(missing_packages)}")
        print("请运行以下命令安装:")
        print(f"pip install {' '.join(missing_packages)}")
        return False
    
    return True


def is_env_flag_enabled(name: str, default: bool = False) -> bool:
    """Parse boolean-like environment flags."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_latest_mtime(path: Path) -> float:
    """Return latest modification time under a file/dir tree."""
    if not path.exists():
        return 0.0

    if path.is_file():
        return path.stat().st_mtime

    latest = 0.0
    for entry in path.rglob("*"):
        if entry.is_file():
            try:
                latest = max(latest, entry.stat().st_mtime)
            except OSError:
                continue
    return latest


def ensure_fresh_frontend_static(current_dir: Path, backend_dir: Path) -> None:
    """Ensure backend/static reflects latest frontend source."""
    auto_build = is_env_flag_enabled("SAGE_AUTO_BUILD_FRONTEND", default=True)
    force_build = is_env_flag_enabled("SAGE_FORCE_FRONTEND_BUILD", default=False)

    if not auto_build and not force_build:
        print("⏭️ 已跳过前端自动构建检查 (SAGE_AUTO_BUILD_FRONTEND=0)")
        return

    frontend_dir = current_dir / "frontend"
    if not frontend_dir.exists():
        print("⚠️ 未找到 frontend 目录，跳过前端构建检查")
        return

    package_json = frontend_dir / "package.json"
    if not package_json.exists():
        print("⚠️ 未找到 frontend/package.json，跳过前端构建检查")
        return

    static_dir = backend_dir / "static"
    static_index = static_dir / "index.html"
    static_assets_dir = static_dir / "assets"

    frontend_source_mtime = max(
        get_latest_mtime(frontend_dir / "src"),
        get_latest_mtime(frontend_dir / "index.html"),
        get_latest_mtime(frontend_dir / "package.json"),
        get_latest_mtime(frontend_dir / "vite.config.ts"),
        get_latest_mtime(frontend_dir / "tsconfig.json"),
        get_latest_mtime(frontend_dir / "tsconfig.node.json"),
    )
    frontend_build_mtime = max(
        get_latest_mtime(static_index),
        get_latest_mtime(static_assets_dir),
    )

    needs_build = force_build
    if force_build:
        print("🔁 检测到 SAGE_FORCE_FRONTEND_BUILD=1，将强制构建前端")
    elif frontend_build_mtime <= 0:
        needs_build = True
        print("🧱 未检测到有效前端静态构建产物，将执行构建")
    elif frontend_source_mtime > frontend_build_mtime:
        needs_build = True
        print("🆕 检测到前端源码更新，将自动构建最新静态资源")

    if not needs_build:
        print("✅ 前端静态资源已是最新，跳过构建")
        return

    npm_executable = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm_executable:
        print("❌ 需要构建前端但未找到 npm，请先安装 Node.js 并确保 npm 在 PATH 中")
        sys.exit(1)

    node_modules_dir = frontend_dir / "node_modules"
    try:
        if not node_modules_dir.exists():
            print("📦 未检测到 node_modules，正在安装前端依赖...")
            subprocess.run([npm_executable, "install"], cwd=frontend_dir, check=True)

        print("🏗️ 正在构建前端静态资源（npm run build）...")
        subprocess.run([npm_executable, "run", "build"], cwd=frontend_dir, check=True)
        print("✅ 前端静态资源构建完成")
    except subprocess.CalledProcessError as e:
        print(f"❌ 前端构建失败，已中止后端启动: {e}")
        sys.exit(1)

def main():
    """主函数"""
    print("🚀 启动 Sage FastAPI + React Demo 后端服务器")
    print("=" * 50)
    
    # 检查当前目录
    current_dir = Path(__file__).parent
    load_env_file(current_dir / ".env")

    backend_dir = current_dir / "backend"
    main_py = backend_dir / "main.py"
    
    if not main_py.exists():
        print(f"❌ 找不到后端文件: {main_py}")
        print("请确保在正确的目录下运行此脚本")
        sys.exit(1)
    
    # 检查依赖
    print("🔍 检查依赖...")
    if not check_dependencies():
        sys.exit(1)
    
    print("✅ 依赖检查通过")

    # 启动前确保后端静态资源不是旧版本
    ensure_fresh_frontend_static(current_dir=current_dir, backend_dir=backend_dir)
    
    # 启动服务器
    print("🌟 启动FastAPI服务器...")
    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
    except Exception:
        local_ip = "127.0.0.1"

    try:
        ip_obj = ipaddress.ip_address(local_ip)
        if (not ip_obj.is_private) or str(ip_obj).startswith("198."):
            local_ip = "127.0.0.1"
    except Exception:
        local_ip = "127.0.0.1"

    print("📡 本机访问: http://127.0.0.1:8001")
    print(f"📚 API文档: http://127.0.0.1:8001/docs")
    print(f"🔧 交互式API: http://127.0.0.1:8001/redoc")
    print(f"🌐 局域网访问(如可用): http://{local_ip}:8001")
    print("-" * 50)
    print("按 Ctrl+C 停止服务器")
    print("=" * 50)
    
    try:
        # 切换到backend目录并启动
        os.chdir(backend_dir)
        subprocess.run([
            sys.executable, "main.py"
        ], check=True)
    except KeyboardInterrupt:
        print("\n👋 服务器已停止")
    except subprocess.CalledProcessError as e:
        print(f"❌ 启动失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 
