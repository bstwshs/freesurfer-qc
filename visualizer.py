"""
Модуль визуализации (Visualizer) — версия 6.
CLAHE/процентильная нормализация контраста, регулируемая alpha для overlay,
яркий контур + fallback-рамка.
"""

from pathlib import Path
import nibabel as nib
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
from typing import List, Dict, Tuple, Optional
from functools import lru_cache

OUTPUT_FILENAME = "slice_output.png"

# Цвета по вердикту
VERDICT_COLORS = {
    "Bad": (1.0, 0.0, 0.0),       # красный (255, 0, 0)
    "Check": (1.0, 0.647, 0.0),   # оранжевый (255, 165, 0)
}


# === Нормализация контраста среза ===

def normalize_contrast(slice_2d: np.ndarray) -> np.ndarray:
    """
    Адаптивная нормализация контраста 2D-среза.
    Пробует CLAHE (scikit-image), при отсутствии — процентильная (p2–p98).
    Возвращает float32 массив в диапазоне [0, 1].
    """
    try:
        from skimage.exposure import equalize_adapthist
        vmin, vmax = slice_2d.min(), slice_2d.max()
        if vmax > vmin:
            img = (slice_2d - vmin) / (vmax - vmin)
        else:
            img = slice_2d.copy()
        return np.asarray(equalize_adapthist(img, clip_limit=0.03), dtype=np.float32)
    except ImportError:
        # Процентильная нормализация: отсекаем 2% снизу и сверху
        p2, p98 = np.percentile(slice_2d, (2, 98))
        if p98 > p2:
            img = np.clip(slice_2d, p2, p98)
            img = (img - p2) / (p98 - p2)
        else:
            vmin, vmax = slice_2d.min(), slice_2d.max()
            img = (slice_2d - vmin) / (vmax - vmin) if vmax > vmin else slice_2d.copy()
        return np.asarray(img, dtype=np.float32)


# === Слайс-функции ===

def get_axial_slice(data: np.ndarray, z: int) -> np.ndarray:
    return np.asarray(data[:, :, z], dtype=np.float32)

def get_coronal_slice(data: np.ndarray, y: int) -> np.ndarray:
    return np.asarray(data[:, y, :], dtype=np.float32)

def get_sagittal_slice(data: np.ndarray, x: int) -> np.ndarray:
    return np.asarray(data[x, :, :], dtype=np.float32)


# === Загрузка aseg.mgz ===

def load_aseg_labels(aseg_path: str) -> np.ndarray:
    img = nib.load(aseg_path)
    return np.asarray(img.get_fdata(), dtype=np.int32)


# === Маска региона на срезе ===

def get_region_mask(aseg_data: np.ndarray, label_id: int,
                    axis: str, slice_idx: int) -> np.ndarray:
    """
    Извлекает 2D-маску региона для заданной оси и номера среза.
    Возвращает булевый массив (True где регион присутствует).
    """
    if axis == "axial":
        aseg_slice = aseg_data[:, :, slice_idx]
    elif axis == "coronal":
        aseg_slice = aseg_data[:, slice_idx, :]
    else:  # sagittal
        aseg_slice = aseg_data[slice_idx, :, :]
    return aseg_slice == label_id


def best_slice_for_region(aseg_data: np.ndarray, label_id: int,
                           axis: str = "axial") -> int:
    """
    Находит индекс среза с максимальной площадью маски региона.
    Перебирает все срезы по указанной оси и возвращает индекс
    с наибольшим количеством вокселей.

    Args:
        aseg_data: 3D-массив меток
        label_id: идентификатор региона
        axis: 'axial', 'coronal', или 'sagittal'

    Returns:
        индекс среза (int)
    """
    if axis == "axial":
        n_slices = aseg_data.shape[2]
        counts = [(aseg_data[:, :, z] == label_id).sum() for z in range(n_slices)]
    elif axis == "coronal":
        n_slices = aseg_data.shape[1]
        counts = [(aseg_data[:, y, :] == label_id).sum() for y in range(n_slices)]
    else:
        n_slices = aseg_data.shape[0]
        counts = [(aseg_data[x, :, :] == label_id).sum() for x in range(n_slices)]

    best = int(np.argmax(counts))
    # Если маска пуста на всех срезах — возвращаем центр объёма
    if counts[best] == 0:
        return n_slices // 2
    return best


