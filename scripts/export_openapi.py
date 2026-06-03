"""
scripts/export_openapi.py
FastAPI 앱의 OpenAPI 스키마를 frontend/openapi.json 으로 덤프.

이 JSON을 openapi-typescript가 읽어 frontend/src/types.gen.ts 를 생성한다.
서버 기동 없이 정적으로 추출하므로 CI에서도 실행 가능.

사용:
  python scripts/export_openapi.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 프로젝트 루트를 import 경로에 추가 (frontend/에서 호출돼도 동작)
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> None:
    from AutoAudit.app.api.server import app

    schema = app.openapi()
    out = _ROOT / "frontend" / "openapi.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OpenAPI schema written → {out} ({len(schema.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
