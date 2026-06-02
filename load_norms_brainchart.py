"""
Модуль загрузки нормативных данных Brain Charts (Nature 2022).
Источник: Rutherford et al. "Charting brain growth and aging at high
spatial precision" (eLife, 2022). Zenodo: https://zenodo.org/records/5535467.

Данные хранятся в brainchart_norms/ как сериализованные .pkl модели
формата norm_blr (NM_0_0_estimate.pkl + meta_data.md).
Из meta_data.md извлекаются mean_resp и std_resp — популяционные
среднее и стандартное отклонение для каждого региона.

Поддерживаются:
  - Объёмы подкорковых структур (37 регионов, aseg-совместимые имена)
  - Толщина коры (151 регион Destrieux → 34 региона Desikan-Killiany
    через композитное объединение gyrus + sulcus + transitional)
  - Площадь поверхности (зарезервировано)

Подход к маппингу толщины:
  Каждый DK-регион состоит из нескольких Destrieux-компонентов:
  - Gyrus (G_*): gyral crown, толще
  - Sulcus (S_*): sulcal fundus, тоньше
  - Gyrus&Sulcus (G&S_*): переходная зона
  Pooled mean: среднее компонентов.
  Pooled std: sqrt(avg(variance_per_component) + variance_of_component_means).
  Это корректно учитывает, что пациент получает одно число
  (средневзвешенное по площади), а норма строится из всех
  анатомических субкомпонентов.
"""

import os
import pickle
import warnings
from pathlib import Path
from typing import Optional, Dict, Tuple

import numpy as np
import pandas as pd


# === Путь к данным ===

def _get_brainchart_dir() -> Path:
    """Возвращает путь к папке brainchart_norms/ относительно корня проекта."""
    return Path(__file__).resolve().parent / "brainchart_norms"


def _get_models_dir() -> Path:
    """
    Возвращает путь к самой полной версии BrainChart-моделей.
    Приоритет: lifespan_57K_82sites > lifespan_29K_82sites_train > ...
    """
    bc_dir = _get_brainchart_dir()
    candidates = [
        "lifespan_57K_82sites/lifespan_57K_82sites",
        "lifespan_29K_82sites_train/lifespan_29K_82sites_train",
        "lifespan_23K_57sites_mqc2/lifespan_23K_57sites_mqc2",
        "lifespan_12K_59sites_mqc_train/lifespan_12K_59sites_mqc_train",
        "lifespan_12K_57sites_mqc2_train/lifespan_12K_57sites_mqc2_train",
    ]
    for c in candidates:
        full = bc_dir / c
        if full.exists() and full.is_dir():
            return full
    return bc_dir  # fallback