def get_region_center(aseg_data: np.ndarray, label_id: int
                      ) -> Tuple[int, int, int]:
    """
    Вычисляет центр масс региона в 3D-пространстве aseg.
    Возвращает (x, y, z) — целочисленные координаты центра.

    Для aseg-лейблов (2-85): центр масс маски.
    Для DK-лейблов (1000+): анатомический центр из таблицы
    (aparc+aseg.mgz нужен для точной маски, но маркер работает и без неё).
    """
    mask_3d = (aseg_data == label_id)
    if mask_3d.any():
        coords = np.argwhere(mask_3d)
        cx = int(np.mean(coords[:, 0]))
        cy = int(np.mean(coords[:, 1]))
        cz = int(np.mean(coords[:, 2]))
        return cx, cy, cz

    # DK cortical region — use anatomical center lookup table
    # (approximate, based on MNI305 space mapped to 256^3 voxel grid)
    DK_CENTERS = {
        1001: (155, 80, 130), 1002: (128, 102, 135), 1003: (148, 78, 155),
        1005: (142, 50, 110), 1006: (145, 60, 82), 1007: (160, 55, 95),
        1008: (155, 68, 120), 1009: (160, 68, 90), 1010: (135, 75, 115),
        1011: (160, 60, 105), 1012: (140, 80, 140), 1013: (145, 55, 105),
        1014: (125, 82, 138), 1015: (162, 72, 100), 1016: (148, 62, 90),
        1017: (122, 78, 145), 1018: (130, 82, 148), 1019: (128, 84, 142),
        1020: (130, 80, 145), 1021: (138, 50, 108), 1022: (130, 70, 135),
        1023: (128, 80, 125), 1024: (128, 76, 148), 1025: (135, 63, 125),
        1026: (128, 98, 138), 1027: (135, 80, 155), 1028: (130, 74, 155),
        1029: (140, 65, 128), 1030: (158, 78, 115), 1031: (148, 75, 125),
        1032: (125, 92, 160), 1033: (156, 78, 82), 1034: (148, 80, 110),
        1035: (145, 85, 130),
        # Right (mirror X: 256-x)
        2001: (101, 80, 130), 2002: (128, 102, 135), 2003: (108, 78, 155),
        2005: (114, 50, 110), 2006: (111, 60, 82), 2007: (96, 55, 95),
        2008: (101, 68, 120), 2009: (96, 68, 90), 2010: (121, 75, 115),
        2011: (96, 60, 105), 2012: (116, 80, 140), 2013: (111, 55, 105),
        2014: (131, 82, 138), 2015: (94, 72, 100), 2016: (108, 62, 90),
        2017: (134, 78, 145), 2018: (126, 82, 148), 2019: (128, 84, 142),
        2020: (126, 80, 145), 2021: (118, 50, 108), 2022: (126, 70, 135),
        2023: (128, 80, 125), 2024: (128, 76, 148), 2025: (121, 63, 125),
        2026: (128, 98, 138), 2027: (121, 80, 155), 2028: (126, 74, 155),
        2029: (116, 65, 128), 2030: (98, 78, 115), 2031: (108, 75, 125),
        2032: (131, 92, 160), 2033: (100, 78, 82), 2034: (108, 80, 110),
        2035: (111, 85, 130),
    }

    cx, cy, cz = DK_CENTERS.get(label_id, (
        aseg_data.shape[0] // 2,
        aseg_data.shape[1] // 2,
        aseg_data.shape[2] // 2,
    ))
    return cx, cy, cz


# === Overlay: заливка региона цветом ===

