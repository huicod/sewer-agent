"""Shared, script-free presentation for stored inspection report documents."""

from html import escape

DOCUMENT_STYLE = """
:root{color-scheme:light;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',
'Microsoft YaHei',sans-serif;color:#223242;background:#edf1f5;font-size:14px;line-height:1.65}
*{box-sizing:border-box}body{margin:0;padding:24px 16px}a{color:#285c87;text-underline-offset:3px}
a:hover{color:#183d5d}a:focus-visible{outline:2px solid #285c87;outline-offset:3px}
.report-document{max-width:1120px;margin:auto;background:white;border:1px solid #ccd6e0;
padding:36px 44px;box-shadow:0 2px 12px #263d5010}
.report-header{text-align:center;border-bottom:3px solid #315d7a;
padding-bottom:22px;margin-bottom:28px}
.report-kicker{color:#557186;font-size:12px;letter-spacing:.12em;margin:0 0 8px}
h1{font-size:27px;letter-spacing:.05em;margin:0;line-height:1.4;color:#1d3347}
.report-subtitle{margin:10px 0 0;color:#617181;font-size:13px}
h2{font-size:18px;line-height:1.5;margin:30px 0 14px;color:#213e56;
border-bottom:1px solid #cdd7df;padding-bottom:9px}
h3{font-size:15px;color:#233f55;margin:22px 0 10px}h4{font-size:14px;margin:18px 0 8px}
p{margin:10px 0}pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere;margin:0}
code{font-family:Consolas,monospace;font-size:12px;overflow-wrap:anywhere;word-break:break-word}
table{width:100%;border-collapse:collapse;table-layout:fixed;margin:12px 0 18px;font-size:13px}
caption{text-align:left;font-weight:600;color:#36566d;padding-bottom:8px}
th,td{border:1px solid #cdd6df;padding:10px 12px;text-align:left;vertical-align:top;
overflow-wrap:anywhere;word-break:break-word}
thead th{background:#eaf0f5;color:#29485f;font-weight:600}
tbody th{background:#f2f5f8;font-weight:500;width:18%}.metadata-table td{width:32%}
.findings-table th:first-child{width:50px}.findings-table th:nth-child(2){width:18%}
.findings-table th:nth-child(3){width:15%}.findings-table th:nth-child(4){width:24%}
.tool-table th:first-child{width:25%}.tool-table th:nth-child(2){width:18%}
.source-table th:first-child{width:42%}.source-table th:nth-child(2){width:20%}
.report-note{border-left:3px solid #52758e;background:#f1f5f8;padding:11px 14px;margin:14px 0;
font-size:13px}.report-note--pending{border-left-color:#b48539;background:#fff8eb;color:#715322}
.muted{color:#647687;font-size:12px}.status{display:inline-block;padding:1px 7px;border:1px solid
#c8d6e1;background:#f0f5f9;color:#294f6b;white-space:nowrap;border-radius:3px;font-size:12px}
.status--needs_review,.status--tentative,.status--waiting_review{border-color:#dfc79c;
background:#fff7e7;color:#8a601e}.status--confirmed,.status--completed{border-color:#b5ccbd;
background:#eef5f0;color:#3f6750}.status--rejected,.status--failed{border-color:#d6b5b5;
background:#fcf0ef;color:#8d4641}.status--cancelled{color:#66717c;background:#f2f3f5}
.report-source{margin:24px 0;padding-top:4px}
.report-source+.report-source{border-top:2px solid #ccd6df}
.evidence-gallery,.image-comparison{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));
gap:16px;margin:16px 0 24px}figure{margin:16px 0;break-inside:avoid}
.evidence-gallery figure,.image-comparison figure{margin:0;border:1px solid #cdd6df;padding:10px}
figure img{display:block;max-width:100%;width:auto;max-height:340px;object-fit:contain;margin:auto;
background:#edf0f3}.evidence-gallery img,.image-comparison img{height:240px;width:100%}
figcaption{text-align:center;font-size:12px;color:#4d6375;overflow-wrap:anywhere;margin-top:8px}
.evidence-links{margin:0;padding-left:17px}.evidence-links li{margin:0 0 6px}
.finding-id{display:block;font-size:11px;margin-top:5px;color:#687887}
.report-footer{border-top:1px solid #cbd6df;padding-top:18px;margin-top:32px;color:#697988;
font-size:12px}.report-outline{display:flex;flex-wrap:wrap;gap:8px 20px;padding:10px 0;
border-bottom:1px solid #dde4ea;font-size:13px}.report-turn{border-left:3px solid #d4e0e8;
padding:2px 14px;margin:18px 0}.report-turn pre{margin:8px 0}
@media(max-width:680px){body{padding:0}.report-document{border:0;padding:22px 16px;box-shadow:none}
h1{font-size:22px}th,td{padding:7px 6px;font-size:12px}.metadata-table th{width:22%}
.metadata-table td{width:28%}.evidence-gallery,.image-comparison{grid-template-columns:1fr}
.status{max-width:100%;white-space:normal}
.findings-table th:first-child{width:32px}.findings-table th:nth-child(2){width:18%}
.findings-table th:nth-child(3){width:17%}.findings-table th:nth-child(4){width:23%}}
@page{size:A4;margin:16mm 14mm}
@media print{body{padding:0;background:white;color:#111}.report-document{max-width:none;
padding:0;border:0;box-shadow:none}h1{font-size:22pt}h2{break-after:avoid;font-size:14pt}
h3,h4{break-after:avoid}table{font-size:9pt}th,td{padding:6px 8px}thead{display:table-header-group}
tr,figure,.report-note{break-inside:avoid}a{color:inherit;text-decoration:none}
.report-outline{display:none}.evidence-gallery img,.image-comparison img{height:52mm}
*{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
"""


def document_start(title: str, subtitle: str) -> list[str]:
    """Start a complete HTML document; titles and subtitles are plain text."""
    return [
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{escape(title)}</title><style>{DOCUMENT_STYLE}</style></head><body>",
        '<main class="report-document"><header class="report-header">',
        '<p class="report-kicker">管道巡检 · 影像检查记录</p>',
        f"<h1>{escape(title)}</h1><p class=\"report-subtitle\">{escape(subtitle)}</p>",
        "</header>",
    ]


def document_end() -> list[str]:
    return [
        '<footer class="report-footer">本报告引用已保存的证据与状态，'
        "不执行新的检测或自动改写结论。图片与原始文件通过本部署的资产链接查看。"
        "</footer></main></body></html>",
    ]


def metadata_table(rows: list[tuple[str, str, str, str]]) -> str:
    """Render plain-text label/value pairs, never caller-provided HTML."""
    body = "".join(
        "<tr>"
        f'<th scope="row">{escape(left_label)}</th><td>{escape(left_value)}</td>'
        f'<th scope="row">{escape(right_label)}</th><td>{escape(right_value)}</td>'
        "</tr>"
        for left_label, left_value, right_label, right_value in rows
    )
    return '<table class="metadata-table"><tbody>' + body + "</tbody></table>"
