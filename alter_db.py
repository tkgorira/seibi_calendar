import sqlite3

DB_PATH = "reservations.db"

def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 既に列があるかどうかチェックして、なければ追加する
    cur.execute("PRAGMA table_info(reservations)")
    cols = [row[1] for row in cur.fetchall()]  # row[1] が列名

    if "pickup_method" not in cols:
        print("pickup_method 列を追加します")
        cur.execute("ALTER TABLE reservations ADD COLUMN pickup_method TEXT")

    if "delivery_method" not in cols:
        print("delivery_method 列を追加します")
        cur.execute("ALTER TABLE reservations ADD COLUMN delivery_method TEXT")

    conn.commit()
    conn.close()
    print("完了しました")

if __name__ == "__main__":
    main()
