"""Browser-test bridge: real exploration execution with strictly mocked model replies."""
import json
from pathlib import Path
import re
import sys
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.security import create_session_token
from app.services.cleanup_service import delete_entire_session
from app.services.exploration_service import ExplorationError, run_exploration
from app.services.session_manager import create_session


CHART_SCRIPT = """
df = dataframes['sales.csv']
totals = df.groupby('region')['revenue'].sum(min_count=1).sort_values()
fig, ax = plt.subplots()
totals.plot.barh(ax=ax)
ax.set_title('Revenue by region')
ax.set_xlabel('Revenue')
ax.set_ylabel('Region')
fig.tight_layout()
show_chart(fig, 'Revenue by region', 'Sum of revenue by region, including all uploaded rows.')
print(f'Total revenue: {totals.sum():.0f}')
"""


def main():
    action = sys.argv[1]
    if action == "create":
        job_id = f"browser-explorer-{uuid.uuid4().hex}"
        path = Path(f"workspace/uploads/{job_id}.csv")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("region,revenue\nNorth,10\nSouth,20\nNorth,15\n")
        create_session(job_id, {
            "job_id": job_id, "initial_prompt": "Explore sales", "current_goal": "Explore sales",
            "status": "Concluído", "current_step": "completed", "progress": 100,
            "settings": {"title": "Sales exploration"},
            "files": [{"original_filename": "sales.csv", "path": str(path), "artifact": {
                "filename": "sales.csv", "type": "csv", "shape": {"rows": 3, "columns": 2},
                "columns": [{"name": "region", "type": "object"}, {"name": "revenue", "type": "int64"}],
            }}],
        })
        print(json.dumps({"job_id": job_id, "token": create_session_token()}))
        return
    job_id = sys.argv[2]
    if not re.fullmatch(r"browser-explorer-[0-9a-f]{32}", job_id):
        raise ValueError("Browser fixtures may only operate on their own test sessions")
    if action == "delete":
        delete_entire_session(job_id)
        print('{}')
        return
    if action not in {"ask", "fail"}:
        raise ValueError("Unknown fixture action")
    code = CHART_SCRIPT if action == "ask" else "raise ValueError('Deliberate browser regression failure')"
    with patch("app.services.llm.client.call_llm", return_value=code):
        try:
            response = {"status": 200, "data": run_exploration(job_id, sys.argv[3])}
        except ExplorationError as exc:
            response = {"status": exc.status_code, "data": {"detail": exc.details}}
    print(json.dumps(response))


if __name__ == "__main__":
    main()
