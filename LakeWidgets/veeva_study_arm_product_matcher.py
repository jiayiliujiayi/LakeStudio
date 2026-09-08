import os
import pandas as pd
import snowflake.connector

class VeevaStudyArmProductMatcher:
    def __init__(self, user_id=None):
        """Initializes the Matcher using corporate SSO configuration profiles."""
        self.user_id = user_id or os.environ.get("SNOWFLAKE_USER")
        if not self.user_id:
            raise ValueError("❌ Initialization Failed: A valid corporate user_id must be provided.")
        self.conn = None

    def connect(self):
        """Initializes the Snowflake session via SSO and locks environmental target paths."""
        print(f"Initializing global Snowflake PROD Connection via SSO for {self.user_id}...")
        self.conn = snowflake.connector.connect(
            user=self.user_id,
            account="colgatepalmoliveprod.us-central1.gcp",
            authenticator="externalbrowser",
        )
        
        # Lock schema and db scopes right at session birth
        init_cur = self.conn.cursor()
        init_cur.execute("USE DATABASE PROD_GTED_HUB")
        init_cur.execute("USE SCHEMA CUR_CLIN_VEEVA_CTMS")
        init_cur.close()
        print("✅ Global session context configured and active.")
        return self.conn

    def _write_csv_with_comments(self, file_path, dataframe, title_label):
        """Utility wrapper to inject governance metadata tracking headers to raw file drops."""
        dirname = os.path.dirname(file_path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
            
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(f'\"# =========================================================================================\"\n')
            f.write(f'\"# {title_label.upper()}\"\n')
            f.write(f'\"# =========================================================================================\"\n')
            f.write(f'\"# Source Extraction Path: PROD_GTED_HUB.CUR_CLIN_VEEVA_CTMS\"\n\n')
            dataframe.to_csv(f, index=False)
        print(f"💾 CSV Matrix successfully written to: {file_path}")

    def execute_alignment_pipeline(self, output_dir="./out"):
        """Extracts source structures, applies deep validation cleansing, and generates CSV data layers."""
        if not self.conn:
            raise ConnectionError("No active Snowflake connection detected. Run .connect() first.")

        cur = self.conn.cursor()
        
        # A. Query Base Studies (Excluding LAST_MODIFIED_DATE)
        base_study_query = """
        SELECT ID AS STUDY_ID, VEEVA_ID, COLGATE_PROTOCOL_NUMBER, PROTOCOL_TITLE, 
               LIFECYCLE_STATE, STUDY_PURPOSE, STUDY_SUBTYPE, STUDY_ORIGINATOR, STUDY_TYPE
        FROM PROD_GTED_HUB.CUR_CLIN_VEEVA_CTMS.CUR_STUDY
        WHERE COLGATE_PROTOCOL_NUMBER NOT LIKE '%Production Test%' AND STUDY_TYPE = 'OOT000000001E02'
        ORDER BY VEEVA_ID DESC;
        """
        cur.execute(base_study_query)
        df_studies = pd.DataFrame(cur.fetchall(), columns=[d[0].upper() for d in cur.description])

        # B. Query Target Study Arms
        arms_query = """
        SELECT ARM_NAME, ARM_TYPE, DESCRIPTION as ARM_DESCRIPTION, DESCRIPTION2 as ARM_DESCRIPTION2, 
               ID AS ARM_ID, STATUS AS ARM_STATUS, PRODUCT_CODE, STUDY AS STUDY_ID
        FROM PROD_GTED_HUB.CUR_CLIN_VEEVA_CTMS.CUR_STUDY_ARM;
        """
        cur.execute(arms_query)
        df_arms = pd.DataFrame(cur.fetchall(), columns=[d[0].upper() for d in cur.description])

        # C. Query Arm Technology Bridge Table
        tech_query = """
        SELECT STUDY_ARM AS ARM_ID, TECHNOLOGY AS TECHNOLOGY_ID
        FROM PROD_GTED_HUB.CUR_CLIN_VEEVA_CTMS.CUR_STUDY_ARM_STUDY_TECH;
        """
        cur.execute(tech_query)
        df_tech = pd.DataFrame(cur.fetchall(), columns=[d[0].upper() for d in cur.description])

        # D. Query Target Study Product Metadata
        product_query = """
        SELECT ACTIVE_INGREDIENT AS PDM_ACTIVE_INGREDIENT, CLASSIFICATION AS PDM_CLASSIFICATION, 
               DELIVERY AS PDM_DELIVERY, DESCRIPTION AS PDM_DESCRIPTION, ID AS TECHNOLOGY_ID, 
               NAME AS PDM_NAME, PDM_URL, ROLE AS PDM_ROLE, STUDY AS STUDY_ID, USE AS PDM_USE
        FROM PROD_GTED_HUB.CUR_CLIN_VEEVA_CTMS.CUR_STUDY_PRODUCT;
        """
        cur.execute(product_query)
        df_products = pd.DataFrame(cur.fetchall(), columns=[d[0].upper() for d in cur.description])
        cur.close()

        # ---- RELATIONSHIP WORKSPACE MERGES ----
        df_merged = pd.merge(df_studies, df_arms, on="STUDY_ID", how="left")
        df_merged = pd.merge(df_merged, df_tech, on="ARM_ID", how="left")
        df_merged = pd.merge(df_merged, df_products, on=["STUDY_ID", "TECHNOLOGY_ID"], how="left")

        # ---- SANITIZATION & CONTENT CLEANING ----
        
        # A. Clean STUDY_SUBTYPE
        if "STUDY_SUBTYPE" in df_merged.columns:
            cleaned_subtype = df_merged["STUDY_SUBTYPE"].fillna("").astype(str).str.replace(r"\d*__c", "", case=False, regex=True).str.replace("_", " ", regex=False).str.strip().str.lower()
            subtype_mappings = {"oc claims": "Oral care claims", "poc": "Proof of concept", "inhouse poc": "In house proof of concept"}
            df_merged["STUDY_SUBTYPE"] = cleaned_subtype.map(subtype_mappings).fillna(cleaned_subtype.str.capitalize()).replace("", None)

        # B, C, D. Clean PDM text domains (DELIVERY, ROLE, USE)
        for txt_col in ["PDM_DELIVERY", "PDM_ROLE", "PDM_USE"]:
            if txt_col in df_merged.columns:
                df_merged[txt_col] = (df_merged[txt_col].fillna("").astype(str)
                                      .str.replace(r"__c", "", case=False, regex=True)
                                      .str.replace("_", " ", regex=False).str.strip().str.title())
                if txt_col == "PDM_DELIVERY":
                    df_merged[txt_col] = df_merged[txt_col].replace({"Na": "N/A", "Devicechem": "Device Chem"})
                df_merged[txt_col] = df_merged[txt_col].replace("", None).replace("Nan", None)

        # E. Clean LIFECYCLE_STATE (Harmonized from Study Aggregator logic)
        if "LIFECYCLE_STATE" in df_merged.columns:
            lifecycle_mappings = {
                "active_state__v": "Active", 
                "cancelled_state__v": "Cancelled", 
                "candidate_state__v": "Candidate",
                "closed_state__c": "Closed [Hard Lock]", 
                "closing_state__v": "Closing [Soft Lock]", 
                "planning_state__v": "Planning"
            }
            regex_fallback = (df_merged["LIFECYCLE_STATE"].fillna("").astype(str)
                              .str.replace(r"(?i)_state__[vc]|__[vc]|\bstate\b", " ", regex=True)
                              .str.replace("_", " ", regex=False).str.strip().str.title())
            df_merged["LIFECYCLE_STATE"] = df_merged["LIFECYCLE_STATE"].str.strip().map(lifecycle_mappings).fillna(regex_fallback).replace("", None)

        # ---- REMOVE THE EXTRA STUDY_TYPE COLUMN AS REQUESTED ----
        if "STUDY_TYPE" in df_merged.columns:
            df_merged = df_merged.drop(columns=["STUDY_TYPE"])

        # ---- METRICS CALCULATIONS ----
        total_studies_count = df_studies["STUDY_ID"].nunique()
        studies_missing_arms = len(set(df_studies["STUDY_ID"].unique()) - set(df_arms["STUDY_ID"].dropna().unique()))
        studies_missing_products = len(set(df_studies["STUDY_ID"].unique()) - set(df_products["STUDY_ID"].dropna().unique()))

        # Strip completely unpopulated columns from expanding workspace
        completely_blank_cols = df_merged.columns[df_merged.isna().all()].tolist()
        df_merged = df_merged.dropna(axis=1, how='all')

        # ---- OUTPUT 1: WRITE THE EXPANDED LOG BASE ----
        expanded_file = os.path.join(output_dir, "veeva_study_arm_product_expanded.csv")
        self._write_csv_with_comments(expanded_file, df_merged, "Veeva Study Arm Product Expanded Workspace")

        # ---- OUTPUT 2: ARCHITECT COLLAPSED "ONE-ROW" MATRIX WITH INTERNAL NEWLINES ----
        # Removed STUDY_TYPE from index definitions
        study_metadata_cols = ["STUDY_ID", "VEEVA_ID", "COLGATE_PROTOCOL_NUMBER", "PROTOCOL_TITLE", "LIFECYCLE_STATE", "STUDY_PURPOSE", "STUDY_SUBTYPE", "STUDY_ORIGINATOR"]
        group_by_cols = [col for col in study_metadata_cols if col in df_merged.columns]
        child_cols = [col for col in df_merged.columns if col not in group_by_cols]

        def collapse_to_cell_newlines(series):
            unique_vals = series.dropna().astype(str).str.strip().drop_duplicates()
            unique_vals = [val for val in unique_vals if val != ""]
            return "\n".join(unique_vals) if unique_vals else None

        agg_dict = {col: collapse_to_cell_newlines for col in child_cols}
        df_collapsed = df_merged.groupby(group_by_cols, dropna=False).agg(agg_dict).reset_index()

        collapsed_file = os.path.join(output_dir, "veeva_study_arm_product_collapsed.csv")
        self._write_csv_with_comments(collapsed_file, df_collapsed, "Veeva Unified Study Arm Product Collapsed Master")

        # ---- OUTPUT 3: BUILD AND WRITE FILE CONSOLE LOG REPORT ----
        blank_cols_str = ", ".join(completely_blank_cols) if completely_blank_cols else "None found."
        
        log_report = []
        log_report.append("="*60)
        log_report.append("📊 VEEVA TRACKING PIPELINE LOG CONSOLE EXPORT")
        log_report.append("="*60)
        log_report.append(f"🔹 Total unique base Oral Care studies processed: {total_studies_count}")
        log_report.append(f"❌ Studies WITHOUT any CUR_STUDY_ARM records:     {studies_missing_arms}")
        log_report.append(f"❌ Studies WITHOUT any CUR_STUDY_PRODUCT records: {studies_missing_products}")
        log_report.append("-"*60)
        log_report.append(f"🧬 Expanded Granular DataFrame Rows:               {len(df_merged)}")
        log_report.append(f"🧬 Consolidated Unique Study Columns Rows:         {len(df_collapsed)}")
        log_report.append("-"*60)
        log_report.append(f"🧹 Automatically dropped blank column(s):          {blank_cols_str}")
        log_report.append("="*60)
        
        full_log_text = "\n".join(log_report)
        print(full_log_text)
        
        log_file_path = os.path.join(output_dir, "veeva_study_arm_product_run_summary.log")
        with open(log_file_path, "w", encoding="utf-8") as lf:
            lf.write(full_log_text + "\n")
        print(f"📝 Run summary trace saved successfully to: {log_file_path}")

        return df_merged, df_collapsed