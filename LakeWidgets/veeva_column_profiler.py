import os
import pandas as pd
import snowflake.connector

class VeevaColumnProfiler:
    def __init__(self, user_id=None):
        """
        Initializes the profiler. Fetches the user identity from an environment
        variable or forces manual instantiation at the notebook orchestration layer.
        """
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

    def profile_schema_columns(self, sample_value_limit=20):
        """
        Profiles every column in the schema by pushing aggregation mathematics
        directly to Snowflake. Computes null footprints, fill-rates, and distinct value arrays.
        
        Features rule overrides to print out ALL distinct values regardless of count if:
        - The column matches CUR_SUBJECT_VISIT_MODULE_DATA_POINT.SUBJECT_VISIT_MODULE_DATAPOINT_NAME
        - The column's distinct values contain statistical words (mean, average, max, min).
        """
        if not self.conn:
            raise ConnectionError("No active Snowflake connection. Call .connect() first.")
            
        cursor = self.conn.cursor()
        
        # Step 1: Rapid Table Metadata Fetching via Caching Layers
        print("📖 Fetching active schema tables via native metadata cache...")
        cursor.execute("SHOW TABLES IN SCHEMA PROD_GTED_HUB.CUR_CLIN_VEEVA_CTMS")
        tables = [row[1] for row in cursor.fetchall()]
        print(f"📋 Found {len(tables)} target tables to profile.")

        # Standard technical metadata exclusions
        EXCLUDED_COLUMNS = {
            'ID', 'GLOBAL_ID', 'DAG_ID', 'DAG_RUN_ID', 'CREATED_BY', 
            'CREATED_DATE', 'UPDATED_BY', 'UPDATED_DATE', 
            'MODIFIED_BY', 'LAST_MODIFIED_BY'
        }
        
        all_columns_metadata = []

        # Step 2: Map structural column definitions
        print("🔍 Scanning structural column profiles...")
        for table in tables:
            try:
                cursor.execute(f"SHOW COLUMNS IN TABLE PROD_GTED_HUB.CUR_CLIN_VEEVA_CTMS.{table}")
                for row in cursor.fetchall():
                    col_name = row[2]
                    data_type_raw = row[3]
                    is_valid_type = any(t in data_type_raw.upper() for t in ['TEXT', 'VARCHAR', 'NUMBER', 'INT', 'FIXED', 'DATE', 'TIMESTAMP'])
                    
                    if is_valid_type and col_name.upper() not in EXCLUDED_COLUMNS:
                        all_columns_metadata.append((table, col_name, data_type_raw))
            except Exception as e:
                print(f"   ⚠️ Skipping structural map for {table}: {str(e)}")
                continue

        profile_records = []
        total_fields = len(all_columns_metadata)
        print(f"\n⚡ Executing compute pushdowns for {total_fields} attributes...")

        # Step 3: Run Pushdown Profiling Mathematics per Attribute
        for idx, (t, c, dt) in enumerate(all_columns_metadata, 1):
            print(f"   📊 [{idx}/{total_fields}] Analyzing column: {t} | {c}...")
            
            # Determine if the column holds character string elements
            is_string_type = any(s in dt.upper() for s in ['TEXT', 'VARCHAR', 'CHAR', 'STRING'])
            
            # Formulate the targeting rule matching strings
            normalized_table = t.upper().strip()
            normalized_column = c.upper().strip()
            
            # Target Override 1: Hardcoded target column matching check
            is_target_column_override = (
                normalized_table == "CUR_SUBJECT_VISIT_MODULE_DATA_POINT" and 
                normalized_column == "SUBJECT_VISIT_MODULE_DATAPOINT_NAME"
            )
            
            try:
                # Type-aware Boundary Profiling: Text tracks LENGTH, numeric/dates track VALUE
                if is_string_type:
                    metrics_query = f"""
                    SELECT 
                        COUNT(*) as total_rows,
                        COUNT({c}) as populated_rows,
                        SUM(CASE WHEN {c} IS NULL OR TO_VARCHAR({c}) = '' THEN 1 ELSE 0 END) as blank_or_null_rows,
                        COUNT(DISTINCT {c}) as unique_count,
                        TO_VARCHAR(MIN(LENGTH({c}))) as min_boundary,
                        TO_VARCHAR(MAX(LENGTH({c}))) as max_boundary
                    FROM {t}
                    """
                else:
                    metrics_query = f"""
                    SELECT 
                        COUNT(*) as total_rows,
                        COUNT({c}) as populated_rows,
                        SUM(CASE WHEN {c} IS NULL OR TO_VARCHAR({c}) = '' THEN 1 ELSE 0 END) as blank_or_null_rows,
                        COUNT(DISTINCT {c}) as unique_count,
                        TO_VARCHAR(MIN({c})) as min_boundary,
                        TO_VARCHAR(MAX({c})) as max_boundary
                    FROM {t}
                    """
                
                cursor.execute(metrics_query)
                total_rows, pop_rows, null_rows, unique_count, min_b, max_b = cursor.fetchone()
                
                # Check for completely empty datasets to avoid ZeroDivisionError
                if total_rows == 0:
                    fill_rate = 0.0
                else:
                    fill_rate = round((pop_rows / total_rows) * 100, 2)
                
                # Format boundary presentation labels
                min_label = f"{min_b} chars" if is_string_type and min_b is not None else min_b
                max_label = f"{max_b} chars" if is_string_type and max_b is not None else max_b
                
                # Dynamic Sample Extraction Pass
                sample_string = "None [Empty Column]"
                if unique_count > 0:
                    # Target Override 2: Pre-check if any distinct values match stats keywords in Snowflake
                    contains_stats_keywords = False
                    if is_string_type:
                        check_query = f"""
                        SELECT EXISTS (
                            SELECT 1 FROM {t} 
                            WHERE LOWER({c}) LIKE '%mean%' 
                               OR LOWER({c}) LIKE '%average%' 
                               OR LOWER({c}) LIKE '%max%' 
                               OR LOWER({c}) LIKE '%min%'
                        )
                        """
                        cursor.execute(check_query)
                        contains_stats_keywords = bool(cursor.fetchone()[0])

                    # Evaluate if this field triggers a total-print out rule
                    trigger_unlimited_print = is_target_column_override or contains_stats_keywords
                    
                    if trigger_unlimited_print:
                        # Pull everything without any LIMIT constraint
                        sample_query = f"""
                        SELECT DISTINCT TO_VARCHAR({c}) as val 
                        FROM {t} 
                        WHERE {c} IS NOT NULL AND TO_VARCHAR({c}) != ''
                        """
                    else:
                        # Pull standard restricted safety boundaries
                        sample_query = f"""
                        SELECT DISTINCT TO_VARCHAR({c}) as val 
                        FROM {t} 
                        WHERE {c} IS NOT NULL AND TO_VARCHAR({c}) != '' 
                        LIMIT {sample_value_limit}
                        """
                    
                    cursor.execute(sample_query)
                    samples = [str(row[0]) for row in cursor.fetchall() if row[0] is not None]
                    
                    # Layout formatting engine based on rules triggered
                    if trigger_unlimited_print:
                        override_reason = "[Column Override]" if is_target_column_override else "[Stats Keyword Match]"
                        sample_string = f"{override_reason} ALL values: " + ", ".join(samples)
                    elif unique_count <= sample_value_limit:
                        sample_string = ", ".join(samples)
                    else:
                        remaining_count = unique_count - len(samples)
                        sample_string = ", ".join(samples) + f" ... [+ {remaining_count} more unique values]"

                profile_records.append({
                    "Table Name": t,
                    "Column Name": c,
                    "Data Type": dt,
                    "Total Dataset Rows": total_rows,
                    "Populated Rows": pop_rows,
                    "Null/Blank Footprint": null_rows,
                    "Data Fill Rate (%)": f"{fill_rate}%",
                    "Distinct Values Count": unique_count,
                    "Minimum Boundary (Value/Len)": min_label if min_label is not None else "N/A",
                    "Maximum Boundary (Value/Len)": max_label if max_label is not None else "N/A",
                    "Distinct Values Sample": sample_string
                })

            except Exception as e:
                print(f"      ⚠️ Compute execution skipped on {t}|{c}: {str(e)}")
                continue

        cursor.close()
        return pd.DataFrame(profile_records)

    def export_targeted_distinct_values(self, target_mapping, output_dir="./out"):
        """
        Extracts, prints, and writes EVERY single distinct value for a set of specific
        tables and columns to individual .txt files.
        
        Args:
            target_mapping (dict): Format -> {"TABLE_NAME": ["COLUMN_A", "COLUMN_B"]}
            output_dir (str): Base folder path to save the generated text logs.
        """
        if not self.conn:
            raise ConnectionError("No active Snowflake connection. Call .connect() first.")
            
        os.makedirs(output_dir, exist_ok=True)
        cursor = self.conn.cursor()
        
        print(f"\n🎯 Initiating deep-dive targeted distinct extraction for {len(target_mapping)} tables...")
        
        for table_name, columns in target_mapping.items():
            for column_name in columns:
                t_clean = table_name.strip().upper()
                c_clean = column_name.strip().upper()
                
                print(f"   🔓 Pulling comprehensive unique domain values for: {t_clean} ➔ {c_clean}...")
                
                try:
                    # Execute complete distinct retrieval query with zero boundary limitations
                    query = f"""
                    SELECT DISTINCT TO_VARCHAR({c_clean}) as val 
                    FROM {t_clean} 
                    WHERE {c_clean} IS NOT NULL AND TO_VARCHAR({c_clean}) != ''
                    ORDER BY val ASC
                    """
                    cursor.execute(query)
                    distinct_values = [str(row[0]) for row in cursor.fetchall() if row[0] is not None]
                    
                    # 1. Print directly out to terminal console for rapid local validation
                    print(f"      📝 Found {len(distinct_values)} absolute unique items:")
                    if distinct_values:
                        for val in distinct_values[:15]: # Show first 15 in logs as a validation snapshot
                            print(f"         • {val}")
                        if len(distinct_values) > 15:
                            print(f"         ... [+ {len(distinct_values) - 15} more printed to text file]")
                    else:
                        print("         • [No populated strings found]")
                    
                    # 2. Structure name layout and save as a physical flat text file
                    file_name = f"distinct_values_of_table_{t_clean.lower()}_column_{c_clean.lower()}.txt"
                    file_path = os.path.join(output_dir, file_name)
                    
                    with open(file_path, "w", encoding="utf-8") as text_file:
                        text_file.write(f"# =========================================================================\n")
                        text_file.write(f"# FULL UNTRUNCATED DISTINCT VALUE DOMAIN LIST\n")
                        text_file.write(f"# Table Scope:  PROD_GTED_HUB.CUR_CLIN_VEEVA_CTMS.{t_clean}\n")
                        text_file.write(f"# Column Scope: {c_clean}\n")
                        text_file.write(f"# Total Count:  {len(distinct_values)} unique items\n")
                        text_file.write(f"# =========================================================================\n\n")
                        
                        for val in distinct_values:
                            text_file.write(f"{val}\n")
                            
                    print(f"      💾 Successfully saved complete array straight to: {file_path}")
                    
                except Exception as e:
                    print(f"      ⚠️ Failed extraction execution on targeting path {t_clean}.{c_clean}: {str(e)}")
                    continue
                    
        cursor.close()

    def export_dictionary(self, dataframe, file_path="./out/veeva_data_dictionary.csv"):
        """Safely saves the data dictionary layout to disk with spreadsheet-safe comments."""
        dirname = os.path.dirname(file_path)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
            
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write('\"# =========================================================================================\"\n')
            f.write('\"# VEEVA SYSTEM ECOSYSTEM AUTOMATED DATA DICTIONARY\"\n')
            f.write('\"# =========================================================================================\"\n')
            f.write('\"# Core Governance Tracking Matrix: Profiles null bounds, fill-rates, and data archetypes.\"\n')
            f.write('\"# Rules Override: Specified target columns and fields with stats phrases print full sets.\"\n')
            f.write('\"# Text columns display character length bounds; numeric/date columns display true value bounds.\"\n')
            f.write('\"# =========================================================================================\"\n\n')
            
            dataframe.to_csv(f, index=False)
            
        print(f"💾 Success! Automated Data Dictionary compiled and written to: {file_path}")