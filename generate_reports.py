#!/usr/bin/env python3
"""
Генератор индивидуальных и сводных отчётов по пакетному QC — generate_reports.py

Читает JSON + PNG из qc_results/, генерирует:
  - reports/index.html       — оглавление (таблица, сортировка JS)
  - reports/RNS001.html ...  — детальный отчёт на каждого субъекта
  - reports/images/           — копии PNG-срезов
  - reports/summary.csv       — CSV-сводка

Использование:
  python generate_reports.py
  python generate_reports.py --input_dir ./qc_results --output_dir ./reports
"""

import argparse
import csv
import json
import os
import shutil
import sys
from pathlib import Path

import plotly.graph_objects as go


# ============================================================
# Load
# ============================================================

def load_all_results(input_dir: str) -> list:
    """Загружает все JSON-файлы субъектов (игнорирует summary.json)."""
    subjects = []
    for fname in sorted(os.listdir(input_dir)):
        if not fname.endswith(".json") or fname == "summary.json":
            continue
        fpath = os.path.join(input_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            subjects.append(data)
        except Exception as e:
            print("WARN: cannot load %s: %s" % (fname, e))
    return subjects


# ============================================================
# Plotly helpers
# ============================================================

def build_histogram(regions: list) -> str:
    """Строит интерактивную гистограмму Z-score. Возвращает HTML-фрагмент."""
    zscores = [r["Zscore"] for r in regions if r.get("Zscore") is not None]
    if not zscores:
        return "<p>No Z-score data available.</p>"

    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=zscores,
        nbinsx=20,
        marker_color="#3498db",
        marker_line_color="white",
        marker_line_width=1,
        name="Z-score",
    ))
    # Threshold lines
    for thresh, color, label in [(2, "#f39c12", "|Z|=2"), (3, "#e74c3c", "|Z|=3")]:
        for sign in [-1, 1]:
            fig.add_vline(
                x=sign * thresh,
                line_dash="dash",
                line_color=color,
                line_width=1.5,
                annotation_text=label if sign > 0 else None,
                annotation_position="top right" if sign > 0 else "top left",
            )

    fig.update_layout(
        title="Z-score Distribution",
        xaxis_title="Z-score",
        yaxis_title="Number of Regions",
        template="plotly_white",
        height=350,
        margin=dict(l=40, r=20, t=50, b=40),
        showlegend=False,
    )
    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def build_verdict_bar(verdict_counts: dict) -> str:
    """Строит столбчатую диаграмму вердиктов. Возвращает HTML-фрагмент."""
    order = ["OK", "Check", "Bad", "Unknown"]
    colors = {"OK": "#27ae60", "Check": "#f39c12", "Bad": "#e74c3c", "Unknown": "#95a5a6"}
    values = [verdict_counts.get(k, 0) for k in order]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=order,
        y=values,
        marker_color=[colors[k] for k in order],
        text=values,
        textposition="outside",
        textfont=dict(size=14),
    ))
    fig.update_layout(
        title="Verdict Distribution",
        xaxis_title="Verdict",
        yaxis_title="Count",
        template="plotly_white",
        height=350,
        margin=dict(l=40, r=20, t=50, b=40),
        showlegend=False,
    )
    return fig.to_html(full_html=False, include_plotlyjs="cdn")


# ============================================================
# Templates (f-strings, no jinja2 dependency)
# ============================================================

