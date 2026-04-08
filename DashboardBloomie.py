import pandas as pd
import gspread
import numpy as np
from google.oauth2.credentials import Credentials
from datetime import datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build
from tenacity import retry, stop_after_attempt, wait_fixed

# ============================================================
# DATE FOR FILE NAME
# ============================================================
file_date = (datetime.today() - timedelta(days=1)).strftime('%Y%m%d')

df = pd.read_csv(rf'MovingItemsBloomie20260401.csv', sep=';', encoding='utf-16', skiprows=[0,2])

# ============================================================
# HANDLE YYYY-MM (2025-11, 2026-01)
# ============================================================
# CSV column "Month" contains YYYY-MM
df['YearMonth'] = pd.to_datetime(df['Month'], format='%Y-%m')

df['Year'] = df['YearMonth'].dt.year
df['Month'] = df['YearMonth'].dt.month

# ============================================================
# BUILD FULL YEAR-MONTH RANGE
# ============================================================
all_periods = pd.period_range(
    start=df['YearMonth'].min(),
    end=df['YearMonth'].max(),
    freq='M'
)

period_df = pd.DataFrame({
    'Year': all_periods.year,
    'Month': all_periods.month
})

# ============================================================
# EXPAND ITEMS × YEAR-MONTH
# ============================================================
unique_keys = df[['Item_No.', 'Kategori', 'Type', 'SubItem']].drop_duplicates()

full_index = pd.MultiIndex.from_product(
    [unique_keys.index, period_df.index],
    names=['item_idx', 'period_idx']
)

full_data = unique_keys.loc[full_index.get_level_values('item_idx')].copy()
full_data['Year'] = period_df.loc[full_index.get_level_values('period_idx'), 'Year'].values
full_data['Month'] = period_df.loc[full_index.get_level_values('period_idx'), 'Month'].values

# ============================================================
# MERGE WITH ORIGINAL DATA
# ============================================================
merged = pd.merge(
    full_data,
    df,
    on=['Item_No.', 'Kategori', 'Type', 'SubItem', 'Year', 'Month'],
    how='left'
)

# ============================================================
# FILL MISSING NUMERIC VALUES
# ============================================================
numeric_cols = [
    'TotalQtySales', 'TotalValueSales', 'TotalCost',
    'AvgPriceRMB', 'TotalQtyGRPO', 'TotalQtyRetur',
    'TotalQtyAdjustment'
]

for col in numeric_cols:
    if col in merged.columns:
        merged[col] = pd.to_numeric(merged[col], errors='coerce').fillna(0)

# ============================================================
# MANUAL, ADA ERROR STOCK
# ============================================================

merged.loc[
    (merged["Item_No."] == "WS-GKC-1810-0019") &
    (merged["Year"] == 2025) &
    (merged["Month"] == 8),
    "TotalQtyGRPO"
] = 180000

merged.loc[
    (merged["Item_No."] == "WS-GKC-1803-0030") &
    (merged["Year"] == 2025) &
    (merged["Month"] == 9),
    "TotalQtyGRPO"
] = 32400

merged.loc[
    (merged["Item_No."] == "WS-MSK-2590C") &
    (merged["Year"] == 2025) &
    (merged["Month"] == 8),
    "TotalQtyGRPO"
] = 5000

merged.loc[
    (merged["Item_No."] == "WS-MSK-2590C1") &
    (merged["Year"] == 2025) &
    (merged["Month"] == 8),
    "TotalQtyGRPO"
] = 5000

merged.loc[
    (merged["Item_No."] == "WS-MSK-2590D") &
    (merged["Year"] == 2025) &
    (merged["Month"] == 8),
    "TotalQtyGRPO"
] = 5000

merged.loc[
    (merged["Item_No."] == "WS-MSK-2590D1") &
    (merged["Year"] == 2025) &
    (merged["Month"] == 8),
    "TotalQtyGRPO"
] = 5000