def build_overlay_rgba(mask: np.ndarray, color: Tuple[float, float, float],
                       alpha: float) -> np.ndarray:
    """
    Строит RGBA-изображение для наложения на срез.
    mask: булевый 2D-массив (True = заливка).
    color: (R, G, B) в диапазоне 0..1.
    alpha: прозрачность 0..1.
    Возвращает (H, W, 4) массив float32.
    """
    overlay = np.zeros((mask.shape[0], mask.shape[1], 4), dtype=np.float32)
    overlay[mask, 0] = color[0]
    overlay[mask, 1] = color[1]
    overlay[mask, 2] = color[2]
    overlay[mask, 3] = alpha
    return overlay


def build_contour_rgba(mask: np.ndarray, color: Tuple[float, float, float],
                       alpha: float = 1.0) -> np.ndarray:
    """
    Строит RGBA-изображение контура региона (яркая граница).
    Контур = пиксели маски, у которых хотя бы один 4-сосед вне маски.
    Чистый numpy, без scipy.
    """
    # Сдвиги для 4-связности
    eroded = np.ones_like(mask, dtype=bool)
    # Проверяем всех 4 соседей: если любой из них False — пиксель на границе
    # eroded = все 4 соседа внутри маски
    eroded[1:, :] &= mask[:-1, :]   # верхний сосед
    eroded[:-1, :] &= mask[1:, :]   # нижний
    eroded[:, 1:] &= mask[:, :-1]   # левый
    eroded[:, :-1] &= mask[:, 1:]   # правый
    eroded &= mask                   # сам пиксель должен быть в маске
    contour = mask & ~eroded

    overlay = np.zeros((mask.shape[0], mask.shape[1], 4), dtype=np.float32)
    overlay[contour, 0] = color[0]
    overlay[contour, 1] = color[1]
    overlay[contour, 2] = color[2]
    overlay[contour, 3] = alpha
    return overlay


# === Построение lookup-таблицы из aseg.stats ===

