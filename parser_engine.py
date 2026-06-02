"""
Модуль парсинга выходных файлов FreeSurfer (Parser Engine) — версия 2.
Поддерживает реальные форматы:
  - stats/aseg.stats (подкорковые объёмы)
  - stats/lh.aparc.stats, stats/rh.aparc.stats (кортикальная толщина)
Совместим с синтетическими данными (один aparc.stats).
"""

from pathlib import Path
import pandas as pd


def _parse_aseg_stats(filepath: Path) -> pd.DataFrame:
    """
    Парсит aseg.stats. Извлекает: StructName -> Region, Volume_mm3 -> Value.
    Тип: 'Volume'.
    """
    # Сначала находим строку с ColHeaders, затем читаем данные
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Ищем строку ColHeaders — с неё начинается таблица (пропускаем её)
    data_start = 0
    for i, line in enumerate(lines):
        if line.startswith("# ColHeaders"):
            data_start = i + 1
            break

    if data_start == 0:
        raise ValueError(f"Не найдена строка ColHeaders в {filepath}")

    # Собираем строки данных (без комментариев)
    data_lines = []
    for line in lines[data_start:]:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        data_lines.append(line)

    if not data_lines:
        raise ValueError(f"Нет данных после ColHeaders в {filepath}")

    # Парсим через pandas
    rows = []
    for line in data_lines:
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            # Формат: Index SegId NVoxels Volume_mm3 StructName [normMean ...]
            region = parts[4]  # StructName
            # Составные имена могут быть через дефис: Left-Lateral-Ventricle
            # Если строка длиннее 10 — имя составное
            if len(parts) > 10:
                # StructName заканчивается перед normMean
                # Ищем позицию: parts[5:] всё до первого числа normMean
                name_end = 5
                for j in range(5, len(parts)):
                    try:
                        float(parts[j])
                        break
                    except ValueError:
                        name_end = j + 1
                region = " ".join(parts[4:name_end])
                value = float(parts[3])
            else:
                region = parts[4]
                value = float(parts[3])
        except (ValueError, IndexError):
            continue
        rows.append({"Region": region, "Value": value, "Type": "Volume"})

    return pd.DataFrame(rows)


def _parse_aparc_stats(filepath: Path, hemi_prefix: str = "") -> pd.DataFrame:
    """
    Парсит aparc.stats (lh или rh).
    Извлекает: StructName -> Region, ThickAvg -> Value.
    hemi_prefix: "lh-" или "rh-", добавляется к имени региона.
    """
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    data_start = 0
    for i, line in enumerate(lines):
        if line.startswith("# ColHeaders"):
            data_start = i + 1
            break

    if data_start == 0:
        raise ValueError(f"Не найдена строка ColHeaders в {filepath}")

    data_lines = []
    for line in lines[data_start:]:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        data_lines.append(line)

    if not data_lines:
        raise ValueError(f"Нет данных после ColHeaders в {filepath}")

    rows = []
    for line in data_lines:
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            # Формат: StructName NumVert SurfArea GrayVol ThickAvg ThickStd ...
            region = parts[0]
            if hemi_prefix:
                region = hemi_prefix + region
            value = float(parts[4])  # ThickAvg
        except (ValueError, IndexError):
            continue
        rows.append({"Region": region, "Value": value, "Type": "Thickness"})

    return pd.DataFrame(rows)


def _find_in_subject(folder: Path, filename: str) -> Path | None:
    """Ищет файл в folder/, folder/stats/, folder/mri/."""
    for sub in [folder, folder / "stats", folder / "mri"]:
        candidate = sub / filename
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def parse_stats(folder_path: str) -> pd.DataFrame:
    """
    Главная точка входа. Принимает путь к папке субъекта.
    Ищет aseg.stats, lh.aparc.stats, rh.aparc.stats (или aparc.stats)
    в папке и её подпапках stats/, mri/.
    Возвращает DataFrame с колонками: Region, Value, Type.
    """
    folder = Path(folder_path)

    # --- ASEG ---
    aseg_path = _find_in_subject(folder, "aseg.stats")
    if aseg_path is None:
        raise FileNotFoundError(f"aseg.stats не найден в {folder} (искал в ., stats/, mri/)")

    df_aseg = _parse_aseg_stats(aseg_path)

    # --- APARC: пробуем сначала lh/rh, затем одиночный ---
    lh_path = _find_in_subject(folder, "lh.aparc.stats")
    rh_path = _find_in_subject(folder, "rh.aparc.stats")
    aparc_single = _find_in_subject(folder, "aparc.stats")

    df_aparc_parts = []

    if lh_path is not None:
        df_aparc_parts.append(_parse_aparc_stats(lh_path, "lh-"))
    if rh_path is not None:
        df_aparc_parts.append(_parse_aparc_stats(rh_path, "rh-"))

    if not df_aparc_parts and aparc_single is not None:
        # Совместимость с синтетическими данными (один aparc.stats)
        df_aparc_parts.append(_parse_aparc_stats(aparc_single, ""))

    if not df_aparc_parts:
        raise FileNotFoundError(
            f"Не найдены aparc-файлы в {folder}. "
            f"Ожидаются: lh.aparc.stats, rh.aparc.stats или aparc.stats"
        )

    df_aparc = pd.concat(df_aparc_parts, ignore_index=True)

    # --- Объединение ---
    if df_aseg.empty and df_aparc.empty:
        raise ValueError("Не удалось извлечь данные из .stats файлов.")

    df = pd.concat([df_aseg, df_aparc], ignore_index=True)
    return df
