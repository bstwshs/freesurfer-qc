"""
Ядро валидации (QC Core Logic) — версия 4.
Загружает нормы из разных источников (csv, freesurfer, enigma, brainchart),
вычисляет Z-score, формирует вердикт.
Регионы без норм помечаются как 'Unknown', а не вызывают ошибку.
"""

from pathlib import Path
from typing import Optional, Tuple
import pandas as pd
import numpy as np
import warnings

from normative_tables import load_norms


def run_qc(
    df: pd.DataFrame,
    norm_source: str = "csv",
    csv_path: Optional[str] = None,
    aseg_stats_path: Optional[str] = None,
    enigma_path: Optional[str] = None,
) -> Tuple[pd.DataFrame, str]:
    """
    Принимает DataFrame с колонками [Region, Value, Type] и параметры источника норм.
    Возвращает (DataFrame с добавленными колонками Mean, Std, Zscore, Verdict,
    source_label).

    norm_source:
      - 'csv': загружает нормы из csv_path (norms.csv)
      - 'freesurfer': извлекает интенсивностные нормы из aseg_stats_path (normMean)
      - 'enigma': загружает ENIGMA-нормы (требует enigma_path)
      - 'brainchart': загружает нормы из Brain Charts (Nature 2022) — PKL-модели

    Для 'brainchart':
      - Для Type='Volume' используются прямые имена (совпадают с aseg).
      - Для Type='Thickness' — композитные DK-нормы (Destrieux G_/S_/G&S_
        компоненты, pooled mean/std).
      - Регионы без маппинга → Verdict='Unknown'.

    Регионы без норм получают Verdict='Unknown' и Zscore=NaN.
    """
    # Копируем df, чтобы не мутировать входной DataFrame
    df = df.copy()

    # --- Brain Charts: особый путь ---
    if norm_source == "brainchart":
        return _run_qc_brainchart(df)

    # --- Стандартный путь: через normative_tables ---
    norms, source_label = load_norms(
        norm_source=norm_source,
        csv_path=csv_path,
        aseg_stats_path=aseg_stats_path,
        enigma_path=enigma_path,
    )

    # Проверка структуры norms
    required_cols = {"Region", "Type", "Mean", "Std"}
    if not required_cols.issubset(norms.columns):
        raise ValueError(
            f"Нормы должны содержать колонки: {required_cols}. "
            f"Найдено: {list(norms.columns)}"
        )

    # Слияние по Region и Type (left join — сохраняем все регионы)
    df = df.merge(norms, on=["Region", "Type"], how="left")

    # Регионы с нормами
    has_norms = df["Mean"].notna()

    # Z-score только для тех, у кого есть нормы
    df["Zscore"] = np.nan
    df.loc[has_norms, "Zscore"] = (
        (df.loc[has_norms, "Value"] - df.loc[has_norms, "Mean"])
        / df.loc[has_norms, "Std"]
    )

    # Вердикт
    df["Verdict"] = _classify_verdict(df)

    # Округление
    df["Zscore"] = df["Zscore"].round(2)

    return df, source_label


# === Brain Charts QC ===

def _run_qc_brainchart(df: pd.DataFrame) -> Tuple[pd.DataFrame, str]:
    """
    Загружает нормы Brain Charts и вычисляет Z-score.
    - Для Volume: точное совпадение имён (aseg = BrainChart).
    - Для Thickness: композитные DK-нормы (Destrieux → Desikan-Killiany
      через pooled mean/std всех G_/S_/G&S_ компонентов).
    """
    from load_norms_brainchart import (
        load_volumes_norms,
        load_composite_thickness_norms,
        map_region_name,
    )

    # Загружаем оба набора норм
    vol_norms = load_volumes_norms()
    thick_norms = load_composite_thickness_norms()

    if vol_norms is None and thick_norms is None:
        raise ValueError(
            "Brain Charts данные не найдены. Скачайте архив с "
            "https://zenodo.org/records/5535467 и распакуйте в brainchart_norms/"
        )

    # Инициализируем колонки
    df = df.copy()
    df["Mean"] = np.nan
    df["Std"] = np.nan

    matched_count = 0
    unknown_count = 0

    # --- Volume регионы: прямое сопоставление (имена совпадают) ---
    if vol_norms is not None:
        vol_mask = df["Type"] == "Volume"
        for idx in df[vol_mask].index:
            region_name = df.at[idx, "Region"]
            norm_row = vol_norms[vol_norms["region"] == region_name]
            if not norm_row.empty:
                df.at[idx, "Mean"] = norm_row.iloc[0]["mean"]
                df.at[idx, "Std"] = norm_row.iloc[0]["std"]
                matched_count += 1
            else:
                unknown_count += 1

    # --- Thickness: композитные DK-нормы ---
    # map_region_name('lh-bankssts') → 'bankssts' (DK-имя без префикса)
    # thick_norms содержит строки с region='bankssts' (одна норма на оба полушария)
    if thick_norms is not None:
        thick_mask = df["Type"] == "Thickness"
        for idx in df[thick_mask].index:
            region_name = df.at[idx, "Region"]
            # Преобразуем lh-<dk> → <dk>
            dk_name = map_region_name(region_name, source="brainchart")
            if dk_name is not None:
                norm_row = thick_norms[thick_norms["region"] == dk_name]
                if not norm_row.empty:
                    df.at[idx, "Mean"] = norm_row.iloc[0]["mean"]
                    df.at[idx, "Std"] = norm_row.iloc[0]["std"]
                    matched_count += 1
                    continue
            unknown_count += 1

    # Z-score
    has_norms = df["Mean"].notna()
    df["Zscore"] = np.nan
    df.loc[has_norms, "Zscore"] = (
        (df.loc[has_norms, "Value"] - df.loc[has_norms, "Mean"])
        / df.loc[has_norms, "Std"]
    )

    # Вердикт
    df["Verdict"] = _classify_verdict(df)

    # Округление
    df["Zscore"] = df["Zscore"].round(2)

    source_label = (
        f"Brain Charts (Nature 2022) — "
        f"сопоставлено: {matched_count}, Unknown: {unknown_count}"
    )

    return df, source_label


# === Вспомогательные функции ===

def _classify_verdict(df: pd.DataFrame) -> pd.Series:
    """Классифицирует регионы по Z-score: OK / Check / Bad / Unknown."""
    def _classify(z):
        if pd.isna(z):
            return "Unknown"
        abs_z = abs(z)
        if abs_z < 2:
            return "OK"
        elif abs_z < 3:
            return "Check"
        else:
            return "Bad"
    return df["Zscore"].apply(_classify)