def build_aseg_lookup(aseg_stats_path: str) -> Dict[int, str]:
    lookup = {}
    with open(aseg_stats_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            try:
                seg_id = int(parts[1])
                if len(parts) > 10:
                    name_end = 5
                    for j in range(5, len(parts)):
                        try:
                            float(parts[j])
                            break
                        except ValueError:
                            name_end = j + 1
                    name = " ".join(parts[4:name_end])
                else:
                    name = parts[4]
                lookup[seg_id] = name
            except (ValueError, IndexError):
                continue
    return lookup


def build_reverse_lookup(lookup: Dict[int, str]) -> Dict[str, int]:
    return {name: seg_id for seg_id, name in lookup.items()}


def compute_flagged_label_ids(
    flagged_regions: List[str],
    df_result: "pd.DataFrame",
    reverse_lookup: Dict[str, int],
) -> Dict[str, dict]:
    """
    Для flagged-регионов строит словарь:
    {region_name: {'label_id': int или None, 'verdict': str}}
    label_id — из reverse_lookup (aseg) или из DK-таблицы (aparc).

    DK label IDs: 1001-1035 (lh), 2001-2035 (rh) — стандартные коды
    FreeSurferColorLUT для Desikan-Killiany-атласа.
    """
    # Desikan-Killiany cortical label IDs (from FreeSurferColorLUT.txt)
    DK_LABEL_IDS = {
        # Left hemisphere (1000+)
        'lh-bankssts': 1001, 'lh-caudalanteriorcingulate': 1002,
        'lh-caudalmiddlefrontal': 1003, 'lh-cuneus': 1005,
        'lh-entorhinal': 1006, 'lh-fusiform': 1007,
        'lh-inferiorparietal': 1008, 'lh-inferiortemporal': 1009,
        'lh-isthmuscingulate': 1010, 'lh-lateraloccipital': 1011,
        'lh-lateralorbitofrontal': 1012, 'lh-lingual': 1013,
        'lh-medialorbitofrontal': 1014, 'lh-middletemporal': 1015,
        'lh-parahippocampal': 1016, 'lh-paracentral': 1017,
        'lh-parsopercularis': 1018, 'lh-parsorbitalis': 1019,
        'lh-parstriangularis': 1020, 'lh-pericalcarine': 1021,
        'lh-postcentral': 1022, 'lh-posteriorcingulate': 1023,
        'lh-precentral': 1024, 'lh-precuneus': 1025,
        'lh-rostralanteriorcingulate': 1026, 'lh-rostralmiddlefrontal': 1027,
        'lh-superiorfrontal': 1028, 'lh-superiorparietal': 1029,
        'lh-superiortemporal': 1030, 'lh-supramarginal': 1031,
        'lh-frontalpole': 1032, 'lh-temporalpole': 1033,
        'lh-transversetemporal': 1034, 'lh-insula': 1035,
        # Right hemisphere (2000+)
        'rh-bankssts': 2001, 'rh-caudalanteriorcingulate': 2002,
        'rh-caudalmiddlefrontal': 2003, 'rh-cuneus': 2005,
        'rh-entorhinal': 2006, 'rh-fusiform': 2007,
        'rh-inferiorparietal': 2008, 'rh-inferiortemporal': 2009,
        'rh-isthmuscingulate': 2010, 'rh-lateraloccipital': 2011,
        'rh-lateralorbitofrontal': 2012, 'rh-lingual': 2013,
        'rh-medialorbitofrontal': 2014, 'rh-middletemporal': 2015,
        'rh-parahippocampal': 2016, 'rh-paracentral': 2017,
        'rh-parsopercularis': 2018, 'rh-parsorbitalis': 2019,
        'rh-parstriangularis': 2020, 'rh-pericalcarine': 2021,
        'rh-postcentral': 2022, 'rh-posteriorcingulate': 2023,
        'rh-precentral': 2024, 'rh-precuneus': 2025,
        'rh-rostralanteriorcingulate': 2026, 'rh-rostralmiddlefrontal': 2027,
        'rh-superiorfrontal': 2028, 'rh-superiorparietal': 2029,
        'rh-superiortemporal': 2030, 'rh-supramarginal': 2031,
        'rh-frontalpole': 2032, 'rh-temporalpole': 2033,
        'rh-transversetemporal': 2034, 'rh-insula': 2035,
    }

    result = {}
    for region in flagged_regions:
        row = df_result[df_result["Region"] == region]
        verdict = row["Verdict"].values[0] if len(row) > 0 else "Check"

        # Сначала ищем в aseg reverse_lookup (подкорковые)
        seg_id = reverse_lookup.get(region)
        if seg_id is not None:
            result[region] = {"label_id": seg_id, "verdict": verdict}
            continue

        # Затем в DK-таблице (кортикальные)
        dk_id = DK_LABEL_IDS.get(region)
        if dk_id is not None:
            result[region] = {"label_id": dk_id, "verdict": verdict}
            continue

        # Не найдено — fallback-рамка
        result[region] = {"label_id": None, "verdict": verdict}

    return result


# === Fallback-рамка (aparc-регионы без aseg) ===

def add_fallback_highlight(ax: plt.Axes, slice_shape: tuple,
                            region_names: List[str]) -> None:
    if not region_names:
        return
    h, w = slice_shape
    cx, cy = w / 2, h / 2
    rect = patches.Rectangle(
        (cx - 10, cy - 10), 20, 20,
        linewidth=4, edgecolor="red", facecolor="none",
        linestyle="--",
    )
    ax.add_patch(rect)
    label = "No aseg:\n" + "\n".join(region_names[:4])
    if len(region_names) > 4:
        label += f"\n... +{len(region_names) - 4}"
    ax.text(cx, cy - 15, label, color="red", fontsize=8,
            ha="center", va="top",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                      edgecolor="red", alpha=0.9))


# === Главная функция отрисовки ===

