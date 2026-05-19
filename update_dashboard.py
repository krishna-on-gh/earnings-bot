"""Auto-called by Task Scheduler to keep dashboard.html current."""
import re, json
from pathlib import Path
from datetime import datetime

base = Path(__file__).parent
html = (base / "dashboard.html").read_text(encoding="utf-8")

for tag, path in [
    ("__RECOMMENDATIONS__", "data/recommendations.json"),
    ("__TRADES__",          "data/trades_history.json"),
    ("__POSITIONS__",       "data/positions.json"),
    ("__BUDGET__",          "data/budget_tracker.json"),
    ("__ACCURACY__",        "data/earnings_accuracy.json"),
    ("__PORTFOLIO_HIST__",  "data/portfolio_history.json"),
]:
    content = (base / path).read_text(encoding="utf-8").strip()
    html = re.sub(rf'(<script id="{tag}"[^>]*>).*?(</script>)',
                  lambda m, c=content: m.group(1) + c + m.group(2),
                  html, flags=re.DOTALL)

meta = json.dumps({"embedded_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), "source": "scheduler"})
html = re.sub(r'(<script id="__META__"[^>]*>).*?(</script>)',
              lambda m, c=meta: m.group(1) + c + m.group(2),
              html, flags=re.DOTALL)
(base / "dashboard.html").write_text(html, encoding="utf-8")