merged.loc[
    (merged["Item_No."] == "WS-MSK-2590D2") &
    (merged["Year"] == 2025) &
    (merged["Month"] == 8),
    "TotalQtyGRPO"
] = 5000

merged.loc[
    (merged["Item_No."] == "WS-MSK-2590E") &
    (merged["Year"] == 2025) &
    (merged["Month"] == 8),
    "TotalQtyGRPO"
] = 5000

# ============================================================
# CALCULATIONS
# ============================================================
merged['GRPO+ADJ'] = merged['TotalQtyGRPO'] + merged['TotalQtyAdjustment']
merged['Sales-Retur'] = merged['TotalQtySales'] - merged['TotalQtyRetur']

merged['Cummulative Balance'] = (
    merged['TotalQtyGRPO']
    - merged['TotalQtySales']
    + merged['TotalQtyAdjustment']
    + merged['TotalQtyRetur']
)

# IMPORTANT: sort by Year + Month
merged = merged.sort_values(
    by=['Item_No.', 'Year', 'Month']
).reset_index(drop=True)

merged['StockOnHand'] = (
    merged.groupby('Item_No.')['Cummulative Balance'].cumsum()
)

# ============================================================
# LAST UPDATED
# ============================================================
merged['Last Updated'] = datetime.today().strftime('%Y%m%d')

# ============================================================
# SUMMARY
# ============================================================
summary = merged.groupby('Item_No.').agg({
    'GRPO+ADJ': 'sum',
    'Sales-Retur': 'sum'
}).reset_index()

summary['In Stock'] = summary['GRPO+ADJ'] - summary['Sales-Retur']
summary['%Terjual'] = np.where(
    summary['GRPO+ADJ'] == 0,
    0,
    summary['Sales-Retur'] / summary['GRPO+ADJ']
)

merged = merged.merge(
    summary[['Item_No.', '%Terjual']],
    on='Item_No.',
    how='left'
)

merged['YearMonth'] = pd.to_datetime(
    merged['Year'].astype(str) + '-' + merged['Month'].astype(str) + '-01'
)

# ============================================================
# RESHAPE FOR SHEET 2
# ============================================================
result = pd.melt(
    summary,
    id_vars='Item_No.',
    value_vars=['GRPO+ADJ', 'Sales-Retur', 'In Stock'],
    var_name='Notes',
    value_name='Value'
)

note_map = {
    'GRPO+ADJ': 'Pembelian',
    'Sales-Retur': 'Penjualan',
    'In Stock': 'In Stock'
}
result['Notes'] = result['Notes'].map(note_map)
result = result[result['Notes'].isin(['Penjualan', 'In Stock'])]

# ============================================================
# CLEAN FOR GOOGLE SHEETS
# ============================================================
def sheets_safe(df):
    df = df.copy()

    # Replace NaN, inf, -inf
    df = df.replace([np.nan, np.inf, -np.inf], "")

    # Convert everything to native Python types
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]) \
           or pd.api.types.is_period_dtype(df[col]):
            df[col] = df[col].astype(str)

    return df


merged = sheets_safe(merged)
result = sheets_safe(result)

merged_data = [merged.columns.tolist()] + merged.values.tolist()
result_data = [result.columns.tolist()] + result.values.tolist()

# ============================================================
# GOOGLE SHEETS
# ============================================================
SERVICE_ACCOUNT_FILE = r'api2.json'
SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/spreadsheets'
]

credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)

SPREADSHEET = "1vnrGdCGJ-DJNHJoLaPyLxwe7wS2gW9aj42BFVWL8RCk"

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def update_sheet(spreadsheet_id, range_name, data):
    service = build('sheets', 'v4', credentials=credentials)

    service.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=range_name
    ).execute()

    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_name + "!A1",
        valueInputOption='USER_ENTERED',
        body={"values": data}
    ).execute()

update_sheet(SPREADSHEET, "Sheet1", merged_data)
update_sheet(SPREADSHEET, "Sheet2", result_data)