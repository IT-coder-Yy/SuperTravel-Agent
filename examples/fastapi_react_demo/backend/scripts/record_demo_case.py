"""从刚完成的真实 SSE 运行录制一个脱敏案例包。

示例：
python -m backend.scripts.record_demo_case --device-id <匿名设备ID> --run-id <运行ID> \
  --case-id hangzhou-humanities-3d --title "杭州三日人文慢游" \
  --summary "湖山、人文与本地餐饮结合的轻松路线。" --traveler-label "双人" --accept
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from backend.services.demo_case_package_service import DemoCasePackageError, DemoCasePackageService
from backend.services.trip_repository import TripRepository, default_trip_database_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="录制已验收的真实旅行案例包")
    parser.add_argument("--device-id", required=True, help="浏览器匿名设备 ID")
    parser.add_argument("--run-id", action="append", required=True, help="刚完成的真实规划运行 ID；澄清链路可重复传入")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--traveler-label", required=True)
    parser.add_argument("--accepted-on", default=None, help="人工验收日期，默认今天")
    parser.add_argument("--database", type=Path, default=default_trip_database_path())
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "data" / "trip_demos",
    )
    parser.add_argument("--accept", action="store_true", help="确认该运行已在页面人工验收且可公开脱敏录制")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.accept:
        print("拒绝写入：请先在真实页面验收，再显式传入 --accept。", file=sys.stderr)
        return 2
    if not args.database.exists():
        print(f"找不到数据库：{args.database}", file=sys.stderr)
        return 2
    repository = TripRepository(args.database)
    service = DemoCasePackageService()
    try:
        package = service.build_from_repository(
            repository=repository,
            device_id=args.device_id,
            run_ids=args.run_id,
            case_id=args.case_id,
            title=args.title,
            summary=args.summary,
            traveler_label=args.traveler_label,
            accepted_on=args.accepted_on,
        )
        target = service.write_package(package, args.output_dir)
    except DemoCasePackageError as exc:
        print(f"录制失败：{exc}", file=sys.stderr)
        return 2
    print(f"已写入脱敏案例包：{target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