VERDICT_COLORS = {
    "OK": "#d5f5e3",       # light green
    "Check": "#fdebd0",    # light orange
    "Bad": "#fadbd8",      # light red
    "Unknown": "#ebedef",  # light gray
}

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #f5f6fa; color: #2c3e50; padding: 20px; }
.container { max-width: 1200px; margin: 0 auto; }
h1 { font-size: 24px; margin-bottom: 4px; }
h2 { font-size: 18px; margin: 24px 0 12px; }
.subtitle { color: #7f8c8d; font-size: 14px; margin-bottom: 20px; }
.card { background: white; border-radius: 10px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin-bottom: 20px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { background: #2c3e50; color: white; padding: 8px 10px; text-align: left; font-weight: 600; cursor: pointer; user-select: none; }
th:hover { background: #34495e; }
td { padding: 6px 10px; border-bottom: 1px solid #ecf0f1; }
tr.OK { background: #d5f5e3; }
tr.Check { background: #fdebd0; }
tr.Bad { background: #fadbd8; }
tr.Unknown { background: #ebedef; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; color: white; }
.badge.OK { background: #27ae60; }
.badge.Check { background: #f39c12; }
.badge.Bad { background: #e74c3c; }
.badge.Unknown { background: #95a5a6; }
.slice-container { text-align: center; margin: 20px 0; }
.slice-container img { max-width: 100%; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }
.charts { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 800px) { .charts { grid-template-columns: 1fr; } }
.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 12px; margin-bottom: 20px; }
.stat-card { background: white; border-radius: 8px; padding: 14px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); text-align: center; }
.stat-card .value { font-size: 26px; font-weight: 700; }
.stat-card .label { font-size: 11px; color: #7f8c8d; margin-top: 4px; text-transform: uppercase; letter-spacing: 0.5px; }
.stat-card.OK .value { color: #27ae60; }
.stat-card.Check .value { color: #f39c12; }
.stat-card.Bad .value { color: #e74c3c; }
.stat-card.Unknown .value { color: #95a5a6; }
.stat-card.SNR .value { color: #3498db; }
.nav { margin-bottom: 16px; }
.nav a { color: #3498db; text-decoration: none; font-size: 14px; }
.nav a:hover { text-decoration: underline; }
.footer { text-align: center; color: #bdc3c7; font-size: 12px; margin-top: 32px; padding-top: 16px; border-top: 1px solid #ecf0f1; }
"""

INDEX_JS = """
<script>
function sortTable(n) {
    var table = document.getElementById("summaryTable");
    var rows = Array.from(table.rows).slice(1);
    var asc = table.getAttribute("data-sort-dir") !== "asc";
    table.setAttribute("data-sort-dir", asc ? "asc" : "desc");
    rows.sort(function(a, b) {
        var x = a.cells[n].textContent.trim();
        var y = b.cells[n].textContent.trim();
        var xn = parseFloat(x), yn = parseFloat(y);
        if (!isNaN(xn) && !isNaN(yn)) return asc ? xn - yn : yn - xn;
        return asc ? x.localeCompare(y) : y.localeCompare(x);
    });
    rows.forEach(function(r) { table.tBodies[0].appendChild(r); });
}
</script>
"""


def build_subject_html(subject: dict) -> str:
    """Строит полный HTML-документ для одного субъекта."""
    sid = subject.get("subject_id", "Unknown")
    snr = subject.get("snr", 0)
    vc = subject.get("verdict_counts", {})
    regions = subject.get("regions", [])
    slice_img = subject.get("slice_image", "")
    warnings = subject.get("warnings", [])
    error = subject.get("error", "")

    total = len(regions)
    bad = vc.get("Bad", 0)
    check = vc.get("Check", 0)
    ok_cnt = vc.get("OK", 0)
    unk = vc.get("Unknown", 0)

    # Regions table rows
    region_rows = []
    for r in regions:
        v = r.get("Verdict", "Unknown")
        z = r.get("Zscore")
        z_str = ("%.2f" % z) if z is not None else "—"
        val = r.get("Value")
        val_str = ("%.2f" % val) if val is not None else "—"
        region_rows.append(
            '<tr class="{v}">'
            '<td>{Region}</td>'
            '<td>{Type}</td>'
            '<td class="num">{Value}</td>'
            '<td class="num">{Zscore}</td>'
            '<td><span class="badge {v}">{v}</span></td>'
            '</tr>'.format(
                v=v, Region=r.get("Region","?"), Type=r.get("Type","?"),
                Value=val_str, Zscore=z_str
            )
        )

    # Slice image
    slice_html = ""
    if slice_img:
        slice_html = '<div class="slice-container"><img src="images/%s" alt="%s slice" /></div>' % (
            slice_img, sid
        )

    # Warnings / errors
    warning_html = ""
    if error:
        warning_html = '<div class="card" style="border-left: 4px solid #e74c3c;"><strong>Error:</strong> %s</div>' % error
    elif warnings:
        warning_html = '<div class="card" style="border-left: 4px solid #f39c12;"><strong>Warnings:</strong><ul>%s</ul></div>' % "".join(
            "<li>%s</li>" % w for w in warnings
        )

    # Charts
    hist_html = build_histogram(regions)
    bar_html = build_verdict_bar(vc)

    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QC Report — {sid}</title>
<style>{css}</style>
</head>
<body>
<div class="container">
    <div class="nav"><a href="index.html">&larr; Back to Index</a></div>
    <h1>QC Report: {sid}</h1>
    <p class="subtitle">Signal-to-Noise Ratio (SNR): {snr:.1f} &nbsp;|&nbsp; Total Regions: {total} &nbsp;|&nbsp; Brain Charts norms (Nature 2022)</p>

    {warning_html}

    <div class="stats-grid">
        <div class="stat-card OK"><div class="value">{ok}</div><div class="label">OK</div></div>
        <div class="stat-card Check"><div class="value">{check}</div><div class="label">Check</div></div>
        <div class="stat-card Bad"><div class="value">{bad}</div><div class="label">Bad</div></div>
        <div class="stat-card Unknown"><div class="value">{unk}</div><div class="label">Unknown</div></div>
        <div class="stat-card SNR"><div class="value">{snr:.1f}</div><div class="label">SNR</div></div>
    </div>

    {slice_html}

    <h2>Charts</h2>
    <div class="charts">
        <div class="card">{hist_html}</div>
        <div class="card">{bar_html}</div>
    </div>

    <h2>All Regions ({total})</h2>
    <div class="card">
    <table>
        <thead><tr>
            <th>Region</th><th>Type</th><th>Value</th><th>Z-score</th><th>Verdict</th>
        </tr></thead>
        <tbody>{region_rows}</tbody>
    </table>
    </div>

    <div class="footer">QC FreeSurfer Prototype &copy; 2026 &mdash; Generated by generate_reports.py</div>
</div>
</body>
</html>""".format(
        sid=sid, snr=snr, total=total,
        ok=ok_cnt, check=check, bad=bad, unk=unk,
        warning_html=warning_html,
        slice_html=slice_html,
        hist_html=hist_html,
        bar_html=bar_html,
        region_rows="\n".join(region_rows),
        css=CSS,
    )


def build_index_html(subjects: list) -> str:
    """Строит оглавление index.html с сортируемой таблицей."""
    rows = []
    for s in subjects:
        sid = s.get("subject_id", "?")
        vc = s.get("verdict_counts", {})
        snr = s.get("snr", 0)
        error = s.get("error", "")

        if error:
            rows.append(
                '<tr style="background:#fadbd8">'
                '<td>%s</td>'
                '<td class="num">—</td>'
                '<td class="num">—</td><td class="num">—</td>'
                '<td class="num">—</td><td class="num">—</td>'
                '<td><span style="color:#e74c3c">ERROR</span></td>'
                '</tr>' % sid
            )
        else:
            rows.append(
                '<tr>'
                '<td><a href="%s.html">%s</a></td>'
                '<td class="num">%.1f</td>'
                '<td class="num">%d</td><td class="num">%d</td>'
                '<td class="num">%d</td><td class="num">%d</td>'
                '<td class="num">%.1f%%</td>'
                '</tr>' % (
                    sid, sid, snr,
                    vc.get("OK", 0), vc.get("Check", 0),
                    vc.get("Bad", 0), vc.get("Unknown", 0),
                    (vc.get("Check",0) + vc.get("Bad",0)) / max(
                        vc.get("OK",0)+vc.get("Check",0)+vc.get("Bad",0)+vc.get("Unknown",0), 1
                    ) * 100
                )
            )

    total = len(subjects)

    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QC FreeSurfer — Batch Summary</title>
<style>{css}</style>
</head>
<body>
<div class="container">
    <h1>QC FreeSurfer — Batch Summary</h1>
    <p class="subtitle">Total subjects: {total} &nbsp;|&nbsp; Norms: Brain Charts (Nature 2022)</p>

    <div class="card">
    <table id="summaryTable" data-sort-dir="asc">
        <thead><tr>
            <th onclick="sortTable(0)">Subject ID &#9650;&#9660;</th>
            <th onclick="sortTable(1)">SNR &#9650;&#9660;</th>
            <th onclick="sortTable(2)">OK &#9650;&#9660;</th>
            <th onclick="sortTable(3)">Check &#9650;&#9660;</th>
            <th onclick="sortTable(4)">Bad &#9650;&#9660;</th>
            <th onclick="sortTable(5)">Unknown &#9650;&#9660;</th>
            <th onclick="sortTable(6)">Problem % &#9650;&#9660;</th>
        </tr></thead>
        <tbody>{rows}</tbody>
    </table>
    </div>

    <div class="footer">QC FreeSurfer Prototype &copy; 2026</div>
</div>
{js}
</body>
</html>""".format(
        total=total,
        rows="\n".join(rows),
        css=CSS,
        js=INDEX_JS,
    )


# ============================================================
# CSV export
# ============================================================

def write_summary_csv(subjects: list, output_dir: str):
    """Записывает summary.csv со сводкой по всем субъектам."""
    csv_path = os.path.join(output_dir, "summary.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["subject_id", "snr", "ok", "check", "bad", "unknown"])
        for s in subjects:
            vc = s.get("verdict_counts", {})
            writer.writerow([
                s.get("subject_id", "?"),
                s.get("snr", 0),
                vc.get("OK", 0),
                vc.get("Check", 0),
                vc.get("Bad", 0),
                vc.get("Unknown", 0),
            ])
    print("  summary.csv: %d rows" % len(subjects))


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Генератор отчётов по пакетному QC (HTML + CSV)"
    )
    parser.add_argument(
        "--input_dir",
        default="qc_results",
        help="Папка с JSON-файлами и PNG-срезами (по умолчанию ./qc_results)",
    )
    parser.add_argument(
        "--output_dir",
        default="reports",
        help="Папка для генерации отчётов (по умолчанию ./reports)",
    )
    args = parser.parse_args()

    input_dir = os.path.abspath(args.input_dir)
    output_dir = os.path.abspath(args.output_dir)

    if not os.path.isdir(input_dir):
        print("ERROR: input_dir not found: %s" % input_dir)
        sys.exit(1)

    # Создаём структуру папок
    images_dir = os.path.join(output_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    # Загружаем данные
    subjects = load_all_results(input_dir)
    if not subjects:
        print("ERROR: no JSON files found in %s" % input_dir)
        sys.exit(1)
    print("Loaded %d subjects" % len(subjects))

    # PNG: копируем в reports/images/
    png_copied = 0
    for s in subjects:
        slice_file = s.get("slice_image", "")
        if not slice_file:
            continue
        src = os.path.join(input_dir, slice_file)
        dst = os.path.join(images_dir, slice_file)
        if os.path.isfile(src):
            shutil.copy2(src, dst)
            png_copied += 1

    print("Copied %d PNG files to %s" % (png_copied, images_dir))

    # Генерируем индивидуальные HTML
    for s in subjects:
        sid = s.get("subject_id", "unknown")
        html = build_subject_html(s)
        html_path = os.path.join(output_dir, "%s.html" % sid)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)

    print("Generated %d individual HTML reports" % len(subjects))

    # Генерируем index.html
    index_html = build_index_html(subjects)
    index_path = os.path.join(output_dir, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(index_html)
    print("Generated %s" % index_path)

    # CSV
    write_summary_csv(subjects, output_dir)

    print("\nDone. Reports in: %s" % output_dir)
    print("  Open: %s" % index_path)


if __name__ == "__main__":
    main()
