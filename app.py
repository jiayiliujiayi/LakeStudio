import os
from pathlib import Path
import re
import sys
import numpy as np
import pandas as pd
import plotly.express as px
from st_aggrid import AgGrid, GridOptionsBuilder, DataReturnMode
import streamlit as st

# Ensure project root is in python path
project_root = str(Path(__file__).resolve().parent)
if project_root not in sys.path:
    sys.path.append(project_root)

from LakeWidgets.veeva_column_profiler import VeevaColumnProfiler
from LakeWidgets.veeva_lake_previewer import VeevaLakePreviewer
from LakeWidgets.veeva_study_aggregator import VeevaStudyAggregator
from LakeWidgets.veeva_study_arm_product_matcher import VeevaStudyArmProductMatcher
from LakeWidgets.veeva_subject_profiler import VeevaSubjectProfiler
from LakeWidgets.veeva_table_column_matcher import VeevaTableColumnMatcher

st.set_page_config(page_title="LakeStudio Data Hub", layout="wide", page_icon="🧰")

st.title("🧰 LakeStudio - Interactive Data Engineering Portal")

# CSS Injection for AgGrid Cell Grid Lines & Text Selection Highlight
st.markdown(
    """
    <style>
    .ag-theme-alpine .ag-cell {
        border-right: 1px solid #d9d9d9 !important;
        user-select: text !important;
        -webkit-user-select: text !important;
    }
    .ag-theme-alpine .ag-header-cell {
        border-right: 1px solid #bfbfbf !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Sidebar - User Identity & Tool Selection
st.sidebar.header("User Settings")
user_id = st.sidebar.text_input("Corporate User ID", value="bp_jiayi_liu@colpal.com")

st.sidebar.header("Select Engine")
selected_tool = st.sidebar.radio(
    "Available Widgets:",
    [
        "Lake Veeva Previewer",
        "Veeva Study Aggregator",
        "Veeva Study Arm Product Matcher",
        "Veeva Subject Profiler",
        "Veeva Column Profiler",
        "Veeva Table Column Matcher",
    ],
)


@st.cache_data
def convert_df_to_csv(df: pd.DataFrame) -> bytes:
    """Helper function to prepare downloadable CSVs."""
    return df.to_csv(index=False).encode("utf-8")


def clean_dropdown_options(series: pd.Series) -> list:
    """Sanitizes categorical column values for user-friendly dropdown lists."""
    cleaned_vals = set()
    for val in series.dropna().unique():
        s_val = str(val).strip()
        if s_val.lower() in ["", "none", "nan", "null", "[null / unassigned]"]:
            continue
        s_val = re.sub(r"(__c|__v|_clin)$", "", s_val)
        cleaned_vals.add(s_val)
    return sorted(list(cleaned_vals), key=lambda x: x.lower())


# -----------------------------------------------------------------------------
# 1. LAKE VEEVA PREVIEWER
# -----------------------------------------------------------------------------
if selected_tool == "Lake Veeva Previewer":
    st.subheader("🗂️ Lake Veeva Table Previewer")
    st.caption("Target Schema: `PROD_GTED_HUB.CUR_CLIN_VEEVA_CTMS`")

    previewer = VeevaLakePreviewer(user_id=user_id)

    # Dynamic Schema Refresh Action
    col_title, col_ref = st.columns([4, 1])
    with col_ref:
        if st.button("🔄 Refresh Tables", use_container_width=True):
            if "available_tables" in st.session_state:
                del st.session_state["available_tables"]

    # Dynamic schema query with session caching
    if "available_tables" not in st.session_state:
        try:
            with st.spinner("Fetching available tables from Snowflake INFORMATION_SCHEMA..."):
                previewer.connect()
                st.session_state["available_tables"] = previewer.fetch_available_tables()
        except Exception as e:
            st.error(f"❌ Failed to fetch tables: {str(e)}")
            st.session_state["available_tables"] = []

    available_tables = st.session_state.get("available_tables", [])
    total_tables = len(available_tables)

    # Top Summary Banner: Available Table Count and Names
    st.markdown("---")
    top_m1, top_m2 = st.columns([1, 3])
    with top_m1:
        st.metric("Total Discovered Tables", f"{total_tables:,}")
    with top_m2:
        with st.expander(f"📋 View All Available Table Names ({total_tables} total)", expanded=False):
            st.write("Dynamic table list loaded from schema:")
            st.code(", ".join(available_tables), language="text")

    st.markdown("---")

    # Table Selection and Configuration Controls
    if available_tables:
        col_tbl, col_cfg = st.columns([2, 2])

        with col_tbl:
            selected_table = st.selectbox(
                "Select Target Table to Preview:",
                options=available_tables,
                key="preview_selected_table",
            )

        with col_cfg:
            display_mode = st.radio("Display Mode:", ["Limit Rows", "Display All Rows"], horizontal=True)

            if display_mode == "Limit Rows":
                row_limit = st.number_input(
                    "Set Row Limit:",
                    min_value=1,
                    value=100,
                    step=10,
                    help="Default is 100 rows. Specify custom limit as needed.",
                )
            else:
                row_limit = None

        if st.button("🔍 Load Table Preview", type="primary"):
            with st.spinner(f"Querying dynamic data for `{selected_table}`..."):
                try:
                    previewer.connect()
                    # Query full table to measure actual size & enable full CSV download
                    df_full = previewer.fetch_table_preview(selected_table, limit=None)
                    st.session_state["preview_df_full"] = df_full
                    st.session_state["preview_table_name"] = selected_table

                    total_actual_rows = len(df_full)

                    # Row limit validation vs total available rows
                    if row_limit is not None and row_limit > total_actual_rows:
                        st.warning(
                            f"⚠️ Requested limit ({row_limit:,}) exceeds actual row count "
                            f"({total_actual_rows:,}). Displaying all {total_actual_rows:,} rows."
                        )
                        df_displayed = df_full.copy()
                    elif row_limit is not None:
                        df_displayed = df_full.head(row_limit).copy()
                    else:
                        df_displayed = df_full.copy()

                    st.session_state["preview_df_displayed"] = df_displayed

                except Exception as e:
                    st.error(f"❌ Failed to load table content: {str(e)}")

        # Data Preview Display Section
        if "preview_df_displayed" in st.session_state and st.session_state.get("preview_table_name") == selected_table:
            df_full = st.session_state["preview_df_full"]
            df_displayed = st.session_state["preview_df_displayed"]

            num_cols = len(df_displayed.columns)
            displayed_rows = len(df_displayed)
            total_rows = len(df_full)

            # Metadata Metrics Callout (Top)
            m_col1, m_col2, m_col3 = st.columns(3)
            m_col1.metric("Total Columns", f"{num_cols:,}")
            m_col2.metric("Displayed Rows", f"{displayed_rows:,}")
            m_col3.metric("Total Available Rows", f"{total_rows:,}")

            # Column Names Listing
            with st.expander("📌 View Active Column Names List", expanded=False):
                st.code(", ".join(df_displayed.columns), language="text")

            # Download Options
            dl_col1, dl_col2 = st.columns(2)
            with dl_col1:
                st.download_button(
                    label=f"💾 Download Displayed Rows ({displayed_rows:,})",
                    data=convert_df_to_csv(df_displayed),
                    file_name=f"{selected_table}_displayed_{displayed_rows}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
            with dl_col2:
                st.download_button(
                    label=f"📦 Download Full Dataset ({total_rows:,} Rows)",
                    data=convert_df_to_csv(df_full),
                    file_name=f"{selected_table}_full_{total_rows}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

            # Interactive Grid View
            st.write("### 📊 Interactive Data Grid")
            gb = GridOptionsBuilder.from_dataframe(df_displayed)
            gb.configure_pagination(paginationAutoPageSize=False, paginationPageSize=15)
            gb.configure_side_bar()
            gb.configure_default_column(filterable=True, sortable=True, resizable=True, minWidth=150)

            gridOptions = gb.build()
            gridOptions["enableCellTextSelection"] = True
            gridOptions["ensureDomOrder"] = True

            AgGrid(
                df_displayed,
                gridOptions=gridOptions,
                data_return_mode=DataReturnMode.FILTERED_AND_SORTED,
                enable_enterprise_modules=False,
                height=450,
                theme="alpine",
                width="100%",
            )

            # Metadata Summary Box (Bottom)
            st.info(
                f"**Table Summary:** `{selected_table}` | **Columns:** {num_cols:,} | "
                f"**Showing:** {displayed_rows:,} of {total_rows:,} rows"
            )

    else:
        st.info("No base tables found in schema `PROD_GTED_HUB.CUR_CLIN_VEEVA_CTMS`.")

# -----------------------------------------------------------------------------
# 2. VEEVA STUDY AGGREGATOR
# -----------------------------------------------------------------------------
elif selected_tool == "Veeva Study Aggregator":
    st.subheader("📊 Unified Clinical Study Aggregator")

    if st.button("Run Master Aggregation Pipeline"):
        with st.spinner("Connecting to Snowflake and compiling study metrics..."):
            aggregator = VeevaStudyAggregator(user_id=user_id)
            aggregator.connect()

            df_type, df_subtype, df_pivot = aggregator.compile_metrics_dashboard()
            df_master = aggregator.build_unified_master_report()

            st.session_state["df_master"] = df_master
            st.session_state["df_pivot"] = df_pivot
            st.session_state["df_type"] = df_type
            st.session_state["df_subtype"] = df_subtype

    if "df_master" in st.session_state:
        df_master = st.session_state["df_master"]

        st.download_button(
            label="💾 Download Master Report (CSV)",
            data=convert_df_to_csv(df_master),
            file_name="veeva_master_study_report.csv",
            mime="text/csv",
        )

        tab1, tab2, tab3 = st.tabs(
            ["📋 Master Study Report", "🧩 Pivot Matrix", "🔬 Type Summaries"]
        )

        with tab1:
            st.dataframe(df_master, use_container_width=True)
        with tab2:
            st.dataframe(st.session_state["df_pivot"], use_container_width=True)
        with tab3:
            col1, col2 = st.columns(2)
            with col1:
                st.write("**Study Type Summary**")
                st.dataframe(st.session_state["df_type"], use_container_width=True)
            with col2:
                st.write("**Study Subtype Summary**")
                st.dataframe(st.session_state["df_subtype"], use_container_width=True)

# -----------------------------------------------------------------------------
# 3. VEEVA STUDY ARM PRODUCT MATCHER
# -----------------------------------------------------------------------------
elif selected_tool == "Veeva Study Arm Product Matcher":
    st.subheader("🧬 Veeva Study Arm Product Alignment")

    if st.button("Execute Alignment Pipeline"):
        with st.spinner("Processing relational merges across parent/child structures..."):
            matcher = VeevaStudyArmProductMatcher(user_id=user_id)
            matcher.connect()
            df_expanded, df_collapsed = matcher.execute_alignment_pipeline(
                output_dir="./out"
            )

            st.session_state["arm_expanded"] = df_expanded
            st.session_state["arm_collapsed"] = df_collapsed

    if "arm_collapsed" in st.session_state:
        df_collapsed = st.session_state["arm_collapsed"]
        df_expanded = st.session_state["arm_expanded"]

        col1, col2 = st.columns(2)
        with col1:
            st.download_button(
                label="💾 Download One-Study One-Row Matrix",
                data=convert_df_to_csv(df_collapsed),
                file_name="veeva_study_arm_product_collapsed.csv",
                mime="text/csv",
            )
        with col2:
            st.download_button(
                label="💾 Download Granular Trace Matrix",
                data=convert_df_to_csv(df_expanded),
                file_name="veeva_study_arm_product_expanded.csv",
                mime="text/csv",
            )

        tab1, tab2 = st.tabs(
            ["One-Study One-Row (Collapsed)", "Granular Traceability (Expanded)"]
        )
        with tab1:
            st.dataframe(df_collapsed, use_container_width=True)
        with tab2:
            st.dataframe(df_expanded, use_container_width=True)

# -----------------------------------------------------------------------------
# 4. VEEVA SUBJECT PROFILER
# -----------------------------------------------------------------------------
elif selected_tool == "Veeva Subject Profiler":
    st.subheader("👥 Veeva Subject Demographics & Diagnostics Profiler")

    if st.button("Run Subject Profiling Engine", type="primary"):
        with st.spinner("Extracting formatted subject records from Snowflake..."):
            try:
                profiler = VeevaSubjectProfiler(user_id=user_id)
                profiler.connect()

                df_subjects = profiler.fetch_cur_subject_records()

                st.session_state["df_subjects"] = df_subjects
                st.session_state["active_filtered_df"] = df_subjects.copy()
                st.session_state["aggrid_key"] = 0
                st.rerun()
            except Exception as e:
                st.error(f"❌ Pipeline Execution Failed: {str(e)}")

    if "df_subjects" in st.session_state:
        df_subjects = st.session_state["df_subjects"]
        profiler_instance = VeevaSubjectProfiler(user_id=user_id)

        if "aggrid_key" not in st.session_state:
            st.session_state["aggrid_key"] = 0
        if "active_filtered_df" not in st.session_state:
            st.session_state["active_filtered_df"] = df_subjects.copy()

        # Active filtered dataset source
        df_display = st.session_state["active_filtered_df"]

        # Dynamically recalculate high-level KPIs based on the filtered view
        filtered_sub, filtered_std, filtered_ctry = profiler_instance.compute_summary_metrics(df_display)

        # High-level KPIs
        m1, m2, m3 = st.columns(3)
        m1.metric("Total Subjects", f"{filtered_sub:,}")
        m2.metric("Unique Studies", f"{filtered_std:,}")
        m3.metric("Unique Countries", f"{filtered_ctry:,}")

        # Download options row
        dl_col1, dl_col2 = st.columns(2)
        with dl_col1:
            st.download_button(
                label="💾 Download Full Dataset (CSV)",
                data=convert_df_to_csv(df_subjects),
                file_name="veeva_formatted_cur_subject_records_full.csv",
                mime="text/csv",
                use_container_width=True,
            )
        with dl_col2:
            st.download_button(
                label="🎯 Download Filtered Dataset (CSV)",
                data=convert_df_to_csv(df_display),
                file_name="veeva_formatted_cur_subject_records_filtered.csv",
                mime="text/csv",
                use_container_width=True,
            )

        # Advanced Column Multi-Filter Panel
        with st.expander("🎛️ Click to Open/Hide Advanced Column Multi-Filter Panel", expanded=False):
            st.caption("Select target columns to configure dynamic categorical or numerical filtering rules.")

            filterable_cols = [c for c in df_subjects.columns if c != "::auto_unique_id::"]
            numeric_cols = ["SUBJECT_AGE", "BIRTH_YEAR_YYYY"]

            selected_filter_cols = st.multiselect(
                "Select Target Columns to Filter:",
                options=filterable_cols,
                default=["STUDY_ID"] if "STUDY_ID" in filterable_cols else [],
                key=f"panel_multiselect_{st.session_state['aggrid_key']}",
            )

            st.markdown("---")
            pending_filtered_df = df_subjects.copy()

            for target_col in selected_filter_cols:
                st.markdown(f"**Filter Criteria for Column:** `{target_col}`")

                if target_col in numeric_cols:
                    op_col, val_col = st.columns([1, 2])
                    with op_col:
                        op = st.selectbox(
                            f"Operator for `{target_col}`:",
                            options=["Equals", "Greater Than", "Less Than", "Between", "Is Null"],
                            key=f"op_{target_col}_{st.session_state['aggrid_key']}",
                        )
                    with val_col:
                        num_series = pd.to_numeric(pending_filtered_df[target_col], errors="coerce")
                        min_v = float(num_series.min()) if not num_series.dropna().empty else 0.0
                        max_v = float(num_series.max()) if not num_series.dropna().empty else 100.0

                        if op in ["Equals", "Greater Than", "Less Than"]:
                            target_num = st.number_input(
                                f"Value for `{target_col}`:",
                                value=min_v,
                                key=f"num_{target_col}_{st.session_state['aggrid_key']}",
                            )
                            if op == "Equals":
                                pending_filtered_df = pending_filtered_df[num_series == target_num]
                            elif op == "Greater Than":
                                pending_filtered_df = pending_filtered_df[num_series > target_num]
                            elif op == "Less Than":
                                pending_filtered_df = pending_filtered_df[num_series < target_num]

                        elif op == "Between":
                            b_val = st.slider(
                                f"Range for `{target_col}`:",
                                min_value=min_v,
                                max_value=max_v,
                                value=(min_v, max_v),
                                key=f"slider_{target_col}_{st.session_state['aggrid_key']}",
                            )
                            pending_filtered_df = pending_filtered_df[
                                (num_series >= b_val[0]) & (num_series <= b_val[1])
                            ]
                        elif op == "Is Null":
                            pending_filtered_df = pending_filtered_df[num_series.isnull()]

                else:
                    col_f1, col_f2 = st.columns(2)
                    clean_options = clean_dropdown_options(df_subjects[target_col])

                    with col_f1:
                        m_vals = st.multiselect(
                            f"Select values for `{target_col}`:",
                            options=clean_options,
                            key=f"multi_{target_col}_{st.session_state['aggrid_key']}",
                        )
                    with col_f2:
                        c_input = st.text_input(
                            f"Or enter comma-separated values for `{target_col}`:",
                            value="",
                            key=f"comma_{target_col}_{st.session_state['aggrid_key']}",
                        )

                    target_filters = set(m_vals)
                    if c_input.strip():
                        parsed_comma = [s.strip() for s in c_input.split(",") if s.strip()]
                        target_filters.update(parsed_comma)

                    if target_filters:
                        escaped_vals = [re.escape(v) for v in target_filters]
                        pattern = "|".join([r"^" + v + r"$" for v in escaped_vals])

                        filtered_subset = pending_filtered_df[
                            pending_filtered_df[target_col].astype(str).str.contains(pattern, case=False, na=False)
                        ]
                        if filtered_subset.empty:
                            loose_pattern = "|".join(escaped_vals)
                            filtered_subset = pending_filtered_df[
                                pending_filtered_df[target_col].astype(str).str.contains(loose_pattern, case=False, na=False)
                            ]
                        pending_filtered_df = filtered_subset

            st.markdown("---")
            btn_act1, btn_act2 = st.columns([1, 1])

            with btn_act1:
                if st.button("🚀 Apply Filters", type="primary", use_container_width=True):
                    st.session_state["active_filtered_df"] = pending_filtered_df.reset_index(drop=True)
                    st.rerun()

            with btn_act2:
                if st.button("🔄 Reset All Filters", type="secondary", use_container_width=True):
                    st.session_state["aggrid_key"] += 1
                    st.session_state["active_filtered_df"] = df_subjects.copy()
                    st.rerun()

        # Dataset Viewer
        st.write("### 📊 Dataset Viewer")
        st.caption("Highlight/click-and-drag cell text or select rows to copy content directly (`Ctrl+C` / `Cmd+C`).")

        gb = GridOptionsBuilder.from_dataframe(df_display)
        gb.configure_pagination(paginationAutoPageSize=False, paginationPageSize=15)
        gb.configure_side_bar()
        gb.configure_selection(selection_mode="multiple", use_checkbox=True)
        gb.configure_default_column(filterable=False, sortable=True, resizable=True, minWidth=180)

        gridOptions = gb.build()
        gridOptions["enableCellTextSelection"] = True
        gridOptions["ensureDomOrder"] = True

        grid_response = AgGrid(
            df_display,
            gridOptions=gridOptions,
            data_return_mode=DataReturnMode.FILTERED_AND_SORTED,
            enable_enterprise_modules=False,
            height=440,
            theme="alpine",
            width="100%",
            key=f"subject_grid_{st.session_state['aggrid_key']}",
        )

        filtered_df = df_display.copy()
        if "::auto_unique_id::" in filtered_df.columns:
            filtered_df = filtered_df.drop(columns=["::auto_unique_id::"])

        total_base_subjects = len(df_subjects)
        filtered_count = len(filtered_df)
        filter_percentage = (filtered_count / total_base_subjects * 100) if total_base_subjects > 0 else 0

        f_col1, f_col2 = st.columns(2)
        f_col1.metric("Filtered Records Count", f"{filtered_count:,}")
        f_col2.metric("Filtered Percentage of Total", f"{filter_percentage:.1f}%")

        # Column Inspector with Value Distribution Breakdown
        st.markdown("---")
        st.write("### 🔍 Column Inspector")
        st.caption(f"Inspecting **{filtered_count:,}** filtered records currently active in the grid above.")

        valid_inspect_columns = [col for col in filtered_df.columns if col != "::auto_unique_id::"]
        col_inspect = st.selectbox("Select Column to Inspect:", options=valid_inspect_columns)

        if col_inspect:
            col_data = filtered_df[col_inspect]

            numeric_series = pd.to_numeric(col_data, errors="coerce")
            is_numeric = numeric_series.dropna().shape[0] > 0 and not col_data.dtype == "object"

            ic1, ic2 = st.columns(2)
            null_count = col_data.isnull().sum() + (col_data == "").sum() + (col_data == "None").sum()
            ic1.metric("Null / Blank Count", f"{null_count:,}")

            if is_numeric and numeric_series.dropna().shape[0] > 0:
                mean_val = numeric_series.mean()
                median_val = numeric_series.median()
                min_val = numeric_series.min()
                max_val = numeric_series.max()
                ic2.metric("Mean / Median | Range", f"{mean_val:.2f} / {median_val:.2f} | [{min_val} to {max_val}]")
            else:
                ic2.metric("Data Type", str(col_data.dtype).upper())

            unique_vals_list = clean_dropdown_options(col_data)
            unique_str = ", ".join(unique_vals_list) if unique_vals_list else "[NONE / ALL NULL IN FILTERED SET]"

            st.write(f"**Unique Values ({len(unique_vals_list):,} total):**")
            st.code(unique_str, language="text")

            # Low cardinality breakdown (< 20 unique values)
            if 0 < len(unique_vals_list) < 20:
                st.write("**📊 Value Distribution Breakdown:**")
                cleaned_series = col_data.fillna("[NULL / UNASSIGNED]").astype(str).str.strip()
                cleaned_series = cleaned_series.replace(
                    {"": "[NULL / UNASSIGNED]", "nan": "[NULL / UNASSIGNED]", "None": "[NULL / UNASSIGNED]"}
                )

                counts_df = cleaned_series.value_counts().reset_index()
                counts_df.columns = [col_inspect, "Count"]
                counts_df["Percentage"] = (counts_df["Count"] / len(cleaned_series) * 100).round(2).astype(str) + "%"

                st.dataframe(counts_df, use_container_width=True, hide_index=True)

        # Dynamic Demographics Visual Distributions
        st.markdown("---")
        st.write("#### 📈 Demographics Visual Distributions")
        col_a, col_b = st.columns(2)

        filtered_df["SUBJECT_AGE"] = pd.to_numeric(filtered_df["SUBJECT_AGE"], errors="coerce")
        filtered_df["BIRTH_YEAR_YYYY"] = pd.to_numeric(filtered_df["BIRTH_YEAR_YYYY"], errors="coerce")

        with col_a:
            fig_age = px.histogram(
                filtered_df,
                x="SUBJECT_AGE",
                nbins=20,
                title=f"Subject Age Distribution (n={filtered_count:,})",
                template="plotly_white",
                color_discrete_sequence=["#1f77b4"],
            )
            fig_age.update_layout(height=320, margin=dict(l=10, r=10, t=40, b=10))
            st.plotly_chart(fig_age, use_container_width=True)

        with col_b:
            fig_birth = px.histogram(
                filtered_df,
                x="BIRTH_YEAR_YYYY",
                nbins=20,
                title=f"Birth Year Distribution (n={filtered_count:,})",
                template="plotly_white",
                color_discrete_sequence=["#2ca02c"],
            )
            fig_birth.update_layout(height=320, margin=dict(l=10, r=10, t=40, b=10))
            st.plotly_chart(fig_birth, use_container_width=True)

        st.write("#### 🔠 Categorical Feature Distributions")
        dynamic_cat_dist = profiler_instance.compute_categorical_distributions(filtered_df)

        cat_items = list(dynamic_cat_dist.items())
        for i in range(0, len(cat_items), 2):
            c1, c2 = st.columns(2)
            col_name_1, df_dist_1 = cat_items[i]
            with c1:
                st.write(f"**📌 `{col_name_1}`**")
                st.dataframe(df_dist_1, use_container_width=True)

            if i + 1 < len(cat_items):
                col_name_2, df_dist_2 = cat_items[i + 1]
                with c2:
                    st.write(f"**📌 `{col_name_2}`**")
                    st.dataframe(df_dist_2, use_container_width=True)

        st.markdown("---")
        st.write("#### 🔍 Data Completeness Diagnostic")
        st.write("**Lowest Fill-Rate Attributes**")
        df_filtered_comp = profiler_instance.compute_completeness_diagnostic(filtered_df)
        st.dataframe(df_filtered_comp.head(15), use_container_width=True)

    else:
        st.info("👈 Click **Run Subject Profiling Engine** above to execute Snowflake queries and view data visuals.")

# -----------------------------------------------------------------------------
# 5. VEEVA COLUMN PROFILER
# -----------------------------------------------------------------------------
elif selected_tool == "Veeva Column Profiler":
    st.subheader("📖 Global Schema Data Profiler")

    sample_limit = st.number_input("Distinct Value Preview Limit", min_value=5, max_value=100, value=20)

    if st.button("Generate Schema Profile"):
        with st.spinner("Pushing aggregation mathematics down to Snowflake..."):
            profiler = VeevaColumnProfiler(user_id=user_id)
            profiler.connect()
            df_dict = profiler.profile_schema_columns(sample_value_limit=sample_limit)
            st.session_state["data_dict"] = df_dict

    if "data_dict" in st.session_state:
        df_dict = st.session_state["data_dict"]
        st.download_button(
            label="💾 Download Data Dictionary (CSV)",
            data=convert_df_to_csv(df_dict),
            file_name="veeva_data_dictionary.csv",
            mime="text/csv",
        )
        st.dataframe(df_dict, use_container_width=True)

# -----------------------------------------------------------------------------
# 6. VEEVA TABLE COLUMN MATCHER
# -----------------------------------------------------------------------------
elif selected_tool == "Veeva Table Column Matcher":
    st.subheader("🔍 Schema Lineage & Key Dependency Matcher")

    if st.button("Compute Overlap Matrix"):
        with st.spinner("Calculating cross-table value set overlaps..."):
            matcher = VeevaTableColumnMatcher(user_id=user_id)
            matcher.connect()
            orig_m, split_m, human_cat = matcher.compute_overlap_matrix(prefix_output="./out/veeva_ctms")

            st.session_state["matcher_cat"] = human_cat
            st.session_state["matcher_split"] = split_m

    if "matcher_cat" in st.session_state:
        st.download_button(
            label="💾 Download Human Readable Catalog (CSV)",
            data=convert_df_to_csv(st.session_state["matcher_cat"]),
            file_name="veeva_column_overlap_catalog.csv",
            mime="text/csv",
        )

        tab1, tab2 = st.tabs(["Human Readable Matches", "Split Overlap Matrix"])
        with tab1:
            st.dataframe(st.session_state["matcher_cat"], use_container_width=True)
        with tab2:
            st.dataframe(st.session_state["matcher_split"], use_container_width=True)
