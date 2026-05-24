# -*- coding: utf-8 -*-
"""마크다운 → HTML 변환 + 브라우저로 열기."""
import sys
import webbrowser
from pathlib import Path

try:
    import markdown
except ImportError:
    print("markdown 패키지 설치 중…")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "markdown"])
    import markdown

ROOT = Path(__file__).parents[1]
md_path = ROOT / "reports" / "weekly_20260511_0517.md"
html_path = md_path.with_suffix(".html")

md_text = md_path.read_text(encoding="utf-8-sig")
body = markdown.markdown(md_text, extensions=["tables", "fenced_code"])

html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>{md_path.stem}</title>
<style>
  body {{
    font-family: -apple-system, "Segoe UI", "Malgun Gothic", sans-serif;
    max-width: 980px;
    margin: 40px auto;
    padding: 0 24px;
    line-height: 1.6;
    color: #1f2328;
  }}
  h1 {{ border-bottom: 2px solid #d0d7de; padding-bottom: 8px; }}
  h2 {{ border-bottom: 1px solid #d0d7de; padding-bottom: 4px; margin-top: 32px; }}
  h3 {{ margin-top: 24px; color: #424a53; }}
  table {{
    border-collapse: collapse;
    width: 100%;
    margin: 16px 0;
    font-size: 14px;
  }}
  th, td {{
    border: 1px solid #d0d7de;
    padding: 6px 12px;
    text-align: left;
  }}
  th {{ background: #f6f8fa; font-weight: 600; }}
  td:nth-child(n+3) {{ text-align: right; }}
  blockquote {{
    border-left: 4px solid #d0d7de;
    margin: 0;
    padding: 0 16px;
    color: #656d76;
  }}
  code {{
    background: #f6f8fa;
    padding: 2px 6px;
    border-radius: 4px;
    font-family: "Consolas", monospace;
  }}
  ul {{ padding-left: 24px; }}
  li {{ margin: 4px 0; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""

html_path.write_text(html, encoding="utf-8")
print(f"✓ HTML 저장: {html_path}")

webbrowser.open(html_path.as_uri())
print("✓ 브라우저로 열기 완료")
