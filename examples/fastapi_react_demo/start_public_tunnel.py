#!/usr/bin/env python3
"""
为本地 Sage FastAPI + React Demo 创建公网访问隧道（Cloudflare Tunnel）。

用法：
1) 快速临时公网地址（每次启动会变）
   python start_public_tunnel.py --port 8001

2) 稳定固定域名（推荐）
   先在 Cloudflare Zero Trust 创建 Tunnel，获得 token 后：
   set CLOUDFLARE_TUNNEL_TOKEN=xxxxx
   python start_public_tunnel.py --token %CLOUDFLARE_TUNNEL_TOKEN%
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


TRYCLOUDFLARE_REGEX = re.compile(r"https://[-a-z0-9]+\.trycloudflare\.com", re.IGNORECASE)


def check_cloudflared() -> str:
    exe = shutil.which("cloudflared")
    if exe:
        return exe

    print("❌ 未检测到 cloudflared。")
    print("请先安装（Windows）：")
    print("  winget install Cloudflare.cloudflared")
    print("安装后重开终端，再运行本脚本。")
    sys.exit(1)


def warn_frontend_build(port: int) -> None:
    static_index = Path(__file__).parent / "backend" / "static" / "index.html"
    if not static_index.exists() and port == 8001:
        print("⚠️ 未发现 backend/static/index.html。")
        print("   建议先执行：")
        print("   cd frontend")
        print("   npm install")
        print("   npm run build")


def run_quick_tunnel(cloudflared: str, port: int) -> int:
    target = f"http://localhost:{port}"
    print(f"🚀 启动临时公网隧道 -> {target}")
    print("   访问地址会在下方日志中出现（*.trycloudflare.com）。")
    print("   按 Ctrl+C 结束隧道。")

    cmd = [cloudflared, "tunnel", "--url", target, "--no-autoupdate"]
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )

    url_printed = False
    assert process.stdout is not None
    for line in process.stdout:
        line = line.rstrip("\n")
        print(line)

        if not url_printed:
            match = TRYCLOUDFLARE_REGEX.search(line)
            if match:
                print("\n✅ 公网访问地址：")
                print(f"{match.group(0)}\n")
                url_printed = True

    return process.wait()


def run_token_tunnel(cloudflared: str, token: str) -> int:
    print("🚀 启动固定隧道（token 模式）")
    print("   该模式通常对应你在 Cloudflare 配置的固定域名。")
    print("   按 Ctrl+C 结束隧道。")

    cmd = [cloudflared, "tunnel", "run", "--token", token]
    return subprocess.call(cmd)


def main() -> int:
    parser = argparse.ArgumentParser(description="启动 SuperTravelAgent 公网访问隧道")
    parser.add_argument("--port", type=int, default=8001, help="本地后端端口，默认 8001")
    parser.add_argument("--token", type=str, default=os.getenv("CLOUDFLARE_TUNNEL_TOKEN", ""), help="Cloudflare Tunnel token（可选）")
    args = parser.parse_args()

    cloudflared = check_cloudflared()
    warn_frontend_build(args.port)

    if args.token:
        return run_token_tunnel(cloudflared, args.token)

    return run_quick_tunnel(cloudflared, args.port)


if __name__ == "__main__":
    raise SystemExit(main())
