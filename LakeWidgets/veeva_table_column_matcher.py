import os
import numpy as np
import pandas as pd
import snowflake.connector

class VeevaTableColumnMatcher:
    def __init__(self, user_id=None):
        # Fallback to an environment variable, or force the user to provide it
        import os
        self.user_id = user_id or os.environ.get("SNOWFLAKE_USER")
        
        if not self.user_id:
            raise ValueError("❌ Initialization Failed: A valid corporate user_id must be provided.")
        self.conn = None
        
    def connect(self):
        """Initializes the Snowflake session via SSO and sets environment contexts."""
        print(f"Initializing global Snowflake connection via SSO for {self.user_id}...")
        self.conn = snowflake.connector.connect(
            user=self.user_id,
            account="colgatepalmoliveprod.us-central1.gcp",
            authenticator="externalbrowser",
        )
        
        # Set database context
        cursor = self.conn.cursor()
        cursor.execute("USE DATABASE PROD_GTED_HUB")
        cursor.execute("USE SCHEMA CUR_CLIN_VEEVA_CTMS")
        cursor.close()
        print("✅ Snowflake Veeva CTMS profiling connection active.")
        return self.conn

    def _write_csv_with_comments(self, file_path, dataframe, include_index=False):
        """Helper method to inject standardized header comments safely wrapped in quotes to prevent cell splitting."""
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write('\"# =========================================================================================\"\n')
            f.write('\"# DATA LAKE MATRIX CHEATSHEET & INTERPRETATION KEY\"\n')
            f.write('\"# =========================================================================================\"\n')
            f.write('\"# EQUAL     -> Perfect matching domains. Example: Table1|Col1: [A,B,C] and Table2|Col2: [A,B,C]\"\n')
            f.write('\"# INCLUDED  -> Source is a smaller child subset. Example: Table1|Col1: [A,B] inside Table2|Col2: [A,B,C,D]\"\n')
            f.write('\"# CONTAINS  -> Source is a broader master directory. Example: Table1|Col1: [A,B,C,D] holds Table2|Col2: [A,B]\"\n')
            f.write('\"# PARTIAL   -> Intersects Broadly. Example: Table1|Col1: [A,B,C,D] shares keys with Table2|Col2: [C,D,E,F]\"\n')
            f.write('\"# =========================================================================================\"\n\n')
            
            dataframe.to_csv(f, index=include_index)

    def compute_overlap_matrix(self, scan_limit=50000, prefix_output="./out/veeva_ctms"):
        """
        HYBRID SPEED ENGINE: Pulls unique value domains over the network exactly ONCE, 
        caching them locally. All cross-table intersection matrices are then calculated 
        locally in local memory.
        """
        if not self.conn:
            raise ConnectionError("No active Snowflake connection. Call .connect() first.")
            
        dirname = os.path.dirname(prefix_output)
        if dirname:
            os.makedirs(dirname, exist_ok=True)
            
        cursor = self.conn.cursor()
        
        # Step 1: Ultra-fast Table Caching via Native SHOW Command
        print("📖 Fetching master Veeva table list via native metadata cache...")
        cursor.execute("SHOW TABLES IN SCHEMA PROD_GTED_HUB.CUR_CLIN_VEEVA_CTMS")
        tables = [row[1] for row in cursor.fetchall()]
        print(f"📋 Found {len(tables)} Veeva tables to process.")

        EXCLUDED_COLUMNS = {
            'GLOBAL_ID', 'DAG_ID', 'DAG_RUN_ID', 'CREATED_BY', 
            'CREATED_DATE', 'UPDATED_BY', 'UPDATED_DATE', 
            'MODIFIED_BY', 'LAST_MODIFIED_BY'
        }
        
        all_columns_metadata = []

        # Step 2: Extract data field definitions instantly
        print("🔍 Scanning structural column layouts...")
        for table in tables:
            try:
                cursor.execute(f"SHOW COLUMNS IN TABLE PROD_GTED_HUB.CUR_CLIN_VEEVA_CTMS.{table}")
                for row in cursor.fetchall():
                    col_name = row[2]
                    data_type_raw = row[3]
                    is_valid_type = any(t in data_type_raw.upper() for t in ['TEXT', 'VARCHAR', 'NUMBER', 'INT', 'FIXED'])
                    
                    if is_valid_type and col_name.upper() not in EXCLUDED_COLUMNS:
                        all_columns_metadata.append((table, col_name, f"{table}|{col_name}"))
            except Exception as e:
                print(f"   ⚠️ Skipping structure scan for {table}: {str(e)}")
                continue

        # Step 3: Extract unique value domains EXACTLY ONCE per attribute
        column_data_pools = {}
        total_fields = len(all_columns_metadata)
        print(f"\n⚡ Caching value sets locally. Downloading distinct value pools for {total_fields} fields...")
        
        for idx, (t, c, key) in enumerate(all_columns_metadata, 1):
            print(f"   📥 [{idx}/{total_fields}] Fetching unique sample baseline: {key}...")
            try:
                cursor.execute(f"""
                    SELECT DISTINCT TO_VARCHAR({c}) 
                    FROM {t} 
                    WHERE {c} IS NOT NULL 
                    LIMIT {scan_limit}
                """)
                values = {row[0] for row in cursor.fetchall() if row[0] is not None}
                if len(values) > 0:
                    column_data_pools[key] = values
            except Exception as e:
                print(f"      ⚠️ Skipping values for {key} due to query friction: {str(e)}")
                continue

        cursor.close()

        # Step 4: Run local comparisons on the cached sets in memory
        print("\n🧠 Computing set intersections and line mappings in local memory...")
        active_keys = list(column_data_pools.keys())
        matrix_size = len(active_keys)
        matrix_df = pd.DataFrame("", index=active_keys, columns=active_keys)

        for i in range(matrix_size):
            for j in range(matrix_size):
                key_a, key_b = active_keys[i], active_keys[j]

                if key_a == key_b:
                    matrix_df.loc[key_a, key_b] = "EQUAL"
                    continue

                set_a = column_data_pools[key_a]
                set_b = column_data_pools[key_b]

                overlap = len(set_a.intersection(set_b))
                if not overlap:
                    continue

                rate_a_in_b = overlap / len(set_a)
                rate_b_in_a = overlap / len(set_b)

                if rate_a_in_b >= 0.95 and rate_b_in_a >= 0.95:
                    relationship = "EQUAL"
                elif rate_a_in_b >= 0.95:
                    relationship = "INCLUDED"
                elif rate_b_in_a >= 0.95:
                    relationship = "CONTAINS"
                elif rate_a_in_b >= 0.50 or rate_b_in_a >= 0.50:
                    relationship = "PARTIAL"
                else:
                    continue

                matrix_df.loc[key_a, key_b] = relationship

        # Densify output
        is_blank = (matrix_df == "")
        keep_indices = (~is_blank).sum(axis=1) > 1
        original_matrix = matrix_df.loc[keep_indices, keep_indices]

        # --- ARTIFACTs Generation ---
        original_csv = f"{prefix_output}_original_matrix.csv"
        self._write_csv_with_comments(original_csv, original_matrix, include_index=True)

        split_df = original_matrix.reset_index().rename(columns={'index': 'SOURCE_KEY'})
        split_df[['Table', 'Colname']] = split_df['SOURCE_KEY'].str.split('|', n=1, expand=True)
        split_df = split_df.drop(columns=['SOURCE_KEY'])
        cols_ordered = ['Table', 'Colname'] + [c for c in split_df.columns if c not in ['Table', 'Colname']]
        split_matrix = split_df[cols_ordered]
        
        split_csv = f"{prefix_output}_split_matrix.csv"
        self._write_csv_with_comments(split_csv, split_matrix, include_index=False)

        catalog_records = []
        for key_a in active_keys:
            t_a, c_a = key_a.split('|', 1)
            equals_list, included_list, contains_list, partials_list = [], [], [], []
            
            for key_b in active_keys:
                if key_a == key_b:
                    continue
                rel = matrix_df.loc[key_a, key_b]
                if rel == "EQUAL": equals_list.append(key_b)
                elif rel == "INCLUDED": included_list.append(key_b)
                elif rel == "CONTAINS": contains_list.append(key_b)
                elif rel == "PARTIAL": partials_list.append(key_b)
            
            if equals_list or included_list or contains_list or partials_list:
                catalog_records.append({
                    "Table": t_a,
                    "Colname": c_a,
                    "Identical Matches (EQUAL)": ", ".join(equals_list) if equals_list else "None",
                    "Is Subset Of / Child (INCLUDED)": ", ".join(included_list) if included_list else "None",
                    "Is Parent Of / Master (CONTAINS)": ", ".join(contains_list) if contains_list else "None",
                    "Intersects Broadly (Partial Value Overlap)": ", ".join(partials_list) if partials_list else "None"
                })
                
        human_catalog = pd.DataFrame(catalog_records)
        if human_catalog.empty:
            human_catalog = pd.DataFrame(columns=["Table", "Colname", "Identical Matches (EQUAL)", "Is Subset Of / Child (INCLUDED)", "Is Parent Of / Master (CONTAINS)", "Intersects Broadly (Partial Value Overlap)"])

        catalog_csv = f"{prefix_output}_human_readable_catalog.csv"
        self._write_csv_with_comments(catalog_csv, human_catalog, include_index=False)
        
        print("💾 Success! All three compressed summary sheets exported directly to `./out` path.")
        return original_matrix, split_matrix, human_catalog