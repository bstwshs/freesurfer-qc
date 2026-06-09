#!/usr/bin/env python3
"""
Пакетная обработка папок FreeSurfer — batch_qc.py

Для каждого субъекта (RNS001, RNS002, ...) выполняет:
  1. Проверку обязательных файлов.
  2. Парсинг .stats (parser_engine).
  3. Загрузку норм Brain Charts (load_norms_brainchart).
  4. Расчёт Z-score и вердиктов (qc_core).
  5. Вычисление SNR для orig/001.mgz.
  6. Генерацию PNG с overlay-подсветкой (visualizer.save_slice_image).
  7. Сохранение результатов в JSON.

Использование:
  python batch_qc.py --subjects_dir /Volumes/.../RNS
  python batch_qc.py --subjects_dir /Volumes/.../RNS --single RNS042
  python batch_qc.py --subjects_dir /Volumes/.../RNS --output_dir my_results
"""

import argparse
import json
import os
import sys
import traceback
import warnings
from pathlib import Path

import nibabel as nib
import numpy as np
import pandas as pd

# Добавляем корень проекта в sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from input_handler import _find_file  # type: ignore
from parser_engine import parse_stats  # type: ignore
from qc_core import run_qc  # type: ignore
from load_norms_brainchart import load_volumes_norms, load_composite_thickness_norms, map_region_name  # type: ignore
from visualizer import (
    load_aseg_labels,
    build_aseg_lookup,
    build_reverse_lookup,
    compute_flagged_label_ids,
    save_slice_image,
)

warnings.filterwarnings("ignore")

# ============================================================
# SNR
# ============================================================

def compute_snr(mgz_path: str) -> float:
    """
    Вычисляет SNR для МРТ-изображения.

    Шум: std в угловой области (первые 10x10x10 вокселей).
    Сигнал: среднее в центральных 20% объёма.
    SNR = mean(сигнал) / std(шум). Если std близок к нулю — 0.
    """
    try:
        img = nib.load(mgz_path)
        data = np.asarray(img.get_fdata(), dtype=np.float32)
    except Exception:
        return 0.0

    # Угловая область — шум
    corner = data[:10, :10, :10]
    noise_std = float(np.std(corner))
    if noise_std < 1e-6:
        return 0.0

    # Центральные 20% — сигнал
    sx, sy, sz = data.shape
    cx_start = int(sx * 0.4)
    cx_end = int(sx * 0.6)
    cy_start = int(sy * 0.4)
    cy_end = int(sy * 0.6)
    cz_start = int(sz * 0.4)
    cz_end = int(sz * 0.6)
    center = data[cx_start:cx_end, cy_start:cy_end, cz_start:cz_end]
    signal_mean = float(np.mean(center))

    return round(signal_mean / noise_std, 2)


# ============================================================
# File helpers
# ============================================================

def check_subject_files(subject_dir: str) -> dict:
    """Проверяет наличие обязательных файлов. Возвращает словарь путей."""
    folder = Path(subject_dir)
    result = {}

    # aseg.stats
    aseg_stats = _find_file(folder, "aseg.stats")
    if not aseg_stats:
        raise FileNotFoundError("aseg.stats не найден в %s" % folder)
    result["aseg_stats"] = str(aseg_stats)

    # aparc.stats (lh + rh, или одиночный)
    lh = _find_file(folder, "lh.aparc.stats")
    rh = _find_file(folder, "rh.aparc.stats")
    aparc_single = _find_file(folder, "aparc.stats")
    if lh or rh or aparc_single:
        result["lh_aparc"] = str(lh) if lh else None
        result["rh_aparc"] = str(rh) if rh else None
        result["aparc_single"] = str(aparc_single) if aparc_single else None
    else:
        raise FileNotFoundError("lh.aparc.stats / rh.aparc.stats не найдены")

    # orig/001.mgz → T1.mgz fallback
    orig_mgz = folder / "mri" / "orig" / "001.mgz"
    t1_mgz = _find_file(folder, "T1.mgz")
    if orig_mgz.exists() and orig_mgz.stat().st_size > 0:
        result["orig_mgz"] = str(orig_mgz)
    elif t1_mgz and t1_mgz.stat().st_size > 0:
        result["orig_mgz"] = str(t1_mgz)
        result["orig_warning"] = "T1.mgz used (orig/001.mgz not found)"
    else:
        result["orig_mgz"] = None
        result["orig_warning"] = "Neither orig/001.mgz nor T1.mgz found"

    # brain.mgz
    brain = _find_file(folder, "brain.mgz")
    result["brain_mgz"] = str(brain) if brain else None

    # aseg.mgz → aparc+aseg.mgz fallback
    aseg_mgz = _find_file(folder, "aseg.mgz")
    if aseg_mgz and aseg_mgz.stat().st_size > 0:
        result["aseg_mgz"] = str(aseg_mgz)
    else:
        aparc_aseg = _find_file(folder, "aparc+aseg.mgz")
        if aparc_aseg and aparc_aseg.stat().st_size > 0:
            result["aseg_mgz"] = str(aparc_aseg)
        else:
            result["aseg_mgz"] = None

    return result


# ============================================================
# Main batch processing
# ============================================================

