import os
import pandas as pd
import snowflake.connector


class VeevaSubjectProfiler:

    def __init__(self, user_id=None):
        self.user_id = user_id or os.environ.get("SNOWFLAKE_USER")
        if not self.user_id:
            raise ValueError(
                "❌ Initialization Failed: A valid corporate user_id must be provided."
            )
        self.conn = None

    def connect(self):
        """Initializes Snowflake session via SSO."""
        print(f"Initializing Snowflake connection for {self.user_id}...")
        self.conn = snowflake.connector.connect(
            user=self.user_id,
            account="colgatepalmoliveprod.us-central1.gcp",
            authenticator="externalbrowser",
        )
        cursor = self.conn.cursor()
        cursor.execute("USE DATABASE PROD_GTED_HUB")
        cursor.execute("USE SCHEMA CUR_CLIN_VEEVA_CTMS")
        cursor.close()
        return self.conn

    def fetch_cur_subject_records(self):
        """Extracts and formats clinical subject attributes and demographics."""
        if not self.conn:
            raise ConnectionError(
                "No active Snowflake connection. Call .connect() first."
            )

        query = """
        SELECT 
            subj.ID AS SUBJECT_ID,
            subj.VEEVA_SUBJECT_NUMBER,
            subj.STUDY AS STUDY_ID,
            ctry.STUDY_COUNTRY_NAME,
            subj.ARM,
            REPLACE(subj.GENDER, '__c', '') AS GENDER,
            CASE 
                WHEN LOWER(subj.BIOLOGICAL_SEX) = 'male_c' THEN 'male'
                WHEN LOWER(subj.BIOLOGICAL_SEX) = 'female_c' THEN 'female'
                ELSE REPLACE(subj.BIOLOGICAL_SEX, '__c', '')
            END AS BIOLOGICAL_SEX,
            REPLACE(subj.RACE, '__c', '') AS RACE,
            REPLACE(subj.ETHNICITY, '__c', '') AS ETHNICITY,
            subj.SUBJECT_AGE,
            CASE 
                WHEN LOWER(subj.BIRTH_MONTH_MM) = 'xy_c' THEN 'xy'
                ELSE REPLACE(subj.BIRTH_MONTH_MM, '__c', '')
            END AS BIRTH_MONTH_MM,
            subj.BIRTH_YEAR_YYYY,
            INITCAP(
                REGEXP_REPLACE(
                    REPLACE(
                        REGEXP_REPLACE(subj.LIFECYCLE_STATE, '_state|__state|__c|__v', ''), 
                        '_', ' '
                    ), 
                    '([a-z])([A-Z])', '\\1 \\2'
                )
            ) AS LIFECYCLE_STATE,
            REPLACE(REGEXP_REPLACE(subj.SUBJECT_STATUS, '_clin|__clin|__v|__c', ''), '_', ' ') AS SUBJECT_STATUS,
            TO_VARCHAR(subj.ENROLLED_DATE, 'YYYY-MM-DD') AS ENROLLED_DATE,
            TO_VARCHAR(subj.DID_NOT_QUALIFY_DATE, 'YYYY-MM-DD') AS DID_NOT_QUALIFY_DATE,
            TO_VARCHAR(subj.WITHDRAWN_DROPOUT_DATE, 'YYYY-MM-DD') AS WITHDRAWN_DROPOUT_DATE,
            REPLACE(subj.CURRENT_PHYSICAL_HEALTH, '__c', '') AS CURRENT_PHYSICAL_HEALTH,
            subj.PREGNANT,
            subj.NURSING_OR_BREASTFEEDING,
            subj.AILMENTS_AND_ILLNESSES,
            subj.DESCRIPTION_OF_ILLNESSES_AND_AILMENTS AS DESCRIPTION_OF_AILMENTS_AND_ILLNESSES,
            subj.SERIOUS_OPERATIONS, 
            subj.SERIOUS_OPERATIONS_LIST,
            REPLACE(subj.FREQUENCY_OF_DENTAL_VISITS, '__c', '') AS FREQUENCY_OF_DENTAL_VISITS
        FROM PROD_GTED_HUB.CUR_CLIN_VEEVA_CTMS.CUR_SUBJECT subj
        LEFT JOIN PROD_GTED_HUB.CUR_CLIN_VEEVA_CTMS.CUR_STUDY_COUNTRY ctry
            ON subj.STUDY_COUNTRY = ctry.ID
        """
        cur = self.conn.cursor()
        try:
            cur.execute(query)
            cols = [desc[0].upper() for desc in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=cols)
            return df
        finally:
            cur.close()

    def compute_summary_metrics(self, df):
        """Computes high-level KPI figures."""
        total_subjects = len(df)
        total_studies = (
            df["STUDY_ID"].nunique() if "STUDY_ID" in df.columns else 0
        )
        total_countries = (
            df["STUDY_COUNTRY_NAME"].nunique()
            if "STUDY_COUNTRY_NAME" in df.columns
            else 0
        )
        return total_subjects, total_studies, total_countries

    def compute_categorical_distributions(self, df):
        """Generates itemized frequency tables for target categorical attributes."""
        cat_columns = [
            "LIFECYCLE_STATE",
            "SUBJECT_STATUS",
            "GENDER",
            "BIOLOGICAL_SEX",
            "RACE",
            "ETHNICITY",
            "CURRENT_PHYSICAL_HEALTH",
            "PREGNANT",
            "NURSING_OR_BREASTFEEDING",
            "FREQUENCY_OF_DENTAL_VISITS",
        ]
        total_subjects = len(df)
        distributions = {}

        for col in cat_columns:
            if col in df.columns:
                counts = (
                    df[col]
                    .fillna("[NULL / UNASSIGNED]")
                    .value_counts()
                    .reset_index()
                )
                counts.columns = [col, "COUNT"]
                counts["PERCENTAGE_%"] = (
                    (counts["COUNT"] / (total_subjects if total_subjects > 0 else 1)) * 100
                ).round(1)
                distributions[col] = counts

        return distributions

    def compute_completeness_diagnostic(self, df):
        """Calculates column fill-rates to isolate low-coverage metadata fields."""
        null_counts = df.isnull().sum()
        total_subjects = len(df)
        denom = total_subjects if total_subjects > 0 else 1
        return pd.DataFrame({
            "COLUMN": null_counts.index,
            "NULL_COUNT": null_counts.values,
            "FILL_RATE_%": (
                (1 - (null_counts.values / denom)) * 100
            ).round(1),
        }).sort_values("FILL_RATE_%", ascending=True)
