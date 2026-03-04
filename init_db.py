# init_db.py
import sqlite3

DB_PATH = "reservations.db"

def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 既存テーブルにカラムを追加（なければ追加、あればスキップ）
    for col in ["customer_name", "delivery_type", "created_by"]:
        try:
            cur.execute(f"ALTER TABLE reservations ADD COLUMN {col} TEXT;")
            print(f"added column: {col}")
        except sqlite3.OperationalError as e:
            # すでにカラムがある場合など
            print(f"skip column {col}: {e}")

    conn.commit()
    conn.close()
    print("done.")

if __name__ == "__main__":
    main()
