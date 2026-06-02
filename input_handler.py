"""
Модуль проверки входных данных (Input Handler) — версия 4.
Поддерживает:
  - Прямую структуру: SUBJECT/stats/, SUBJECT/mri/
  - BIDS-вложенность: .../derivatives/freesurfer/subjects/sub-XX/
  - Fallback-имена: brain.mgz → T1.mgz, aseg.mgz → aparc+aseg.mgz
Авто-поиск субъекта, если указан родительский путь.
"""

from pathlib import Path
from typing import Tuple, Optional


def _find_file(folder: Path, filename: str) -> Optional[Path]:
    """Ищет файл: сначала в folder/, затем в folder/stats/, folder/mri/."""
    for sub in [folder, folder / "stats", folder / "mri"]:
        candidate = sub / filename
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _find_dir(folder: Path, dirname: str) -> Optional[Path]:
    """Ищет подпапку в folder/."""
    candidate = folder / dirname
    if candidate.exists() and candidate.is_dir():
        return candidate
    return None


def _has_stats_or_mri(folder: Path) -> bool:
    """Проверяет, есть ли в folder подпапки stats/ или mri/."""
    return (folder / "stats").is_dir() or (folder / "mri").is_dir()


def resolve_subject_path(raw_path: str) -> Tuple[Optional[str], str]:
    """
    Пытается найти реальный путь к папке субъекта FreeSurfer.

    Стратегия:
    1. Если по указанному пути уже есть stats/ или mri/ — возвращаем как есть.
    2. Если содержит derivatives/freesurfer/subjects/ — спускаемся до первого subject.
    3. Если это родительская папка — ищем derivatives/freesurfer/subjects/*/ внутри,
       берём первую папку субъекта.

    Returns:
        (resolved_path, diagnostic_message)
        Если resolved_path is None — субъект не найден.
    """
    folder = Path(raw_path)

    if not folder.exists():
        return None, f"Папка не найдена: {raw_path}"

    # Стратегия 1: прямой путь к субъекту
    if _has_stats_or_mri(folder):
        return str(folder), f"Прямой путь: {folder}"

    # Стратегия 2: путь уже содержит derivatives/freesurfer/subjects/sub-XX
    #   но, возможно, введён не до конца
    bids_tail = Path("derivatives") / "freesurfer" / "subjects"
    if bids_tail.as_posix() in folder.as_posix():
        # Ищем stats/ или mri/ внутри
        for subdir in sorted(folder.rglob("stats")):
            parent = subdir.parent
            if _has_stats_or_mri(parent):
                return str(parent), f"BIDS (прямой): {parent}"
        # Может, сам folder — это subjects/? Поищем подпапки
        for child in sorted(folder.iterdir()):
            if child.is_dir() and _has_stats_or_mri(child):
                return str(child), f"BIDS (дочерняя): {child}"
        return None, (f"BIDS-структура найдена, но нет stats/ или mri/ "
                      f"внутри {folder}")

    # Стратегия 3: поиск BIDS-цепочки внутри родительской папки
    bids_path = folder / "derivatives" / "freesurfer" / "subjects"
    if bids_path.exists() and bids_path.is_dir():
        subjects = sorted(
            [d for d in bids_path.iterdir()
             if d.is_dir() and not d.name.startswith(".")]
        )
        for subj in subjects:
            if _has_stats_or_mri(subj):
                return str(subj), f"BIDS (авто): derivatives/freesurfer/subjects/{subj.name}"

        # subjects/ есть, но внутри нет stats/mri — может, нужно глубже
        for subj in subjects:
            for child in sorted(subj.rglob("stats")):
                parent = child.parent
                if _has_stats_or_mri(parent):
                    return str(parent), f"BIDS (глубокий поиск): {parent.relative_to(folder)}"

        return None, (f"BIDS-папка найдена ({bids_path}), "
                      f"но субъекты не содержат stats/ или mri/")

    return None, (f"Путь не похож на папку субъекта FreeSurfer: "
                  f"нет stats/ или mri/ в {folder}")


