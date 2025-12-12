import pandas as pd
import gspread
import numpy as np
from google.oauth2.credentials import Credentials
import datetime
from datetime import datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build
from tenacity import retry, stop_after_attempt, wait_fixed

today = datetime.today() - timedelta(days=1)
today = today.strftime('%Y%m%d')

df = pd.read_csv(rf'S:\MovingItemsBloomie\MovingItemsBloomie{today}.csv', sep=';', encoding='utf-16', skiprows=[0,2])

# Define the full set of months you expect
all_months = list(range(1, 13))  # months 1 to 12

# Get unique combinations of identifying columns (e.g., per item)
unique_keys = df[['Item_No.', 'Kategori', 'Type', 'SubItem']].drop_duplicates()

# Build full index: each unique item × each month
full_index = pd.MultiIndex.from_product(
    [unique_keys.index, all_months],
    names=['item_idx', 'Month']
)

# Expand the base info for each item to match all months
full_data = unique_keys.loc[full_index.get_level_values('item_idx')].copy()
full_data['Month'] = full_index.get_level_values('Month')

# Ensure 'Month' is int in both dataframes
full_data['Month'] = full_data['Month'].astype(int)
df['Month'] = df['Month'].astype(int)

# Merge with original data
merged = pd.merge(
    full_data,
    df,
    on=['Item_No.', 'Kategori', 'Type', 'SubItem', 'Month'],
    how='left'
)

# Fill missing numeric values with 0
numeric_cols = ['TotalQtySales', 'TotalValueSales', 'TotalCost', 'AvgPriceRMB', 'TotalQtyGRPO', 'TotalQtyRetur']
merged[numeric_cols] = merged[numeric_cols].fillna(0)

# Optional: sort by Item and Month
merged = merged.sort_values(by=['Item_No.', 'Month']).reset_index(drop=True)

# Make sure the needed columns are numeric
merged['TotalQtyGRPO'] = pd.to_numeric(merged['TotalQtyGRPO'], errors='coerce')
merged['TotalQtySales'] = pd.to_numeric(merged['TotalQtySales'], errors='coerce')
merged['TotalQtyRetur'] = pd.to_numeric(merged['TotalQtyRetur'], errors='coerce')
merged['TotalQtyAdjustment'] = pd.to_numeric(merged['TotalQtyAdjustment'], errors='coerce').fillna(0)
merged['GRPO+ADJ'] = merged['TotalQtyGRPO'] + merged['TotalQtyAdjustment']
merged['Sales-Retur'] = merged['TotalQtySales'] - merged['TotalQtyRetur']

# Create the new column
merged['Cummulative Balance'] = merged['TotalQtyGRPO'] - merged['TotalQtySales'] + merged['TotalQtyAdjustment'] + merged['TotalQtyRetur']

# Then group by 'Item_No.' and calculate the cumulative sum of these changes
merged['StockOnHand'] = merged.groupby('Item_No.')['Cummulative Balance'].cumsum()

today = datetime.today()
today = today.strftime('%Y%m%d')

merged['Last Updated'] = today

# Grouping by Item_No.
summary = merged.groupby('Item_No.').agg({
    'GRPO+ADJ': 'sum',
    'Sales-Retur': 'sum'
}).reset_index()

# Calculating In Stock
summary['In Stock'] = summary['GRPO+ADJ'] - summary['Sales-Retur']
summary['%Terjual'] = summary['Sales-Retur'] / summary['GRPO+ADJ']

merged = merged.merge(summary[['Item_No.', '%Terjual']], on='Item_No.', how='left')

# Reshaping to desired format
result = pd.melt(
    summary,
    id_vars='Item_No.',
    value_vars=['GRPO+ADJ', 'Sales-Retur', 'In Stock'],
    var_name='Notes',
    value_name='Value'
)

# Renaming Notes values
note_map = {
    'GRPO+ADJ': 'Pembelian',
    'Sales-Retur': 'Penjualan',
    'In Stock': 'In Stock'
}
result['Notes'] = result['Notes'].map(note_map)

result = result[result['Notes'].isin(['Penjualan', 'In Stock'])]

# Replace NaN with empty string
cleaned = merged.fillna("")
cleaned2 = result.fillna("")

# Include headers + values as list of lists
merged = [cleaned.columns.tolist()] + cleaned.values.tolist()
result = [cleaned2.columns.tolist()] + cleaned2.values.tolist()

SERVICE_ACCOUNT_FILE = r'projectgraphdashboard.json'
SCOPES = ['https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/spreadsheets']
credentials = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
# Autentikasi menggunakan gspread
client = gspread.authorize(credentials)
sheet = client.open_by_key("165G6N5_DRpe55fo0pYvYvmYLepynV_nmZ2ZgQhQHbl0")
worksheet1 = sheet.get_worksheet(0)
worksheet2 = sheet.get_worksheet(1)

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def update_sheet(spreadsheet_id, range_name, data):
    try:
        service = build('sheets', 'v4', credentials=credentials)
        print(f"Membersihkan data di {spreadsheet_id} - {range_name}...")

        #Membersihkan Data
        service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=range_name
        ).execute()

        #Update Data
        print(f"Memperbarui data di {spreadsheet_id} - {range_name}...")
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=range_name + "!A1",
            valueInputOption='USER_ENTERED',
            body={"values": data}
        ).execute()
        print("Update berhasil!")

    except Exception as e:
        print(f"Terjadi error saat update ke {spreadsheet_id} - {range_name}: {e}")
        raise

SPREADSHEET="165G6N5_DRpe55fo0pYvYvmYLepynV_nmZ2ZgQhQHbl0"
update_sheet(SPREADSHEET, "Sheet1", merged)
update_sheet(SPREADSHEET, "Sheet2", result)