def render_slice_view(
    data: np.ndarray,
    axis: str,
    slice_idx: int,
    region_names: List[str],
    flagged_label_ids: Optional[Dict[str, dict]] = None,
    aseg_data: Optional[np.ndarray] = None,
    show_overlay: bool = True,
    test_label_id: Optional[int] = None,
    alpha_bad: float = 0.4,
    alpha_check: float = 0.35,
    alpha_test: float = 0.4,
    mask_cache: Optional[dict] = None,
) -> plt.Figure:
    """
    Генерирует matplotlib-фигуру с выбранным срезом и подсветкой.

    flagged_label_ids: {region: {'label_id': int|None, 'verdict': str}}
    aseg_data: 3D-массив меток (для масок).
    show_overlay: если False — показываем чистый срез.
    test_label_id: если задан — рисует ярко-зелёную заливку для этого SegId
                   (для демонстрации работы overlay).
    alpha_bad: прозрачность для Bad (по умолчанию 0.4).
    alpha_check: прозрачность для Check (по умолчанию 0.35).
    alpha_test: прозрачность тестовой заливки (по умолчанию 0.4).
    mask_cache: опциональный dict для кэширования get_region_mask.
    """
    # Срез мозга
    if axis == "axial":
        slice_2d = get_axial_slice(data, slice_idx)
        axis_label = "Z"
    elif axis == "coronal":
        slice_2d = get_coronal_slice(data, slice_idx)
        axis_label = "Y"
    else:
        slice_2d = get_sagittal_slice(data, slice_idx)
        axis_label = "X"

    # Нормализация контраста (CLAHE или процентильная)
    slice_2d_norm = normalize_contrast(slice_2d)

    fig, ax = plt.subplots(figsize=(4, 4))
    ax.imshow(slice_2d_norm.T, cmap="gray", origin="lower")

    # --- Подсветка ---
    regions_with_mask = []     # есть label_id (aseg или DK)
    regions_without_mask = []  # нет label_id

    if flagged_label_ids:
        for region in region_names:
            info = flagged_label_ids.get(region)
            if info and info["label_id"] is not None:
                regions_with_mask.append(region)
            else:
                regions_without_mask.append(region)

        # Заливка регионов с известными label_id
        if show_overlay and aseg_data is not None:
            visible_count = 0
            aseg_count = 0
            dk_count = 0
            for region in regions_with_mask:
                info = flagged_label_ids[region]
                label_id = info["label_id"]
                verdict = info.get("verdict", "Check")
                color = VERDICT_COLORS.get(verdict, (1.0, 0.0, 0.0))
                alpha = alpha_check if verdict == "Check" else alpha_bad

                # Кэшированная маска
                if mask_cache is not None:
                    key = (label_id, axis, slice_idx)
                    if key not in mask_cache:
                        mask_cache[key] = get_region_mask(aseg_data, label_id, axis, slice_idx)
                    mask = mask_cache[key]
                else:
                    mask = get_region_mask(aseg_data, label_id, axis, slice_idx)
                if mask.any():
                    overlay = build_overlay_rgba(mask, color, alpha)
                    ax.imshow(
                        overlay.transpose(1, 0, 2),
                        origin="lower",
                        interpolation="nearest",
                    )
                    contour = build_contour_rgba(mask, color, alpha=1.0)
                    ax.imshow(
                        contour.transpose(1, 0, 2),
                        origin="lower",
                        interpolation="nearest",
                    )
                    visible_count += 1
                    if label_id < 1000:
                        aseg_count += 1
                    else:
                        dk_count += 1
                else:
                    # Маркер в центре региона (если маска не видна на этом срезе)
                    cx, cy, cz = get_region_center(aseg_data, label_id)
                    if axis == "axial":
                        px, py = cx, cy
                    elif axis == "coronal":
                        px, py = cx, cz
                    else:
                        px, py = cy, cz

                    if 0 <= px < mask.shape[0] and 0 <= py < mask.shape[1]:
                        circle = patches.Circle(
                            (px, py), radius=6,
                            edgecolor=color, facecolor=color,
                            linewidth=3, alpha=min(alpha + 0.3, 1.0),
                        )
                        ax.add_patch(circle)
                        ax.annotate(
                            region.replace('lh-', 'L-').replace('rh-', 'R-'),
                            (px, py),
                            textcoords="offset points",
                            xytext=(10, 10),
                            fontsize=7, color=color,
                            bbox=dict(boxstyle="round,pad=0.2",
                                      facecolor="black", edgecolor=color, alpha=0.85),
                        )
                    visible_count += 1
                    if label_id < 1000:
                        aseg_count += 1
                    else:
                        dk_count += 1

            # --- Тестовая зелёная заливка ---
            if test_label_id is not None:
                test_mask = get_region_mask(aseg_data, test_label_id, axis, slice_idx)
                GREEN = (0.0, 1.0, 0.0)
                if test_mask.any():
                    test_overlay = build_overlay_rgba(test_mask, GREEN, alpha_test)
                    ax.imshow(
                        test_overlay.transpose(1, 0, 2),
                        origin="lower",
                        interpolation="nearest",
                    )
                    test_contour = build_contour_rgba(test_mask, GREEN, alpha=1.0)
                    ax.imshow(
                        test_contour.transpose(1, 0, 2),
                        origin="lower",
                        interpolation="nearest",
                    )
                    ax.text(
                        0.02, 0.90,
                        "TEST: SegId=%d (%d px)" % (test_label_id, test_mask.sum()),
                        transform=ax.transAxes,
                        color="lime", fontsize=9, va="top",
                        bbox=dict(boxstyle="round,pad=0.3", facecolor="black",
                                  edgecolor="lime", alpha=0.9),
                    )
                else:
                    ax.text(
                        0.02, 0.90,
                        "TEST: SegId=%d — не виден" % test_label_id,
                        transform=ax.transAxes,
                        color="yellow", fontsize=8, va="top",
                        bbox=dict(boxstyle="round,pad=0.3", facecolor="black",
                                  edgecolor="yellow", alpha=0.8),
                    )

            # Инфо о видимых регионах
            if visible_count > 0:
                ax.text(
                    0.02, 0.98,
                    "Overlay: %d region(s) [%d aseg, %d DK]" % (
                        visible_count, aseg_count, dk_count),
                    transform=ax.transAxes,
                    color="white", fontsize=8, va="top",
                    bbox=dict(boxstyle="round,pad=0.3", facecolor="black",
                              edgecolor="red", alpha=0.8),
                )

        # Fallback для регионов без масок
        if regions_without_mask:
            add_fallback_highlight(ax, slice_2d_norm.shape, regions_without_mask)

    elif region_names:
        # Нет aseg-данных — только fallback
        add_fallback_highlight(ax, slice_2d_norm.shape, region_names)

    # Подпись
    title = f"{axis.capitalize()} slice ({axis_label}={slice_idx})"
    if not show_overlay:
        title += " [overlay off]"
    ax.set_title(title)

    ax.axis("off")
    fig.tight_layout()
    return fig


# === Старая функция (обратная совместимость) ===

def generate_slice(brain_mgz_path: str, region_name: str,
                   output_dir: str = ".") -> str:
    mgz_file = Path(brain_mgz_path)
    if not mgz_file.exists():
        alt = mgz_file.parent / "mri" / mgz_file.name
        if alt.exists():
            mgz_file = alt
        else:
            raise FileNotFoundError(f"Файл не найден: {mgz_file.resolve()}")
    img = nib.load(str(mgz_file))
    data = img.get_fdata()
    data = np.asarray(data, dtype=np.float32)
    slice_idx = data.shape[2] // 2
    fig = render_slice_view(data, "axial", slice_idx, [region_name])
    output_path = Path(output_dir) / OUTPUT_FILENAME
    fig.savefig(str(output_path), dpi=100, bbox_inches="tight")
    plt.close(fig)
    return str(output_path.resolve())