def check_input_folder(folder_path: str) -> Tuple[bool, str]:
    """
    Проверяет существование обязательных файлов в папке субъекта.
    Возвращает (True, "") если всё в порядке,
    иначе (False, сообщение_об_ошибке).

    Обязательные файлы:
      - aseg.stats
      - lh.aparc.stats или aparc.stats
    Опциональные (не блокируют работу, но желательны):
      - brain.mgz или T1.mgz (для визуализации)
    """
    folder = Path(folder_path)

    if not folder.exists():
        return False, f"Папка не найдена: {folder.resolve()}"

    if not folder.is_dir():
        return False, f"Указанный путь не является папкой: {folder.resolve()}"

    missing = []

    # aseg.stats — обязателен
    if _find_file(folder, "aseg.stats") is None:
        missing.append("aseg.stats")

    # aparc: lh.aparc.stats или aparc.stats — обязателен
    has_aparc = (
        _find_file(folder, "lh.aparc.stats") is not None
        or _find_file(folder, "aparc.stats") is not None
    )
    if not has_aparc:
        missing.append("lh.aparc.stats или aparc.stats")

    if missing:
        return False, f"Отсутствуют обязательные файлы: {', '.join(missing)}"

    return True, ""


def resolve_file_path(folder_path: str, filename: str) -> Optional[str]:
    """
    Возвращает полный путь к файлу, ища его в folder/, folder/stats/, folder/mri/.
    Удобно для получения реального пути после проверки.
    """
    folder = Path(folder_path)
    result = _find_file(folder, filename)
    return str(result.resolve()) if result else None


def find_brain_mgz(subject_path: str) -> Optional[str]:
    """
    Ищет файл с мозгом для визуализации.
    Приоритет: brain.mgz → T1.mgz.
    """
    folder = Path(subject_path)
    # Сначала brain.mgz
    result = _find_file(folder, "brain.mgz")
    if result:
        return str(result)
    # Fallback: T1.mgz
    result = _find_file(folder, "T1.mgz")
    if result:
        return str(result)
    return None


def find_aseg_mgz(subject_path: str) -> Optional[str]:
    """
    Ищет файл сегментации для overlay.
    Приоритет: aparc+aseg.mgz → aseg.mgz.
    aparc+aseg.mgz содержит и подкорковые, и кортикальные метки —
    это обеспечивает полный overlay для всех регионов.
    Отбрасывает пустые файлы (0 байт).
    """
    folder = Path(subject_path)
    # Сначала aparc+aseg.mgz (полный набор меток: aseg + DK)
    result = _find_file(folder, "aparc+aseg.mgz")
    if result and result.stat().st_size > 0:
        return str(result)
    # Fallback: aseg.mgz (только подкорковые метки)
    result = _find_file(folder, "aseg.mgz")
    if result and result.stat().st_size > 0:
        return str(result)
    return None


def find_aseg_stats_path(subject_path: str) -> Optional[str]:
    """Ищет aseg.stats для построения lookup-таблицы."""
    folder = Path(subject_path)
    result = _find_file(folder, "aseg.stats")
    return str(result) if result else None


def diagnose_files(subject_path: str) -> dict:
    """
    Диагностика: какие файлы найдены, какие нет.
    Возвращает словарь для отображения в UI.
    """
    return {
        "brain_source": _classify_find(subject_path, "brain.mgz", "T1.mgz"),
        "aseg_source": _classify_find(subject_path, "aseg.mgz", "aparc+aseg.mgz"),
        "aseg_stats": "OK" if find_aseg_stats_path(subject_path) else "MISS",
        "lh_aparc": "OK" if _find_file(Path(subject_path), "lh.aparc.stats") else "MISS",
        "rh_aparc": "OK" if _find_file(Path(subject_path), "rh.aparc.stats") else "MISS",
    }


def _classify_find(subject_path: str, primary: str, fallback: str) -> str:
    """Возвращает строку: какой файл найден (ASCII-safe)."""
    folder = Path(subject_path)
    p = _find_file(folder, primary)
    if p and p.stat().st_size > 0:
        return f"OK: {primary}"
    f = _find_file(folder, fallback)
    if f and f.stat().st_size > 0:
        return f"FALLBACK: {fallback} (instead of {primary})"
    # Проверим, есть ли файл но пустой
    for name in [primary, fallback]:
        c = _find_file(folder, name)
        if c and c.stat().st_size == 0:
            return f"EMPTY: {name} (0 bytes)"
    return f"MISS: no {primary} or {fallback}"
