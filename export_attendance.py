import pandas as pd
from database import get_connection

conn = get_connection()

query = "SELECT * FROM attendance"

df = pd.read_sql(query, conn)

df["attendance_date"] = df["attendance_date"].astype(str)

df["attendance_time"] = (
    df["attendance_time"]
    .astype(str)
    .str.replace("0 days ", "", regex=False)
)

df.to_excel(
    "attendance_report.xlsx",
    index=False
)

print("Excel Report Generated Successfully")
print(df)