def process_subject(subject_id: str, subject_dir: str, output_dir: str) -> dict:
    """Обрабатывает одного субъекта. Возвращает словарь для JSON."""
    result = {
        "subject_id": subject_id,
        "snr": 0.0,
        "verdict_counts": {"OK": 0, "Check": 0, "Bad": 0, "Unknown": 0},
        "regions": [],
        "slice_image": "",
        "warnings": [],
    }

    # ---- 1. Проверка файлов ----
    try:
        files = check_subject_files(subject_dir)
    except FileNotFoundError as e:
        result["error"] = str(e)
        return result

    if files.get("orig_warning"):
        result["warnings"].append(files["orig_warning"])

    # ---- 2. Парсинг ----
    try:
        df = parse_stats(subject_dir)
    except Exception as e:
        result["error"] = "Парсинг: %s" % e
        return result

    # ---- 3. QC (Brain Charts) ----
    try:
        df_result, source_label = run_qc(df, norm_source="brainchart")
    except Exception as e:
        # Fallback: пробуем CSV
        csv_path = os.path.join(str(PROJECT_ROOT), "norms.csv")
        try:
            df_result, source_label = run_qc(df, norm_source="csv", csv_path=csv_path)
            result["warnings"].append("Brain Charts failed, used CSV: %s" % e)
        except Exception as e2:
            result["error"] = "QC: %s / CSV: %s" % (e, e2)
            return result

    # ---- 4. Вердикты ----
    vc = df_result["Verdict"].value_counts()
    result["verdict_counts"] = {
        "OK": int(vc.get("OK", 0)),
        "Check": int(vc.get("Check", 0)),
        "Bad": int(vc.get("Bad", 0)),
        "Unknown": int(vc.get("Unknown", 0)),
    }

    # Регионы (все)
    for _, row in df_result.iterrows():
        result["regions"].append({
            "Region": str(row["Region"]),
            "Type": str(row["Type"]),
            "Value": float(row["Value"]) if not pd.isna(row["Value"]) else None,
            "Zscore": float(round(row["Zscore"], 2)) if not pd.isna(row["Zscore"]) else None,
            "Verdict": str(row["Verdict"]),
        })

    # ---- 5. SNR ----
    if files.get("orig_mgz"):
        result["snr"] = compute_snr(files["orig_mgz"])

    # ---- 6. PNG срез ----
    flagged_mask = df_result["Verdict"].isin(["Check", "Bad"])
    flagged_regions = df_result.loc[flagged_mask, "Region"].tolist()

    if flagged_regions and files.get("brain_mgz") and files.get("aseg_mgz"):
        try:
            brain_data = np.asarray(
                nib.load(files["brain_mgz"]).get_fdata(), dtype=np.float32
            )
            aseg_data = load_aseg_labels(files["aseg_mgz"])

            # Build lookup
            lookup = build_aseg_lookup(files["aseg_stats"])
            reverse_lookup = build_reverse_lookup(lookup)
            flagged_label_ids = compute_flagged_label_ids(
                flagged_regions, df_result, reverse_lookup
            )

            png_name = "%s_slice.png" % subject_id
            png_path = os.path.join(output_dir, png_name)
            save_slice_image(
                brain_data,
                aseg_data,
                flagged_regions,
                flagged_label_ids,
                png_path,
                axis="axial",
                show_overlay=True,
                alpha_bad=0.4,
                alpha_check=0.35,
                dpi=150,
            )
            result["slice_image"] = png_name
        except Exception as e:
            result["warnings"].append("Slice image failed: %s" % e)

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Пакетная обработка QC для папок FreeSurfer"
    )
    parser.add_argument(
        "--subjects_dir",
        required=True,
        help="Путь к папке с подпапками субъектов (RNS001, RNS002, ...)",
    )
    parser.add_argument(
        "--single",
        default=None,
        help="Обработать только одного субъекта (например, RNS042) — для теста",
    )
    parser.add_argument(
        "--output_dir",
        default="qc_results",
        help="Папка для сохранения результатов (по умолчанию qc_results)",
    )
    args = parser.parse_args()

    subjects_dir = os.path.abspath(args.subjects_dir)
    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    if not os.path.isdir(subjects_dir):
        print("ERROR: subjects_dir не найдена: %s" % subjects_dir)
        sys.exit(1)

    # Список субъектов
    if args.single:
        subject_ids = [args.single]
    else:
        all_entries = sorted(os.listdir(subjects_dir))
        subject_ids = [
            e for e in all_entries
            if e.startswith("RNS") and os.path.isdir(os.path.join(subjects_dir, e))
        ]

    if not subject_ids:
        print("ERROR: не найдено ни одной папки субъекта (RNS*) в %s" % subjects_dir)
        sys.exit(1)

    print("Найдено субъектов: %d" % len(subject_ids))

    # tqdm
    try:
        from tqdm import tqdm
        iterator = tqdm(subject_ids, desc="Processing", unit="subj")
    except ImportError:
        iterator = subject_ids
        print("(tqdm не установлен — pip install tqdm)")

    success_count = 0
    error_count = 0

    for subject_id in iterator:
        subject_dir = os.path.join(subjects_dir, subject_id)
        if not os.path.isdir(subject_dir):
            if isinstance(iterator, type(subject_ids)):
                print("SKIP %s: не папка" % subject_id)
            continue

        result = process_subject(subject_id, subject_dir, output_dir)

        # Сохраняем JSON
        json_path = os.path.join(output_dir, "%s.json" % subject_id)
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print("ERROR saving JSON for %s: %s" % (subject_id, e))

        if "error" in result:
            error_count += 1
        else:
            success_count += 1

    # Итоговый summary.json
    summary = {
        "total_subjects": len(subject_ids),
        "success": success_count,
        "errors": error_count,
        "subjects_dir": subjects_dir,
    }
    summary_path = os.path.join(output_dir, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print("\nГотово. Успешно: %d, ошибок: %d. Результаты в %s" % (
        success_count, error_count, output_dir))


if __name__ == "__main__":
    main()