def _read_meta_data(region_dir: Path) -> Optional[dict]:
    """
    Читает meta_data.md (pickle) из папки региона.
    Возвращает словарь с ключами mean_resp, std_resp, valid_voxels и др.
    """
    md_path = region_dir / "Models" / "meta_data.md"
    if not md_path.exists():
        return None
    try:
        with open(md_path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def _get_destrieux_norm(models_dir: Path, hemi: str,
                         destrieux_name: str) -> Optional[Tuple[float, float]]:
    """Возвращает (mean, std) для одного Destrieux-региона."""
    full_name = f"{hemi}_{destrieux_name}_thickness"
    region_dir = models_dir / full_name
    meta = _read_meta_data(region_dir)
    if meta is None:
        return None
    try:
        return (float(meta["mean_resp"][0][0]), float(meta["std_resp"][0][0]))
    except (KeyError, IndexError, TypeError):
        return None


# === Загрузка норм объёмов ===

def load_volumes_norms() -> Optional[pd.DataFrame]:
    """
    Загружает нормы объёмов подкорковых структур из Brain Charts.

    Returns:
        DataFrame с колонками: region, mean, std
        или None если данные не найдены.
    """
    models_dir = _get_models_dir()
    if not models_dir.exists():
        warnings.warn(f"BrainChart models directory not found: {models_dir}")
        return None

    rows = []
    for entry in sorted(os.listdir(str(models_dir))):
        region_path = models_dir / entry
        if not region_path.is_dir():
            continue
        # Пропускаем thickness и surfacearea регионы
        name = region_path.name
        if name.endswith("_thickness") or name.endswith("_surfacearea"):
            continue

        meta = _read_meta_data(region_path)
        if meta is None:
            continue

        try:
            mean_val = float(meta["mean_resp"][0][0])
            std_val = float(meta["std_resp"][0][0])
        except (KeyError, IndexError, TypeError):
            continue

        rows.append({
            "region": name,
            "mean": round(mean_val, 2),
            "std": round(std_val, 2),
        })

    if not rows:
        warnings.warn("No volume norms found in BrainChart models")
        return None

    return pd.DataFrame(rows)


# === Загрузка норм толщины (сырые Destrieux) ===

def load_thickness_norms() -> Optional[pd.DataFrame]:
    """
    Загружает сырые нормы толщины коры из Brain Charts (Destrieux atlas).

    Returns:
        DataFrame с колонками: region, mean, std
        или None если данные не найдены.
    """
    models_dir = _get_models_dir()
    if not models_dir.exists():
        warnings.warn(f"BrainChart models directory not found: {models_dir}")
        return None

    rows = []
    for entry in sorted(os.listdir(str(models_dir))):
        region_path = models_dir / entry
        if not region_path.is_dir():
            continue
        name = region_path.name
        if not name.endswith("_thickness"):
            continue

        meta = _read_meta_data(region_path)
        if meta is None:
            continue

        try:
            mean_val = float(meta["mean_resp"][0][0])
            std_val = float(meta["std_resp"][0][0])
        except (KeyError, IndexError, TypeError):
            continue

        rows.append({
            "region": name,
            "mean": round(mean_val, 4),
            "std": round(std_val, 4),
        })

    if not rows:
        warnings.warn("No thickness norms found in BrainChart models")
        return None

    return pd.DataFrame(rows)


def load_surface_norms() -> Optional[pd.DataFrame]:
    """Загружает нормы площади поверхности (зарезервировано)."""
    models_dir = _get_models_dir()
    if not models_dir.exists():
        return None
    rows = []
    for entry in sorted(os.listdir(str(models_dir))):
        region_path = models_dir / entry
        if not region_path.is_dir():
            continue
        if not region_path.name.endswith("_surfacearea"):
            continue
        meta = _read_meta_data(region_path)
        if meta is None:
            continue
        try:
            rows.append({
                "region": region_path.name,
                "mean": round(float(meta["mean_resp"][0][0]), 2),
                "std": round(float(meta["std_resp"][0][0]), 2),
            })
        except (KeyError, IndexError, TypeError):
            continue
    if not rows:
        warnings.warn("No surface area norms found in BrainChart models")
        return None
    return pd.DataFrame(rows)


# ============================================================
# Композитный маппинг: Desikan-Killiany → Destrieux
# ============================================================
# Каждый DK-регион (34 на полушарие) состоит из нескольких
# Destrieux-регионов (74+ на полушарие) — гирус + сулькус + переход.
# Нормы для DK-региона = pooled mean/std по всем его Destrieux-компонентам.
#
# Источник маппинга: FreeSurfer LookupTable + Destrieux et al. (2004).
# Сопоставление через анатомические границы DK-парцелляции.

_DK_COMPOSITE: Dict[str, list] = {
    "bankssts":                    ["S_temporal_sup"],
    "caudalanteriorcingulate":     ["G&S_cingul-Ant"],
    "caudalmiddlefrontal":         ["G_front_middle", "S_front_middle"],
    "cuneus":                      ["G_cuneus", "S_oc_middle&Lunatus",
                                     "S_oc_sup&transversal"],
    "entorhinal":                  ["G_temporal_inf"],
    "frontalpole":                 ["G&S_transv_frontopol", "Pole_occipital",
                                     "S_orbital_lateral"],
    "fusiform":                    ["G_oc-temp_lat-fusifor", "S_oc-temp_lat",
                                     "S_collat_transv_ant", "S_collat_transv_post"],
    "inferiorparietal":            ["G_pariet_inf-Angular", "G_pariet_inf-Supramar",
                                     "S_intrapariet&P_trans"],
    "inferiortemporal":            ["G_temporal_inf", "S_temporal_inf"],
    "insula":                      ["G_insular_short", "G_Ins_lg&S_cent_ins",
                                     "S_circular_insula_ant", "S_circular_insula_inf",
                                     "S_circular_insula_sup"],
    "isthmuscingulate":            ["G_cingul-Post-ventral", "S_cingul-Marginalis"],
    "lateraloccipital":            ["G_occipital_middle", "G_occipital_sup",
                                     "S_occipital_ant"],
    "lateralorbitofrontal":        ["G_front_inf-Orbital", "S_orbital-H_Shaped",
                                     "S_orbital_lateral"],
    "lingual":                     ["G_oc-temp_med-Lingual", "S_oc-temp_med&Lingual",
                                     "S_calcarine"],
    "medialorbitofrontal":         ["G_rectus", "G_subcallosal",
                                     "S_orbital_med-olfact"],
    "middletemporal":              ["G_temporal_middle", "S_temporal_inf"],
    "paracentral":                 ["G&S_paracentral", "S_central"],
    "parahippocampal":             ["G_oc-temp_med-Parahip", "S_collat_transv_ant",
                                     "S_collat_transv_post"],
    "parsopercularis":             ["G_front_inf-Opercular", "S_front_inf"],
    "parsorbitalis":               ["G_front_inf-Orbital", "S_orbital-H_Shaped"],
    "parstriangularis":            ["G_front_inf-Triangul", "S_front_inf"],
    "pericalcarine":               ["S_calcarine", "G_oc-temp_med-Lingual"],
    "postcentral":                 ["G_postcentral", "S_postcentral", "S_central"],
    "posteriorcingulate":          ["G_cingul-Post-dorsal", "S_cingul-Marginalis",
                                     "S_pericallosal"],
    "precentral":                  ["G_precentral", "S_precentral-inf-part",
                                     "S_precentral-sup-part", "S_central"],
    "precuneus":                   ["G_precuneus", "S_subparietal",
                                     "S_parieto_occipital"],
    "rostralanteriorcingulate":    ["G&S_cingul-Mid-Ant", "S_pericallosal"],
    "rostralmiddlefrontal":        ["G_front_middle", "S_front_middle"],
    "superiorfrontal":             ["G_front_sup", "S_front_sup", "S_front_middle"],
    "superiorparietal":            ["G_parietal_sup", "S_intrapariet&P_trans",
                                     "S_subparietal"],
    "superiortemporal":            ["G_temp_sup-Lateral", "G_temp_sup-Plan_tempo",
                                     "G_temp_sup-Plan_polar", "S_temporal_sup",
                                     "S_temporal_transverse"],
    "supramarginal":               ["G_pariet_inf-Supramar", "S_postcentral",
                                     "S_intrapariet&P_trans"],
    "temporalpole":                ["Pole_temporal", "G_temp_sup-Plan_polar"],
    "transversetemporal":          ["G_temp_sup-G_T_transv",
                                     "S_temporal_transverse"],
}

# Кэш композитных норм (заполняется лениво при первом вызове)
_composite_cache: Optional[Dict[str, Tuple[float, float]]] = None


def load_composite_thickness_norms() -> Optional[pd.DataFrame]:
    """
    Загружает композитные нормы толщины: Destrieux → Desikan-Killiany.

    Для каждого DK-региона вычисляет pooled mean и pooled std
    по всем его Destrieux-компонентам (G_* + S_* + G&S_*)
    с обоих полушарий.

    Pooled std = sqrt(avg(var) + var(means)) — корректный учёт
    как внутрикомпонентной, так и межкомпонентной дисперсии.

    Returns:
        DataFrame с колонками: region (DK-имя), mean, std
    """
    global _composite_cache

    models_dir = _get_models_dir()
    if not models_dir.exists():
        warnings.warn(f"BrainChart models directory not found: {models_dir}")
        return None

    if _composite_cache is not None:
        rows = [{"region": dk, "mean": m, "std": s}
                for dk, (m, s) in _composite_cache.items()]
        return pd.DataFrame(rows)

    _composite_cache = {}

    for dk_name, dx_list in sorted(_DK_COMPOSITE.items()):
        all_means = []
        all_stds = []

        for dx in dx_list:
            for hemi in ["lh", "rh"]:
                norm = _get_destrieux_norm(models_dir, hemi, dx)
                if norm is not None:
                    all_means.append(norm[0])
                    all_stds.append(norm[1])

        if not all_means:
            continue

        # Pooled mean: среднее арифметическое компонентов
        pool_mean = float(np.mean(all_means))

        # Pooled std по формуле:
        # σ_pooled = sqrt( avg(σ_i²) + var(μ_i) )
        # где avg(σ_i²) — средняя внутрикомпонентная дисперсия,
        # var(μ_i) — межкомпонентная дисперсия средних
        avg_var = float(np.mean([s ** 2 for s in all_stds]))
        var_of_means = float(np.var(all_means)) if len(all_means) > 1 else 0.0
        pool_std = float(np.sqrt(avg_var + var_of_means))

        _composite_cache[dk_name] = (round(pool_mean, 4), round(pool_std, 4))

    if not _composite_cache:
        warnings.warn("Failed to compute any composite DK norms")
        return None

    rows = [{"region": dk, "mean": m, "std": s}
            for dk, (m, s) in sorted(_composite_cache.items())]
    return pd.DataFrame(rows)


# === Маппинг названий регионов ===

def map_region_name(patient_name: str, source: str = "brainchart") -> Optional[str]:
    """
    Преобразует имя региона FreeSurfer в имя норм Brain Charts.

    - Для aseg (Volume): точное совпадение.
    - Для aparc (Thickness): lh-<dk> или rh-<dk> → <dk> (DK-имя без префикса).

    Args:
        patient_name: 'Left-Hippocampus', 'lh-bankssts', и т.д.
        source: 'brainchart' или 'csv'

    Returns:
        Имя для поиска в таблице норм, или None.
    """
    if source != "brainchart":
        return patient_name

    # Volume: точное совпадение
    if not patient_name.startswith("lh-") and not patient_name.startswith("rh-"):
        return patient_name

    # Thickness: lh-<dk> или rh-<dk> → <dk>
    parts = patient_name.split("-", 1)
    if len(parts) != 2:
        return None

    hemi, dk_name = parts
    if hemi not in ("lh", "rh"):
        return None

    # Проверяем, что DK-имя есть в композитном маппинге
    if dk_name in _DK_COMPOSITE:
        return dk_name

    # Fuzzy fallback
    for dk_key in _DK_COMPOSITE:
        if dk_name.lower() in dk_key or dk_key in dk_name.lower():
            return dk_key

    return None


def get_brainchart_name_variants(patient_name: str) -> list:
    """
    Возвращает список возможных BrainChart-имён для отладки.

    Args:
        patient_name: имя из FreeSurfer

    Returns:
        список строк — вариантов имени
    """
    variants = []
    if patient_name.startswith("lh-") or patient_name.startswith("rh-"):
        hemi, dk = patient_name.split("-", 1)
        if dk in _DK_COMPOSITE:
            variants.append(dk)
            for dx in _DK_COMPOSITE[dk]:
                variants.append(f"{hemi}_{dx}_thickness")
    else:
        variants.append(patient_name)
    return variants
