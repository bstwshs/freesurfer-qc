"""
Модуль нормативных таблиц (Normative Tables) — версия 1.
Загрузка референсных норм из разных источников:
  - freesurfer: aseg.stats (normMean — интенсивностная норма)
  - csv: norms.csv (популяционные объёмные/thickness нормы)
  - enigma: ENIGMA-нормы (заглушка, требует доступа к ENIGMA-файлам)
"""

from pathlib import Path
from typing import Optional, Tuple
import pandas as pd
import numpy as np


# === FreeSurfer aseg.stats ===

def load_aseg_norms(aseg_stats_path: str) -> pd.DataFrame:
    """
    Извлекает нормативные данные из aseg.stats.
    Колонка normMean (индекс 5 в данных, после ColHeaders) используется как
    референсное значение интенсивности для каждой структуры.

    Returns:
        DataFrame с колонками: Region, Mean, Std
        (Std заполняется NaN — в aseg.stats есть normStdDev,
         но это интенсивностная дисперсия, не популяционная).
    """
    path = Path(aseg_stats_path)
    if not path.exists():
        raise FileNotFoundError(f"aseg.stats не найден: {path.resolve()}")

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Ищем строку ColHeaders
    data_start = 0
    for i, line in enumerate(lines):
        if line.startswith("# ColHeaders"):
            data_start = i + 1
            break

    if data_start == 0:
        raise ValueError(f"Не найдена строка ColHeaders в {path}")

    rows = []
    for line in lines[data_start:]:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 6:
            continue

        try:
            # Формат: Index SegId NVoxels Volume_mm3 StructName normMean ...
            # Составные имена (Left-Lateral-Ventricle) обрабатываем
            struct_name = parts[4]
            if len(parts) > 10:
                name_end = 5
                for j in range(5, len(parts)):
                    try:
                        float(parts[j])
                        break
                    except ValueError:
                        name_end = j + 1
                struct_name = " ".join(parts[4:name_end])
                norm_mean = float(parts[name_end])
                norm_std = float(parts[name_end + 1]) if len(parts) > name_end + 1 else np.nan
            else:
                struct_name = parts[4]
                norm_mean = float(parts[5])
                norm_std = float(parts[6]) if len(parts) > 6 else np.nan

            # Пропускаем структуры с нулевым объёмом (например, 5th-Ventricle)
            volume_mm3 = float(parts[3])
            if volume_mm3 == 0:
                continue

            rows.append({
                "Region": struct_name,
                "Type": "Volume",
                "Mean": norm_mean,
                "Std": norm_std if not np.isnan(norm_std) and norm_std > 0 else np.nan,
            })
        except (ValueError, IndexError):
            continue

    return pd.DataFrame(rows)


# === ENIGMA (заглушка) ===

def load_enigma_norms(enigma_path: Optional[str] = None) -> pd.DataFrame:
    """
    Загружает ENIGMA-нормы. Пока заглушка — возвращает пустой DataFrame.
    Для реального использования требуется скачать CSV с ENIGMA-сервера
    и указать путь к нему.
    """
    if enigma_path and Path(enigma_path).exists():
        return pd.read_csv(enigma_path)

    # Заглушка: пустой DataFrame с правильными колонками
    return pd.DataFrame(columns=["Region", "Type", "Mean", "Std"])


# === Диспетчер загрузки норм ===

def load_norms(norm_source: str = "csv",
               csv_path: Optional[str] = None,
               aseg_stats_path: Optional[str] = None,
               enigma_path: Optional[str] = None) -> Tuple[pd.DataFrame, str]:
    """
    Загружает нормативные данные из указанного источника.

    Args:
        norm_source: 'csv', 'freesurfer', или 'enigma'
        csv_path: путь к norms.csv (для source='csv')
        aseg_stats_path: путь к aseg.stats (для source='freesurfer')
        enigma_path: путь к ENIGMA-файлу (для source='enigma')

    Returns:
        (DataFrame с колонками Region, Type, Mean, Std, source_label)
    """
    if norm_source == "freesurfer":
        if not aseg_stats_path:
            raise ValueError("Для norm_source='freesurfer' нужен aseg_stats_path")
        norms = load_aseg_norms(aseg_stats_path)
        norms["Type"] = "Volume"
        source_label = f"FreeSurfer aseg.stats ({aseg_stats_path})"
    elif norm_source == "enigma":
        norms = load_enigma_norms(enigma_path)
        if norms.empty:
            raise ValueError(
                "ENIGMA-нормы недоступны. Укажите enigma_path или "
                "скачайте данные с https://enigma.ini.usc.edu/"
            )
        source_label = f"ENIGMA ({enigma_path or 'default'})"
    else:  # csv
        if not csv_path:
            raise ValueError("Для norm_source='csv' нужен csv_path")
        norms = pd.read_csv(csv_path)
        source_label = f"CSV ({csv_path})"

    # Проверка обязательных колонок
    required = {"Region", "Type", "Mean", "Std"}
    if not required.issubset(norms.columns):
        raise ValueError(
            f"Нормы должны содержать колонки: {required}. "
            f"Найдено: {list(norms.columns)}"
        )

    return norms, source_label
