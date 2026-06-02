"""
Главный файл Streamlit-приложения для QC FreeSurfer — версия 5.
Поддерживает BIDS-вложенность, fallback-имена файлов (T1.mgz, aparc+aseg.mgz),
авто-поиск субъекта в derivatives/freesurfer/subjects/.
"""

import os
import sys
import io
from pathlib import Path

import streamlit as st
import pandas as pd
import numpy as np
import nibabel as nib
import matplotlib.pyplot as plt
import matplotlib
matplotlib.use("Agg")

# Добавляем корень проекта в sys.path для импорта модулей
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from input_handler import (
    check_input_folder,
    resolve_subject_path,
    find_brain_mgz,
    find_aseg_mgz,
    find_aseg_stats_path,
    diagnose_files,
)
from parser_engine import parse_stats
from qc_core import run_qc
from visualizer import (
    generate_slice,
    render_slice_view,
    load_aseg_labels,
    build_aseg_lookup,
    build_reverse_lookup,
    compute_flagged_label_ids,
    get_region_mask,
    get_region_center,
)


# === Константы ===
NORMS_FILE = PROJECT_ROOT / "norms.csv"


# === Инициализация session_state ===
def init_session_state():
    """Создаёт ключи session_state, если их ещё нет."""
    defaults = {
        "qc_done": False,
        "df_result": None,
        "brain_data": None,
        "subject_path": "",
        "resolved_path": "",
        "flagged_regions": [],
        "verdict_counts": {"OK": 0, "Check": 0, "Bad": 0},
        "bad_count": 0,
        "mgz_path": None,
        "slice_visible": False,
        # aseg-подсветка
        "aseg_data": None,
        "flagged_label_ids": None,
        "aseg_available": False,
        # диагностика файлов
        "file_diag": {},
        "resolve_msg": "",
        # навигация по регионам
        "target_region": "",
        # alpha-ползунки
        "alpha_bad": 0.4,
        "alpha_check": 0.35,
        # источник норм
        "norm_source": "csv",
        "source_label": "",
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


init_session_state()


# === Вспомогательные функции ===

@st.cache_data
def load_norms(path: str) -> pd.DataFrame:
    return pd.read_csv(path)


@st.cache_data
def cached_load_brain(mgz_path: str) -> np.ndarray | None:
    """Кэшированная загрузка brain.mgz / T1.mgz (без привязки к session_state)."""
    try:
        img = nib.load(mgz_path)
        data = img.get_fdata()
        return np.asarray(data, dtype=np.float32)
    except Exception:
        return None


def load_brain_to_session(mgz_path: str) -> bool:
    """Загружает brain.mgz и сохраняет в session_state. Возвращает True при успехе."""
    data = cached_load_brain(mgz_path)
    if data is not None:
        st.session_state["brain_data"] = data
        st.session_state["mgz_path"] = mgz_path
        return True
    return False


@st.cache_data
def cached_load_aseg(aseg_path: str):
    """Кэшированная загрузка aseg.mgz через визуализатор."""
    return load_aseg_labels(aseg_path)


# Кэш масок: (aseg_path, label_id, axis, slice_idx) → mask
# Используем хэш от aseg_path (строка) + параметры
_mask_cache: dict = {}


def cached_region_mask(aseg_data, aseg_path: str, label_id: int,
                        axis: str, slice_idx: int):
    """Кэшированный get_region_mask с fallback-ключом по label_id+axis+slice."""
    key = (aseg_path, label_id, axis, slice_idx)
    if key not in _mask_cache:
        _mask_cache[key] = get_region_mask(aseg_data, label_id, axis, slice_idx)
    return _mask_cache[key]


def clear_mask_cache():
    """Очищает кэш масок при смене субъекта."""
    _mask_cache.clear()


# === UI ===
st.set_page_config(
    page_title="QC FreeSurfer",
    page_icon="\U0001f9e0",
    layout="wide",
)

# Тонкая кастомизация стиля
st.markdown("""
<style>
    .stButton > button {
        border-radius: 8px;
        font-weight: 600;
    }
    .stRadio > div[role="radiogroup"] {
        gap: 1.5rem;
    }
    .stCheckbox label {
        font-size: 0.85rem;
    }
    /* Badge-цвета для вердиктов */
    span[data-verdict="Bad"] {
        background: #ff4444;
        color: white;
        padding: 2px 8px;
        border-radius: 4px;
        font-weight: bold;
    }
    span[data-verdict="Check"] {
        background: #ff9800;
        color: white;
        padding: 2px 8px;
        border-radius: 4px;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

st.title("\U0001f9e0 QC FreeSurfer — Контроль качества сегментации")
st.markdown(
    "Прототип автоматического контроля качества результатов "
    "обработки **FreeSurfer**. Сравнение с нормами (Z-score), "
    "вердикт OK / Check / Bad, интерактивный просмотр срезов."
)

st.divider()

# --- Ввод пути ---
col1, col2 = st.columns([3, 1])
with col1:
    subject_path = st.text_input(
        "Путь к папке субъекта (или родительской папке с BIDS)",
        value="./test_data/subject_001",
        placeholder="./test_data/subject_001 или Входные данные",
        help="Допустимые варианты: (1) прямой путь к субъекту со stats/ и mri/; "
             "(2) путь к BIDS-цепочке .../derivatives/freesurfer/subjects/sub-XX/; "
             "(3) родительская папка — субъект будет найден автоматически.",
    )
with col2:
    st.markdown("###")
    run_button = st.button(
        "\U0001f50d Запустить QC", type="primary", use_container_width=True
    )

# --- Выбор источника нормативных данных ---
norm_source = st.radio(
    "Источник нормативных данных:",
    options=["csv", "brainchart"],
    format_func=lambda s: {
        "csv": "CSV (norms.csv) — базовые популяционные нормы",
        "brainchart": "Brain Charts (Nature 2022) — научные нормы",
    }[s],
    horizontal=True,
    key="norm_source",
)

# ================================================================
# ЗАПУСК QC — сохраняет всё в session_state
# ================================================================
if run_button:
    if not subject_path:
        st.error("Введите путь к папке субъекта.")
    else:
        valid = True

        # Шаг 0: Резолвинг пути (BIDS-авто-поиск)
        with st.spinner("Поиск папки субъекта..."):
            resolved, resolve_msg = resolve_subject_path(subject_path)
            if resolved is None:
                st.error(resolve_msg)
                valid = False
            else:
                st.info(f"📁 {resolve_msg}")
                st.session_state["resolved_path"] = resolved
                st.session_state["resolve_msg"] = resolve_msg

        if valid:
            # Шаг 1: Проверка
            with st.spinner("Проверка файлов..."):
                ok, msg = check_input_folder(resolved)
                if not ok:
                    st.error(msg)
                    valid = False

        if valid:
            st.success("Все обязательные файлы найдены.")

            # Диагностика файлов
            file_diag = diagnose_files(resolved)
            st.session_state["file_diag"] = file_diag

            # Шаг 2: Парсинг
            with st.spinner("Парсинг .stats файлов..."):
                try:
                    df = parse_stats(resolved)
                except Exception as e:
                    st.error(f"Ошибка парсинга: {e}")
                    valid = False

        if valid:
            st.info(f"Извлечено записей: {len(df)}")

            # Шаг 3: QC
            with st.spinner("Расчёт Z-score и вердиктов..."):
                try:
                    ns = st.session_state.get("norm_source", "csv")
                    if ns == "brainchart":
                        df_result, source_label = run_qc(
                            df, norm_source=ns,
                        )
                    elif ns == "csv":
                        df_result, source_label = run_qc(
                            df, norm_source=ns,
                            csv_path=str(NORMS_FILE),
                        )
                    else:
                        df_result, source_label = run_qc(
                            df, norm_source=ns,
                            csv_path=str(NORMS_FILE),
                        )
                except Exception as e:
                    st.error(f"Ошибка QC-анализа: {e}")
                    valid = False

        if valid:
            st.success("QC-анализ завершён.")

            # --- Сохраняем ВСЁ в session_state ---
            st.session_state["df_result"] = df_result
            st.session_state["subject_path"] = resolved
            st.session_state["source_label"] = source_label

            # Вердикты
            vc = df_result["Verdict"].value_counts()
            verdict_counts = {
                "OK": vc.get("OK", 0),
                "Check": vc.get("Check", 0),
                "Bad": vc.get("Bad", 0),
            }
            st.session_state["verdict_counts"] = verdict_counts
            st.session_state["bad_count"] = verdict_counts["Bad"]

            # Проблемные регионы (Check + Bad)
            flagged_mask = df_result["Verdict"].isin(["Check", "Bad"])
            flagged_regions = df_result.loc[flagged_mask, "Region"].tolist()
            st.session_state["flagged_regions"] = flagged_regions

            # brain.mgz / T1.mgz
            mgz_path = find_brain_mgz(resolved)
            st.session_state["mgz_path"] = mgz_path
            if mgz_path:
                load_brain_to_session(mgz_path)
            else:
                st.session_state["brain_data"] = None

            # aseg.mgz / aparc+aseg.mgz — заливка регионов
            aseg_mgz_path = find_aseg_mgz(resolved)
            if aseg_mgz_path:
                try:
                    aseg_data = cached_load_aseg(aseg_mgz_path)
                    st.session_state["aseg_data"] = aseg_data

                    aseg_stats = find_aseg_stats_path(resolved)
                    if aseg_stats:
                        lookup = build_aseg_lookup(aseg_stats)
                        reverse_lookup = build_reverse_lookup(lookup)
                        flagged_label_ids = compute_flagged_label_ids(
                            flagged_regions, df_result, reverse_lookup
                        )
                        st.session_state["flagged_label_ids"] = flagged_label_ids
                        st.session_state["aseg_available"] = True
                    else:
                        st.session_state["flagged_label_ids"] = None
                        st.session_state["aseg_available"] = False
                except Exception:
                    st.session_state["aseg_data"] = None
                    st.session_state["flagged_label_ids"] = None
                    st.session_state["aseg_available"] = False
            else:
                st.session_state["aseg_data"] = None
                st.session_state["flagged_label_ids"] = None
                st.session_state["aseg_available"] = False

            st.session_state["qc_done"] = True
            st.session_state["slice_visible"] = False  # сброс при новом QC
            clear_mask_cache()  # очистка кэша масок при новом QC


# ================================================================
# ОТОБРАЖЕНИЕ РЕЗУЛЬТАТОВ (только если qc_done)
# ================================================================
if st.session_state["qc_done"]:
    df_result = st.session_state["df_result"]
    verdict_counts = st.session_state["verdict_counts"]
    flagged_regions = st.session_state["flagged_regions"]
    bad_count = st.session_state["bad_count"]
    mgz_path = st.session_state["mgz_path"]
    brain_data = st.session_state["brain_data"]
    flagged_label_ids = st.session_state["flagged_label_ids"]
    aseg_data = st.session_state["aseg_data"]
    aseg_available = st.session_state["aseg_available"]

    st.divider()

    # === ДВУХКОЛОНОЧНЫЙ ДАШБОРД ===
    col_left, col_right = st.columns([1, 2.5])

    # ================================================================
    # ЛЕВАЯ КОЛОНКА — Проблемные регионы + навигация
    # ================================================================
    with col_left:
        st.subheader("⚠️ Проблемные регионы")

        flagged_df = df_result[df_result["Verdict"].isin(["Check", "Bad"])]

        if flagged_df.empty:
            st.success("Все регионы в норме ✅")
        else:
            st.caption(f"{len(flagged_df)} регионов требуют внимания")

            # Selectbox + кнопка «Перейти»
            region_options = [""] + [
                f"{row['Region']}  [{row['Verdict']}]  Z={row['Zscore']:.2f}"
                for _, row in flagged_df.iterrows()
            ]
            selected = st.selectbox(
                "Выберите регион:",
                options=region_options,
                format_func=lambda x: x if x else "—",
                key="target_region_select",
            )

            go_clicked = st.button(
                "📍 Перейти к региону",
                type="primary",
                use_container_width=True,
                key="go_to_region_btn",
            )

            if go_clicked and selected:
                region_name = selected.split("  [")[0].strip()
                info = (flagged_label_ids or {}).get(region_name, {})
                label_id = info.get("label_id") if info else None

                if label_id and aseg_data is not None:
                    from visualizer import best_slice_for_region
                    best_z = best_slice_for_region(aseg_data, label_id, "axial")
                    st.session_state["slice_axis"] = "axial"
                    st.session_state["slice_idx"] = best_z
                    st.session_state["target_region"] = region_name
                    n_vox = (aseg_data == label_id).sum()
                    st.success(
                        f"Перешли к {region_name} "
                        f"(срез Z={best_z}, {n_vox} vox)"
                    )
                elif aseg_data is None:
                    st.warning("aseg-файл не загружен — навигация недоступна")
                else:
                    st.info(f"Регион '{region_name}' не найден в aseg-таблице")

            # --- Чекбоксы для flagged-регионов ---
            st.divider()
            st.caption("🎯 Показать/скрыть регионы:")
            visible_regions = []
            for _, row in flagged_df.iterrows():
                region = row["Region"]
                v = row["Verdict"]
                z = row["Zscore"]
                label = f"{v}  {region}  (Z={z:.2f})"
                show = st.checkbox(
                    label,
                    value=True,
                    key=f"show_{region}",
                )
                if show:
                    visible_regions.append(region)
            if not visible_regions:
                st.caption("⚠️ Все регионы скрыты — overlay не отображается")

            # Список flagged-регионов с цветовой индикацией
            st.divider()
            st.caption("📊 Сводка:")
            for _, row in flagged_df.iterrows():
                v = row["Verdict"]
                color = "red" if v == "Bad" else "orange"
                z = row["Zscore"]
                st.markdown(
                    f"<span style='color:{color};font-weight:bold'>{v}</span> "
                    f"**{row['Region']}**  "
                    f"<small>Z={z:.2f}</small>",
                    unsafe_allow_html=True,
                )

        # Диагностика файлов (компактно)
        st.divider()
        file_diag = st.session_state.get("file_diag", {})
        if file_diag:
            st.caption("📁 Файлы:")
            st.caption(f"🧠 {file_diag.get('brain_source','?')}")
            st.caption(f"🎨 {file_diag.get('aseg_source','?')}")

    # ================================================================
    # ПРАВАЯ КОЛОНКА — Графики, таблицы, вьювер среза
    # ================================================================
    with col_right:
        # Источник норм
        source_label = st.session_state.get("source_label", "")
        if source_label:
            st.caption(f"📋 Нормы: {source_label}")

        # --- Графики в expander ---
        with st.expander("📊 Графики анализа", expanded=False):
            tab1, tab2 = st.tabs(["Гистограмма Z-score", "Вердикты"])

            with tab1:
                fig1, ax1 = plt.subplots(figsize=(5, 3))
                ax1.hist(df_result["Zscore"], bins=12, color="steelblue",
                         edgecolor="white", alpha=0.85)
                ax1.axvline(x=0, color="gray", linestyle="--", linewidth=1)
                ax1.axvline(x=2, color="orange", linestyle="--",
                            linewidth=1, label="|Z|=2")
                ax1.axvline(x=-2, color="orange", linestyle="--", linewidth=1)
                ax1.axvline(x=3, color="red", linestyle="--",
                            linewidth=1, label="|Z|=3")
                ax1.axvline(x=-3, color="red", linestyle="--", linewidth=1)
                ax1.set_xlabel("Z-score", fontsize=8)
                ax1.set_ylabel("Число регионов", fontsize=8)
                ax1.legend(fontsize=7)
                ax1.set_title("Распределение Z-score", fontsize=9)
                ax1.tick_params(labelsize=7)
                fig1.set_size_inches(5, 3)
                st.pyplot(fig1)

            with tab2:
                fig2, ax2 = plt.subplots(figsize=(4, 2.8))
                bar_colors = {"OK": "green", "Check": "orange", "Bad": "red"}
                order = ["OK", "Check", "Bad"]
                values = [verdict_counts.get(v, 0) for v in order]
                bars = ax2.bar(order, values,
                               color=[bar_colors[v] for v in order])
                ax2.set_xlabel("Вердикт", fontsize=8)
                ax2.set_ylabel("Количество", fontsize=8)
                ax2.set_title("Количество регионов по вердиктам", fontsize=9)
                ax2.tick_params(labelsize=7)
                for bar, count in zip(bars, values):
                    ax2.text(bar.get_x() + bar.get_width() / 2,
                             bar.get_height() + 0.2,
                             str(count), ha="center", fontsize=10)
                fig2.set_size_inches(4, 2.8)
                st.pyplot(fig2)

        # --- Таблицы в expander ---
        with st.expander("📋 Таблицы результатов", expanded=False):
            st.caption("Таблица 1: Результаты QC по регионам")
            display_cols = ["Region", "Type", "Value", "Mean", "Std",
                            "Zscore", "Verdict"]
            st.dataframe(
                df_result[display_cols],
                use_container_width=True,
                hide_index=True,
                height=250,
                column_config={
                    "Zscore": st.column_config.NumberColumn(format="%.2f"),
                    "Value": st.column_config.NumberColumn(format="%.1f"),
                },
            )

            st.caption("Таблица 2: Сводка по вердиктам")
            summary_df = pd.DataFrame({
                "Вердикт": ["OK", "Check", "Bad"],
                "Количество": values,
                "Описание": [
                    "Норма (|Z| < 2)",
                    "Требует проверки (2 <= |Z| < 3)",
                    "Брак (|Z| >= 3)",
                ],
            })
            st.dataframe(summary_df, use_container_width=True, hide_index=True)

            # Кнопка экспорта CSV
            csv_data = df_result.to_csv(index=False).encode("utf-8")
            st.download_button(
                label="📥 Скачать результаты QC (CSV)",
                data=csv_data,
                file_name="qc_results.csv",
                mime="text/csv",
                use_container_width=True,
            )

        # ================================================================
        # ИНТЕРАКТИВНЫЙ ПРОСМОТР СРЕЗОВ
        # ================================================================
        st.subheader("🔍 Просмотр срезов")

        if brain_data is None:
            st.warning(
                "brain.mgz / T1.mgz не найден. "
                "Интерактивный просмотр недоступен."
            )
        else:
            # Компактная строка с информацией
            brain_src = (file_diag or {}).get("brain_source", "?")
            aseg_src = (file_diag or {}).get("aseg_source", "?")
            st.caption(
                f"Загружен: {mgz_path} | {brain_data.shape} | "
                f"🧠 {brain_src} | 🎨 {aseg_src}"
            )

            # Управление: overlay | ось | слайдер — в одной строке
            ctrl1, ctrl2 = st.columns([1, 2])
            with ctrl1:
                show_overlay = st.checkbox(
                    "Показывать overlay",
                    value=True,
                    key="show_overlay_cb",
                )
            with ctrl2:
                axis = st.radio(
                    "Ось среза:",
                    options=["axial", "coronal", "sagittal"],
                    format_func=lambda a: {
                        "axial": "Axial (Z)",
                        "coronal": "Coronal (Y)",
                        "sagittal": "Sagittal (X)",
                    }[a],
                    horizontal=True,
                    key="slice_axis",
                )

            # Alpha-ползунки (только при наличии aseg)
            if aseg_data is not None:
                ac1, ac2 = st.columns(2)
                with ac1:
                    alpha_bad = st.slider(
                        "Alpha Bad",
                        min_value=0.0,
                        max_value=1.0,
                        value=st.session_state.get("alpha_bad", 0.4),
                        step=0.05,
                        key="alpha_bad",
                    )
                with ac2:
                    alpha_check = st.slider(
                        "Alpha Check",
                        min_value=0.0,
                        max_value=1.0,
                        value=st.session_state.get("alpha_check", 0.35),
                        step=0.05,
                        key="alpha_check",
                    )
            else:
                alpha_bad = 0.4
                alpha_check = 0.35

            # Слайдер номера среза
            axis_dim = {"axial": 2, "coronal": 1, "sagittal": 0}[axis]
            max_slice = brain_data.shape[axis_dim] - 1
            default_val = st.session_state.get("slice_idx", max_slice // 2)
            slice_idx = st.slider(
                "Номер среза:",
                min_value=0,
                max_value=max_slice,
                value=min(default_val, max_slice),
                step=1,
                key="slice_idx",
            )

            # Авто-рендер среза (без отдельной кнопки)
            # --- Тестовая зелёная заливка ---
            test_mode = st.checkbox(
                "🧪 Тестовая зелёная заливка (Left-Hippocampus)",
                value=False,
                key="test_mode_cb",
            )
            test_label_id = (17 if test_mode and aseg_data is not None
                             else None)

            # Используем visible_regions из чекбоксов
            active_regions = visible_regions if 'visible_regions' in dir() else flagged_regions

            # Диагностика масок
            with st.expander("🔬 Диагностика", expanded=False):

                # Диагностика масок
                if flagged_label_ids and aseg_data is not None:
                    diag_rows = []
                    for region in flagged_regions:
                        info = flagged_label_ids.get(region, {})
                        lid = info.get("label_id") if info else None
                        ver = info.get("verdict", "?") if info else "?"
                        if lid is not None:
                            mask = get_region_mask(
                                aseg_data, lid, axis, slice_idx
                            )
                            px = mask.sum()
                            vis = ("✅" if px > 0 else
                                   "❌ не виден на этом срезе")
                        else:
                            px = 0
                            vis = "⚠️ нет aseg (aparc)"
                        diag_rows.append({
                            "Регион": region,
                            "SegId": lid if lid else "—",
                            "Verdict": ver,
                            "Px": px,
                            "Статус": vis,
                        })
                    st.dataframe(
                        pd.DataFrame(diag_rows),
                        use_container_width=True,
                        hide_index=True,
                        height=200,
                    )
                elif not aseg_data:
                    st.warning("aseg-файл не загружен — диагностика масок невозможна.")

            # Рендер
            fig = render_slice_view(
                brain_data, axis, slice_idx, active_regions,
                flagged_label_ids=flagged_label_ids,
                aseg_data=aseg_data,
                show_overlay=show_overlay,
                test_label_id=test_label_id,
                alpha_bad=alpha_bad,
                alpha_check=alpha_check,
                alpha_test=0.6,
                mask_cache=_mask_cache,
            )
            fig.set_size_inches(4, 4)
            st.pyplot(fig, use_container_width=False)

            # Кнопка скачивания среза как PNG
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
            buf.seek(0)
            st.download_button(
                label="🖼️ Скачать срез (PNG)",
                data=buf,
                file_name="qc_slice.png",
                mime="image/png",
                use_container_width=True,
            )

            plt.close(fig)

            # Краткая сводка под срезом
            if active_regions:
                st.caption(
                    f"⚠️ {len(active_regions)} проблемных: "
                    f"{', '.join(active_regions[:5])}"
                    + (f" ... +{len(active_regions) - 5}"
                       if len(active_regions) > 5 else "")
                )
            else:
                st.caption("✅ Все регионы OK")

else:
    st.info("👆 Запустите QC — введите путь и нажмите кнопку.")

st.divider()
st.caption(
    "Прототип для задания 5П курса «Мини-ВКР». "
    "Стек: Python, Pandas, NiBabel, Streamlit, Matplotlib."
)
