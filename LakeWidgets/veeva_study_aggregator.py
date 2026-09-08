import os
import pandas as pd
import snowflake.connector

class VeevaStudyAggregator:
    def __init__(self, user_id=None):
        # Fallback to an environment variable, or force the user to provide it
        import os
        self.user_id = user_id or os.environ.get("SNOWFLAKE_USER")
        
        if not self.user_id:
            raise ValueError("❌ Initialization Failed: A valid corporate user_id must be provided.")
        self.conn = None

    def connect(self):
        """Initializes the Snowflake session via SSO and sets environment contexts globally."""
        print(f"Initializing global Snowflake PROD Connection via SSO for {self.user_id}...")
        self.conn = snowflake.connector.connect(
            user=self.user_id,
            account="colgatepalmoliveprod.us-central1.gcp",
            authenticator="externalbrowser",
        )
        
        # Set database context globally
        init_cur = self.conn.cursor()
        init_cur.execute("USE DATABASE PROD_GTED_HUB")
        init_cur.execute("USE SCHEMA CUR_CLIN_VEEVA_CTMS")
        init_cur.close()
        print("✅ Global connection active and ready.")
        return self.conn

    def compile_metrics_dashboard(self):
        """Extracts and calculates standalone distribution frequencies and cross-tabulations."""
        if not self.conn:
            raise ConnectionError("No active Snowflake connection. Call .connect() first.")
            
        cur = self.conn.cursor()
        try:
            print("Fetching global classification distributions for type and subtype tracking...")
            metrics_query = """
            SELECT 
                COALESCE(STUDY_TYPE, 'UNASSIGNED') as STUDY_TYPE,
                COALESCE(STUDY_SUBTYPE, 'UNASSIGNED') as STUDY_SUBTYPE,
                COUNT(DISTINCT ID) as STUDY_COUNT
            FROM PROD_GTED_HUB.CUR_CLIN_VEEVA_CTMS.CUR_STUDY
            GROUP BY STUDY_TYPE, STUDY_SUBTYPE
            ORDER BY STUDY_COUNT DESC;
            """
            cur.execute(metrics_query)
            metric_cols = ["STUDY_TYPE", "STUDY_SUBTYPE", "STUDY_COUNT"]
            df_raw = pd.DataFrame(cur.fetchall(), columns=metric_cols)

            df_type = df_raw.groupby("STUDY_TYPE")["STUDY_COUNT"].sum().reset_index().sort_values(by="STUDY_COUNT", ascending=False)
            df_subtype = df_raw.groupby("STUDY_SUBTYPE")["STUDY_COUNT"].sum().reset_index().sort_values(by="STUDY_COUNT", ascending=False)

            df_pivot = df_raw.pivot_table(index="STUDY_TYPE", columns="STUDY_SUBTYPE", values="STUDY_COUNT", aggfunc="sum", fill_value=0)
            df_pivot["TOTAL_STUDIES"] = df_pivot.sum(axis=1)
            
            col_totals = df_pivot.sum(axis=0).to_frame().T
            col_totals.index = ["TOTAL_SUBTYPES"]
            df_pivot = pd.concat([df_pivot, col_totals]).reset_index().rename(columns={"index": "STUDY_TYPE"})

            return df_type, df_subtype, df_pivot
        finally:
            cur.close()

    def extract_clean_oral_care_studies(self):
        """Ingests, harmonizes, and deduplicates the Master Oral Care dataset."""
        if not self.conn:
            raise ConnectionError("No active Snowflake connection. Call .connect() first.")
            
        cur = self.conn.cursor()
        try:
            print("Executing comprehensive master study extraction from HUB layer...")
            query_study = """
            SELECT ID, VEEVA_ID, COLGATE_PROTOCOL_NUMBER, PROTOCOL_TITLE, LIFECYCLE_STATE,
                   STUDY_PURPOSE, SAMPLE_SIZE, PRIMARY_TECHNOLOGY, STUDY_SUBTYPE, STUDY_ORIGINATOR, STUDY_TYPE, LAST_MODIFIED_DATE
            FROM PROD_GTED_HUB.CUR_CLIN_VEEVA_CTMS.CUR_STUDY ORDER BY VEEVA_ID DESC;
            """
            cur.execute(query_study)
            study_cols = [desc[0].upper() for desc in cur.description]
            df_study_raw = pd.DataFrame(cur.fetchall(), columns=study_cols)

            query_product = """
            SELECT CLASSIFICATION, DESCRIPTION, LAST_MODIFIED_DATE
            FROM PROD_GTED_HUB.CUR_CLIN_VEEVA_CTMS.CUR_STUDY_PRODUCT
            WHERE CLASSIFICATION IS NOT NULL AND DESCRIPTION IS NOT NULL;
            """
            cur.execute(query_product)
            prod_cols = [desc[0].upper() for desc in cur.description]
            df_product_raw = pd.DataFrame(cur.fetchall(), columns=prod_cols)

            if "COLGATE_PROTOCOL_NUMBER" in df_study_raw.columns:
                df_study_raw = df_study_raw[~df_study_raw["COLGATE_PROTOCOL_NUMBER"].fillna("").astype(str).str.contains("Production Test", case=False, na=False)]

            if "STUDY_TYPE" in df_study_raw.columns:
                df_study_raw = df_study_raw[df_study_raw["STUDY_TYPE"] == "OOT000000001E02"]

            df_study_raw["LAST_MODIFIED_DATE"] = pd.to_datetime(df_study_raw["LAST_MODIFIED_DATE"])
            df_product_raw["LAST_MODIFIED_DATE"] = pd.to_datetime(df_product_raw["LAST_MODIFIED_DATE"])

            df_prod_sorted = df_product_raw.sort_values(by=["CLASSIFICATION", "LAST_MODIFIED_DATE"])
            df_product_clean = df_prod_sorted.groupby("CLASSIFICATION", dropna=False)["DESCRIPTION"].agg(lambda s: "; ".join(s.dropna().astype(str).str.strip().drop_duplicates())).reset_index().rename(columns={"DESCRIPTION": "RESOLVED_TECH_NAME"})

            df_originators = df_study_raw.groupby("COLGATE_PROTOCOL_NUMBER", dropna=False)["STUDY_ORIGINATOR"].agg(lambda s: ", ".join(s.dropna().astype(str).str.strip().drop_duplicates())).reset_index()

            df_study_sorted = df_study_raw.sort_values(by=["COLGATE_PROTOCOL_NUMBER", "LAST_MODIFIED_DATE"])
            df_study_deduped = df_study_sorted.drop_duplicates(subset=["COLGATE_PROTOCOL_NUMBER"], keep="last").drop(columns=["STUDY_ORIGINATOR"])

            df_clean = pd.merge(df_study_deduped, df_originators, on="COLGATE_PROTOCOL_NUMBER", how="left")
            df_clean = pd.merge(df_clean, df_product_clean, left_on="PRIMARY_TECHNOLOGY", right_on="CLASSIFICATION", how="left")
            df_clean["PRIMARY_TECHNOLOGY"] = df_clean["RESOLVED_TECH_NAME"].fillna(df_clean["PRIMARY_TECHNOLOGY"])

            if "PROTOCOL_TITLE" in df_clean.columns:
                df_clean["PROTOCOL_TITLE"] = df_clean["PROTOCOL_TITLE"].fillna("").astype(str).str.replace(r"\s+", " ", regex=True).str.strip().replace("", None)
            if "STUDY_PURPOSE" in df_clean.columns:
                df_clean["STUDY_PURPOSE"] = df_clean["STUDY_PURPOSE"].fillna("").astype(str).str.replace(r"\s+", " ", regex=True).str.strip().replace("", None)

            if "LIFECYCLE_STATE" in df_clean.columns:
                lifecycle_mappings = {
                    "active_state__v": "Active", "cancelled_state__v": "Cancelled", "candidate_state__v": "Candidate",
                    "closed_state__c": "Closed [Hard Lock]", "closing_state__v": "Closing [Soft Lock]", "planning_state__v": "Planning"
                }
                regex_fallback = df_clean["LIFECYCLE_STATE"].fillna("").astype(str).str.replace(r"(?i)_state__[vc]|__[vc]|\bstate\b", " ", regex=True).str.replace("_", " ", regex=False).str.strip().str.title()
                df_clean["LIFECYCLE_STATE"] = df_clean["LIFECYCLE_STATE"].str.strip().map(lifecycle_mappings).fillna(regex_fallback).replace("", None)

            if "STUDY_SUBTYPE" in df_clean.columns:
                cleaned_subtype = df_clean["STUDY_SUBTYPE"].fillna("").astype(str).str.replace(r"\d*__c", "", case=False, regex=True).str.replace("_", " ", regex=False).str.strip().str.lower()
                subtype_mappings = {"oc claims": "Oral care claims", "poc": "Proof of concept", "inhouse poc": "In house proof of concept"}
                df_clean["STUDY_SUBTYPE"] = cleaned_subtype.map(subtype_mappings).fillna(cleaned_subtype.str.capitalize()).replace("", None)

            ordered_cols = ["ID", "VEEVA_ID", "COLGATE_PROTOCOL_NUMBER", "PROTOCOL_TITLE", "LIFECYCLE_STATE", "STUDY_PURPOSE", "SAMPLE_SIZE", "PRIMARY_TECHNOLOGY", "STUDY_SUBTYPE", "STUDY_ORIGINATOR"]
            return df_clean[ordered_cols].sort_values(by="VEEVA_ID", ascending=False)
        finally:
            cur.close()

    def aggregate_study_sites(self, study_ids):
        """Extracts and collapses transactional child site rows for targeted study IDs."""
        if not self.conn:
            raise ConnectionError("No active Snowflake connection.")
        if not study_ids:
            return pd.DataFrame(columns=["STUDY_ID", "SITE_ID", "SITE_NAME", "SITE_NAME_UNIQUE", "PRINCIPAL_INVESTIGATOR"])
            
        cur = self.conn.cursor()
        try:
            # Resolved f-string backslash parsing friction
            formatted_ids = ", ".join([f"'{uid}'" for uid in study_ids])
            
            query = f"""
            SELECT STUDY_NUMBER as STUDY_ID, ID as SITE_ID, SITE_NAME, PRINCIPAL_INVESTIGATOR 
            FROM PROD_GTED_HUB.CUR_CLIN_VEEVA_CTMS.CUR_STUDY_SITE 
            WHERE STUDY_NUMBER IN ({formatted_ids})
            """
            cur.execute(query)
            df_site_raw = pd.DataFrame(cur.fetchall(), columns=["STUDY_ID", "SITE_ID", "SITE_NAME", "PRINCIPAL_INVESTIGATOR"])

            collapse_unique = lambda s: ", ".join(s.dropna().astype(str).str.strip().replace("", None).drop_duplicates())
            collapse_all = lambda s: ", ".join(s.dropna().astype(str).str.strip().replace("", None))

            df_site = df_site_raw.groupby("STUDY_ID").agg({"SITE_ID": collapse_unique, "SITE_NAME": collapse_all, "PRINCIPAL_INVESTIGATOR": collapse_unique}).reset_index()
            df_unique = df_site_raw.groupby("STUDY_ID")["SITE_NAME"].agg(collapse_unique).reset_index().rename(columns={"SITE_NAME": "SITE_NAME_UNIQUE"})
            
            df_site = pd.merge(df_site, df_unique, on="STUDY_ID", how="left")
            return df_site[["STUDY_ID", "SITE_ID", "SITE_NAME", "SITE_NAME_UNIQUE", "PRINCIPAL_INVESTIGATOR"]].sort_values(by="STUDY_ID")
        finally:
            cur.close()

    def aggregate_study_countries(self, study_ids):
        """Extracts and collapses geo-tracking layouts via semicolon serialization."""
        if not self.conn:
            raise ConnectionError("No active Snowflake connection.")
        if not study_ids:
            return pd.DataFrame(columns=["STUDY_ID", "STUDY_COUNTRY_NAME", "COUNTRY_NAME_UNIQUE", "COUNTRY_ABBREVIATION", "COUNTRY"])
            
        cur = self.conn.cursor()
        try:
            # Resolved f-string backslash parsing friction
            formatted_ids = ", ".join([f"'{uid}'" for uid in study_ids])

            query = f"""
            SELECT STUDY_NUMBER as STUDY_ID, STUDY_COUNTRY_NAME, COUNTRY_ABBREVIATION, COUNTRY 
            FROM PROD_GTED_HUB.CUR_CLIN_VEEVA_CTMS.CUR_STUDY_COUNTRY 
            WHERE STUDY_NUMBER IN ({formatted_ids})
            """
            cur.execute(query)
            df_country_raw = pd.DataFrame(cur.fetchall(), columns=["STUDY_ID", "STUDY_COUNTRY_NAME", "COUNTRY_ABBREVIATION", "COUNTRY"])

            collapse_unique = lambda s: "; ".join(s.dropna().astype(str).str.strip().replace("", None).drop_duplicates())
            collapse_all = lambda s: "; ".join(s.dropna().astype(str).str.strip().replace("", None))

            df_country = df_country_raw.groupby("STUDY_ID").agg({"STUDY_COUNTRY_NAME": collapse_all, "COUNTRY_ABBREVIATION": collapse_unique, "COUNTRY": collapse_unique}).reset_index()
            df_unique = df_country_raw.groupby("STUDY_ID")["STUDY_COUNTRY_NAME"].agg(collapse_unique).reset_index().rename(columns={"STUDY_COUNTRY_NAME": "COUNTRY_NAME_UNIQUE"})
            
            df_country = pd.merge(df_country, df_unique, on="STUDY_ID", how="left")
            return df_country[["STUDY_ID", "STUDY_COUNTRY_NAME", "COUNTRY_NAME_UNIQUE", "COUNTRY_ABBREVIATION", "COUNTRY"]].sort_values(by="STUDY_ID")
        finally:
            cur.close()

    def build_unified_master_report(self):
        """Orchestrates the data extraction pipeline and combines data layers using left outer joins."""
        # 1. Base Extraction
        df_oral_care = self.extract_clean_oral_care_studies()
        target_ids = df_oral_care["ID"].dropna().unique().tolist()
        
        # 2. Child Extractions
        df_site = self.aggregate_study_sites(target_ids)
        df_country = self.aggregate_study_countries(target_ids)
        
        # 3. Join Pipelines Safely
        df_master = pd.merge(df_oral_care, df_site, left_on="ID", right_on="STUDY_ID", how="left").drop(columns=["STUDY_ID"], errors="ignore")
        df_master = pd.merge(df_master, df_country, left_on="ID", right_on="STUDY_ID", how="left").drop(columns=["STUDY_ID"], errors="ignore")
        
        return df_master.sort_values(by="VEEVA_ID", ascending=False)

    def export_to_csv(self, dataframe, file_path="./out/veeva_master_study_report.csv"):
        """
        Safely exports a generated DataFrame to a CSV file on disk.
        Ensures target directories are created automatically and wraps headers safely.
        """
        # 1. Automatically handle directory creation if needed
        dirname = os.path.dirname(file_path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
            
        # 2. Inject standardized header comments encapsulated in spreadsheet-safe quotes
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write('\"# =========================================================================================\"\n')
            f.write('\"# VEEVA CTMS INTEGRATED MASTER STUDY REPORT\"\n')
            f.write('\"# =========================================================================================\"\n')
            f.write(f'\"# Extraction Source: PROD_GTED_HUB.CUR_CLIN_VEEVA_CTMS\"\n')
            f.write('\"# Includes compiled child alignments for study sites, investigators, and geo-countries.\"\n')
            f.write('\"# =========================================================================================\"\n\n')
            
            # 3. Append the structural dataframe content
            dataframe.to_csv(f, index=False)
            
        print(f"💾 Success! Master presentation layer table exported to: {file_path}")