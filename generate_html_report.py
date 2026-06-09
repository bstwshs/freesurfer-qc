#!/usr/bin/env python3
"""
Генератор HTML-отчёта по результатам пакетного QC — generate_html_report.py

Читает все JSON-файлы из папки qc_results/, группирует субъектов
по доле проблемных регионов и генерирует автономный HTML-файл
(изображения PNG встроены в base64).

Использование:
  python generate_html_report.py --results_dir qc_results --output qc_report.html
"""

import argparse
import base64
import json
import os
import sys
from datetime import datetime
from pathlib import Path


def encode_image_base64(image_path: str) -> str:
    """Кодирует PNG в base64 data URI."""
    try:
        with open(image_path, "rb") as f:
            data = f.read()
        return "data:image/png;base64," + base64.b64encode(data).decode("ascii")
    except Exception:
        return ""


def load_results(results_dir: str) -> list:
    """Загружает все JSON-файлы субъектов из папки результатов."""
    subjects = []
    for fname in sorted(os.listdir(results_dir)):
        if not fname.endswith(".json") or fname == "summary.json":
            continue
        fpath = os.path.join(results_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            data["_json_file"] = fname
            subjects.append(data)
        except Exception:
            continue
    return subjects


def verdict_color(verdict):
    return {"OK": "#27ae60", "Check": "#f39c12", "Bad": "#e74c3c"}.get(verdict, "#95a5a6")


def build_html(subjects: list, results_dir: str) -> str:
    """Строит HTML-отчёт."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Сортировка: сначала с Bad, потом с Check, потом OK
    def sort_key(s):
        vc = s.get("verdict_counts", {})
        return (-vc.get("Bad", 0), -vc.get("Check", 0), s.get("subject_id", ""))

    subjects_sorted = sorted(subjects, key=sort_key)

    total = len(subjects_sorted)
    if total == 0:
        return "<html><body><h1>Нет данных</h1></body></html>"

    # Сводная статистика
    all_ok = sum(s.get("verdict_counts", {}).get("OK", 0) for s in subjects_sorted)
    all_check = sum(s.get("verdict_counts", {}).get("Check", 0) for s in subjects_sorted)
    all_bad = sum(s.get("verdict_counts", {}).get("Bad", 0) for s in subjects_sorted)
    all_unknown = sum(s.get("verdict_counts", {}).get("Unknown", 0) for s in subjects_sorted)
    avg_snr = sum(s.get("snr", 0) for s in subjects_sorted if s.get("snr", 0) > 0)
    snr_count = sum(1 for s in subjects_sorted if s.get("snr", 0) > 0)

    rows = []
    for s in subjects_sorted:
        sid = s.get("subject_id", "?")
        vc = s.get("verdict_counts", {})
        snr = s.get("snr", 0)
        total_regions = sum(vc.values()) if vc else 0
        bad = vc.get("Bad", 0)
        check = vc.get("Check", 0)
        problem_pct = round((bad + check) / max(total_regions, 1) * 100, 1)

        # Row class
        if bad > 0:
            row_class = "bad"
        elif check > 0:
            row_class = "check"
        elif "error" in s:
            row_class = "error"
        else:
            row_class = "ok"

        # Slice image
        slice_img = ""
        slice_file = s.get("slice_image", "")
        if slice_file:
            img_path = os.path.join(results_dir, slice_file)
            b64 = encode_image_base64(img_path)
            if b64:
                slice_img = '<img src="%s" class="slice-img" />' % b64

        # Error message
        error_msg = ""
        if "error" in s:
            error_msg = '<div class="error-msg">%s</div>' % s["error"]

        # Warnings
        warnings_html = ""
        if s.get("warnings"):
            warnings_html = "<ul>" + "".join(
                "<li>%s</li>" % w for w in s["warnings"]
            ) + "</ul>"

        # Tiny bars
        ok_val = vc.get("OK", 0)

        rows.append("""
        <tr class="{row_class}">
            <td><strong>{sid}</strong>{error_msg}</td>
            <td class="num">{snr:.1f}</td>
            <td class="num">{total_regions}</td>
            <td class="num ok-cell">{ok_val}</td>
            <td class="num check-cell">{check}</td>
            <td class="num bad-cell">{bad}</td>
            <td class="num unknown-cell">{unknown_val}</td>
            <td class="num">{problem_pct}%</td>
            <td class="img-cell">{slice_img}</td>
        </tr>
        """.format(
            row_class=row_class,
            sid=sid,
            error_msg=error_msg,
            snr=snr,
            total_regions=total_regions,
            ok_val=ok_val,
            check=check,
            bad=bad,
            unknown_val=vc.get("Unknown", 0),
            problem_pct=problem_pct,
            slice_img=slice_img,
        ))

    html = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QC FreeSurfer — Batch Report</title>
<style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #f5f6fa; padding: 20px; color: #2c3e50; }}
    .container {{ max-width: 1400px; margin: 0 auto; }}
    h1 {{ font-size: 24px; margin-bottom: 8px; }}
    .subtitle {{ color: #7f8c8d; font-size: 14px; margin-bottom: 24px; }}

    .summary-cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 24px; }}
    .card {{ background: white; border-radius: 10px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); text-align: center; }}
    .card .value {{ font-size: 28px; font-weight: 700; }}
    .card .label {{ font-size: 12px; color: #7f8c8d; margin-top: 4px; }}
    .card.ok .value {{ color: #27ae60; }}
    .card.check .value {{ color: #f39c12; }}
    .card.bad .value {{ color: #e74c3c; }}
    .card.unknown .value {{ color: #95a5a6; }}

    table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.08); font-size: 13px; }}
    th {{ background: #2c3e50; color: white; padding: 10px 8px; text-align: left; font-weight: 600; white-space: nowrap; }}
    td {{ padding: 8px; border-bottom: 1px solid #ecf0f1; }}
    tr:hover {{ background: #f8f9fa; }}
    tr.bad {{ background: #fdecea; }}
    tr.check {{ background: #fef9e7; }}
    tr.error {{ background: #fadbd8; }}
    tr.ok {{  }}
    .num {{ text-align: right; }}
    .ok-cell {{ color: #27ae60; font-weight: 600; }}
    .check-cell {{ color: #f39c12; font-weight: 600; }}
    .bad-cell {{ color: #e74c3c; font-weight: 600; }}
    .unknown-cell {{ color: #95a5a6; }}
    .img-cell {{ text-align: center; min-width: 100px; }}
    .slice-img {{ max-height: 60px; border-radius: 4px; cursor: pointer; }}
    .error-msg {{ color: #e74c3c; font-size: 11px; }}

    .footer {{ text-align: center; color: #bdc3c7; font-size: 12px; margin-top: 24px; }}
</style>
</head>
<body>
<div class="container">
    <h1>QC FreeSurfer — Сводный отчёт</h1>
    <p class="subtitle">Сгенерирован: {now} | Субъектов: {total}</p>

    <div class="summary-cards">
        <div class="card ok">
            <div class="value">{all_ok}</div>
            <div class="label">OK (|Z| < 2)</div>
        </div>
        <div class="card check">
            <div class="value">{all_check}</div>
            <div class="label">Check (2 ≤ |Z| < 3)</div>
        </div>
        <div class="card bad">
            <div class="value">{all_bad}</div>
            <div class="label">Bad (|Z| ≥ 3)</div>
        </div>
        <div class="card unknown">
            <div class="value">{all_unknown}</div>
            <div class="label">Unknown</div>
        </div>
        <div class="card">
            <div class="value">{avg_snr:.1f}</div>
            <div class="label">Средний SNR (n={snr_count})</div>
        </div>
        <div class="card">
            <div class="value">{problem_pct_global:.1f}%</div>
            <div class="label">Проблемных регионов</div>
        </div>
    </div>

    <table>
        <thead>
            <tr>
                <th>Субъект</th>
                <th>SNR</th>
                <th>Всего</th>
                <th>OK</th>
                <th>Check</th>
                <th>Bad</th>
                <th>Unknown</th>
                <th>Проблем %</th>
                <th>Срез</th>
            </tr>
        </thead>
        <tbody>
            {rows}
        </tbody>
    </table>

    <div class="footer">QC FreeSurfer Prototype &copy; 2026</div>
</div>
</body>
</html>""".format(
        now=now,
        total=total,
        all_ok=all_ok,
        all_check=all_check,
        all_bad=all_bad,
        all_unknown=all_unknown,
        avg_snr=avg_snr / max(snr_count, 1),
        snr_count=snr_count,
        problem_pct_global=round((all_check + all_bad) / max(all_ok + all_check + all_bad + all_unknown, 1) * 100, 1),
        rows="\n".join(rows),
    )

    return html


def main():
    parser = argparse.ArgumentParser(
        description="Генератор HTML-отчёта по результатам пакетного QC"
    )
    parser.add_argument(
        "--results_dir",
        default="qc_results",
        help="Папка с JSON-файлами результатов batch_qc.py",
    )
    parser.add_argument(
        "--output",
        default="qc_report.html",
        help="Имя выходного HTML-файла",
    )
    args = parser.parse_args()

    results_dir = os.path.abspath(args.results_dir)
    if not os.path.isdir(results_dir):
        print("ERROR: results_dir не найдена: %s" % results_dir)
        sys.exit(1)

    subjects = load_results(results_dir)
    print("Загружено субъектов: %d" % len(subjects))

    html = build_html(subjects, results_dir)

    output_path = os.path.abspath(args.output)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print("HTML-отчёт сохранён: %s (%d KB)" % (output_path, len(html) // 1024))


if __name__ == "__main__":
    main()
