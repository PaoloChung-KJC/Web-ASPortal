"""표 정의가 crm-rma 와 같은지 대조한다 (코드끼리). crm-rma 가 옆 폴더에 없으면 건너뛴다.

crm-rma 에서 A/S 관련 표를 바꾸면 이 테스트가 실패한다 → app/models.py 를 crm-rma 에 맞춘다(반대로 고치지 않는다).
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
CRM = BACKEND.parents[1] / "crm-rma" / "backend"
CRM_PY = CRM / ".venv" / "Scripts" / "python.exe"
DUMP = BACKEND / "tools" / "dump_columns.py"

# crm-rma 와 일부러 다르게 둔 곳 (app/models.py 머리말 참고)
COMPANY_COLUMNS = {"id", "name"}                      # company 는 두 칸만 쓴다
FK_DROPPED = {("as_case", "item_id"), ("as_case", "rma_id")}   # 외래키 선언만 뺐다 — 형식은 같아야 한다


def dump(python, cwd):
    out = subprocess.run([str(python), str(DUMP)], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                         timeout=120)
    if out.returncode != 0:
        raise AssertionError(f"dump_columns 실패 ({cwd}):\n{out.stderr[-2000:]}")
    return json.loads(out.stdout)


@unittest.skipUnless(CRM_PY.exists(), "crm-rma 가 옆에 없으면 건너뛴다")
class CrmSchemaMatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ours = dump(sys.executable, BACKEND)
        cls.crm = dump(CRM_PY, CRM)

    def test_every_portal_table_exists_in_crm(self):
        self.assertEqual(sorted(set(self.ours) - set(self.crm)), [])
        self.assertGreater(len(self.crm), len(self.ours))   # crm 쪽은 표가 훨씬 많다 (제대로 읽었는지)

    def test_columns_match_crm_definitions(self):
        problems = []
        for table, cols in self.ours.items():
            crm_cols = self.crm[table]
            for name, ours in cols.items():
                theirs = crm_cols.get(name)
                if theirs is None:
                    problems.append(f"{table}.{name}: crm-rma 에 없는 칸")
                elif ours != theirs:
                    problems.append(f"{table}.{name}: 여기 {ours} / crm-rma {theirs}")
            if table != "company":
                for name in sorted(set(crm_cols) - set(cols)):
                    problems.append(f"{table}.{name}: crm-rma 에만 있는 칸 (models.py 에 더할 것)")
        self.assertEqual(problems, [])

    def test_company_uses_only_two_columns(self):
        self.assertEqual(set(self.ours["company"]), COMPANY_COLUMNS)


if __name__ == "__main__":
    unittest.main()
