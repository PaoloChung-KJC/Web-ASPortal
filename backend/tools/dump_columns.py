"""표 정의를 JSON 으로 내놓는다 — tests/test_crm_schema_match.py 가 이 프로그램과 crm-rma 양쪽에서 돌린다.

현재 폴더(cwd)의 app 패키지를 읽는다. 그래서 crm-rma/backend 에서 돌리면 crm-rma 의 표 정의가 나온다.
출력: {표: {칸: {"type": "VARCHAR(60)", "nullable": true, "pk": false}}}
"""
import importlib
import json
import os
import pkgutil
import sys

sys.path.insert(0, os.getcwd())

from app.database import Base  # noqa: E402

try:   # crm-rma: 표 정의가 app/models/ 패키지에 나뉘어 있다
    import app.models as models  # noqa: E402
    if hasattr(models, "__path__"):
        for m in pkgutil.iter_modules(models.__path__):
            importlib.import_module(f"app.models.{m.name}")
except ImportError as e:  # pragma: no cover
    sys.exit(f"app.models 를 읽지 못했습니다: {e}")

out = {}
for table in Base.metadata.sorted_tables:
    out[table.name] = {
        c.name: {"type": str(c.type), "nullable": bool(c.nullable), "pk": bool(c.primary_key)}
        for c in table.columns
    }
sys.stdout.reconfigure(encoding="utf-8")
print(json.dumps(out, ensure_ascii=False, sort_keys=True))
