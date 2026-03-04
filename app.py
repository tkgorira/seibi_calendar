from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, send_from_directory, Response
)
import sqlite3
from datetime import datetime
import os
from functools import wraps
from PIL import Image  # Pillow
import cv2  # カメラ用
import threading
import time
import requests
import numpy as np

app = Flask(__name__)
DB_PATH = "reservations.db"

# ===== 画像共有用設定 =====
BASE_DIR = r"C:\\Users\\miyos\\Documents\\seibi_calendar\\画像共有フォルダ"
THUMB_DIR = os.path.join(BASE_DIR, "_thumbs")  # サムネ保存用
app.config["UPLOAD_FOLDER"] = BASE_DIR

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "bmp"}

USERNAME = "xxxx"
PASSWORD = "xxxx"

# ===== カメラ設定 =====
CAM_URL = "http://192.168.137.97/jpg"  # （いまは使えないが一応残しておく）

SNAPSHOT_DIR = r"D:\\snapshot"
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

last_snapshot_time = None
last_snapshot_lock = threading.Lock()

# ===== 音量ステータス用（M5StickC Plus2） =====
sound_status = {"status": None, "level": None, "time": None}
sound_status_lock = threading.Lock()


# ===== DB関連 =====
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """reservations / off_days / sound_logs テーブルを作成（なければ）"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # ここは「新規DB作成用」の定義（既存DBには ALTER で列を追加しておく）
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS reservations (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            start          TEXT NOT NULL,
            end            TEXT,
            title          TEXT NOT NULL,
            mechanic       TEXT,
            content        TEXT,
            customer_name  TEXT,
            pickup_method  TEXT,
            delivery_method TEXT,
            car_model      TEXT,
            created_by     TEXT
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS off_days (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            date     TEXT NOT NULL,
            mechanic TEXT NOT NULL
        )
        """
    )

    # 音量ログ用テーブル
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sound_logs (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ts      TEXT NOT NULL,
            status  TEXT,
            level   INTEGER
        )
        """
    )

    conn.commit()
    conn.close()


# ===== Basic認証関連 =====
def check_auth(u, p):
    return u == USERNAME and p == PASSWORD


def authenticate():
    return Response(
        "Login required", 401,
        {"WWW-Authenticate": 'Basic realm="Login Required"'}
    )


def requires_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return wrapper


# ===== サムネ関連 =====
def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def ensure_thumb_dir():
    if not os.path.exists(THUMB_DIR):
        os.makedirs(THUMB_DIR, exist_ok=True)


def make_thumbnail(src_full_path, rel_path, size=(320, 240), quality=60):
    """
    src_full_path: 元画像のフルパス
    rel_path: BASE_DIR からの相対パス (例: '会社/DSC0001.JPG')
    """
    ensure_thumb_dir()

    thumb_full_path = os.path.join(THUMB_DIR, rel_path)
    thumb_dir = os.path.dirname(thumb_full_path)
    if not os.path.exists(thumb_dir):
        os.makedirs(thumb_dir, exist_ok=True)

    if os.path.exists(thumb_full_path):
        return

    try:
        img = Image.open(src_full_path)
        img.thumbnail(size)
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(thumb_full_path, format="JPEG", quality=quality, optimize=True)
    except Exception as e:
        print("Thumbnail error:", rel_path, e)


# ===== 画像アップロード／閲覧関連 =====
@app.route("/images", methods=["GET", "POST"])
@requires_auth
def images():
    """
    画像一覧＋アップロード画面
    ?dir=会社 などでサブフォルダを指定
    POST:
      - file: 画像アップロード
      - new_folder: フォルダ作成
    """
    # 表示したいサブフォルダ（空文字ならルート）
    subdir = request.args.get("dir", "").strip()

    # ベースディレクトリ＋サブディレクトリ
    current_dir = os.path.join(BASE_DIR, subdir) if subdir else BASE_DIR

    # ディレクトリが存在しなければ作成
    if not os.path.exists(current_dir):
        os.makedirs(current_dir, exist_ok=True)

    # --- フォルダ作成処理 ---
    if request.method == "POST":
        new_folder = request.form.get("new_folder", "").strip()
        if new_folder:
            safe_name = new_folder.replace("/", "").replace("\\", "")
            if safe_name:
                new_dir_path = os.path.join(BASE_DIR, safe_name)
                if not os.path.exists(new_dir_path):
                    os.makedirs(new_dir_path, exist_ok=True)

                thumb_new_dir = os.path.join(THUMB_DIR, safe_name)
                if not os.path.exists(thumb_new_dir):
                    os.makedirs(thumb_new_dir, exist_ok=True)

            return redirect(url_for("images"))

        # 画像アップロード
        file = request.files.get("file")
        if file and allowed_file(file.filename):
            filename = file.filename
            save_path = os.path.join(current_dir, filename)
            file.save(save_path)

            src_full_path = save_path
            rel_path = os.path.relpath(src_full_path, BASE_DIR).replace("\\", "/")
            make_thumbnail(src_full_path, rel_path)

            return redirect(
                url_for("images", dir=subdir) if subdir else url_for("images")
            )

    # --- フォルダ一覧（BASE_DIR直下） ---
    folder_names = []
    if os.path.exists(BASE_DIR):
        for name in os.listdir(BASE_DIR):
            full = os.path.join(BASE_DIR, name)
            if os.path.isdir(full) and name != "_thumbs":
                folder_names.append(name)
    folder_names.sort()

    # --- 現在フォルダ内の画像一覧（元画像パス） ---
    files = []
    for name in os.listdir(current_dir):
        full_path = os.path.join(current_dir, name)
        if os.path.isfile(full_path) and "." in name:
            ext = name.rsplit(".", 1)[1].lower()
            if ext in ALLOWED_EXTENSIONS:
                rel_path = os.path.relpath(full_path, BASE_DIR)
                rel_path = rel_path.replace("\\", "/")
                files.append(rel_path)

    files.sort()
    files = files[:50]  # 一覧に出す最大枚数

    return render_template(
        "images.html",
        files=files,
        folders=folder_names,
        current_dir=subdir,
    )


@app.route("/images/delete", methods=["POST"])
@requires_auth
def delete_image():
    """画像とサムネイルを削除する"""
    rel_path = request.form.get("filename", "").strip()
    if not rel_path:
        return redirect(url_for("images"))

    # 元画像とサムネのパス
    src_path = os.path.join(BASE_DIR, rel_path)
    thumb_path = os.path.join(THUMB_DIR, rel_path)

    # 簡易パス検証（../ 対策）
    norm_src = os.path.normpath(src_path)
    if not norm_src.startswith(os.path.normpath(BASE_DIR)):
        return redirect(url_for("images"))

    try:
        if os.path.exists(src_path):
            os.remove(src_path)
        if os.path.exists(thumb_path):
            os.remove(thumb_path)
    except Exception as e:
        print("delete error:", e)

    # 削除後は元いたフォルダに戻す
    subdir = ""
    if "/" in rel_path:
        subdir = rel_path.rsplit("/", 1)[0]

    return redirect(url_for("images", dir=subdir) if subdir else url_for("images"))


@app.route("/images/file/<path:filename>")
@requires_auth
def image_file(filename):
    """個別画像を返す（サブフォルダもOK／_thumbsもOK）"""
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


# ★ 画像一覧をJSONで返すAPI（スライドショー用）
@app.route("/api/images")
@requires_auth
def api_images():
    """
    クエリ:
      ?dir=会社   のように指定。
      dir が空なら BASE_DIR 直下の画像を返す。
    戻り値:
      ["会社/xxx.jpg", "会社/yyy.png", ...] のような相対パス配列
    """
    subdir = request.args.get("dir", "").strip()
    current_dir = os.path.join(BASE_DIR, subdir) if subdir else BASE_DIR

    if not os.path.exists(current_dir):
        return jsonify([])

    files = []
    for name in os.listdir(current_dir):
        full_path = os.path.join(current_dir, name)
        if os.path.isfile(full_path) and "." in name:
            ext = name.rsplit(".", 1)[1].lower()
            if ext in ALLOWED_EXTENSIONS:
                rel_path = os.path.relpath(full_path, BASE_DIR)
                rel_path = rel_path.replace("\\", "/")
                files.append(rel_path)

    files.sort()
    return jsonify(files)


# ===== カメラ用スナップショット関連 =====
def cleanup_old_snapshots(max_files=120):
    files = [
        os.path.join(SNAPSHOT_DIR, f)
        for f in os.listdir(SNAPSHOT_DIR)
        if f.lower().endswith(".jpg")
    ]
    if len(files) <= max_files:
        return
    files.sort(key=lambda p: os.path.getmtime(p))
    for p in files[:-max_files]:
        try:
            os.remove(p)
        except Exception as e:
            print("snapshot cleanup error:", e)


def snapshot_worker():
    global last_snapshot_time

    session = requests.Session()
    last_ok = 0
    consecutive_fail = 0

    while True:
        start = time.time()
        try:
            resp = session.get(CAM_URL, timeout=3)
            if resp.status_code == 200:
                data = resp.content
                img_array = np.frombuffer(data, dtype=np.uint8)
                frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

                if frame is None:
                    print("[snapshot_worker] imdecode failed")
                    consecutive_fail += 1
                else:
                    ts = datetime.now()
                    filename = ts.strftime("%Y%m%d_%H%M%S_%f")[:-4] + ".jpg"
                    full_path = os.path.join(SNAPSHOT_DIR, filename)

                    ok = cv2.imwrite(full_path, frame)
                    if not ok:
                        print("[snapshot_worker] cv2.imwrite failed:", full_path)
                        consecutive_fail += 1
                    else:
                        last_ok = time.time()
                        consecutive_fail = 0
                        with last_snapshot_lock:
                            last_snapshot_time = ts.strftime("%H:%M:%S")

                        cleanup_old_snapshots(max_files=120)
            else:
                print("[snapshot_worker] HTTP status:", resp.status_code)
                consecutive_fail += 1

        except Exception as e:
            print("[snapshot_worker] error:", e)
            consecutive_fail += 1

        if consecutive_fail >= 5:
            time.sleep(2.0)

        elapsed = time.time() - start
        sleep_time = max(0.0, 0.5 - elapsed)
        time.sleep(sleep_time)


@app.route("/latest.jpg")
@requires_auth
def latest_jpg():
    files = [
        os.path.join(SNAPSHOT_DIR, f)
        for f in os.listdir(SNAPSHOT_DIR)
        if f.lower().endswith(".jpg")
    ]
    if not files:
        return ("", 503)

    latest_path = max(files, key=lambda p: os.path.getmtime(p))

    try:
        with open(latest_path, "rb") as f:
            data = f.read()
        return Response(data, mimetype="image/jpeg")
    except Exception as e:
        print("latest_jpg error:", e)
        return ("", 500)


# 認証なし（HTML/JS 側の fetch で叩きやすくする）
@app.route("/api/snapshot_status")
def snapshot_status():
    with last_snapshot_lock:
        ts = last_snapshot_time
    return jsonify({"last_time": ts})


@app.route("/camera")
@requires_auth
def camera_page():
    html = """
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8">
      <title>入口カメラ</title>
      <style>
        body { font-family: sans-serif; background: #222; color: #eee; }
        h1 { text-align: center; }
        .wrap { text-align: center; margin-top: 20px; }
        img { max-width: 70vw; max-height: 80vh; border: 2px solid #555; }
        a, button { color: #000; }
        .btn-bar { text-align:center; margin-top:10px; }
        .btn-bar button {
          margin: 0 4px;
          padding: 4px 8px;
          border-radius: 4px;
          border: 1px solid #ccc;
          cursor: pointer;
        }
        .status-box {
          display: inline-block;
          vertical-align: top;
          margin-left: 10px;
          font-size: 0.75rem;
          color: #ccc;
          text-align: left;
        }
      </style>
    </head>
    <body>
      <h1>入口カメラ</h1>
      <div class="wrap">
        <div style="display:inline-block;">
          <img id="cam" src="/latest.jpg">
        </div>
        <div class="status-box">
          <div>Snapshot status:</div>
          <div id="status-line">waiting...</div>
        </div>
      </div>
      <div class="btn-bar">
        回転:
        <button onclick="setRotate(0)" disabled>0°</button>
        <button onclick="setRotate(90)" disabled>90°</button>
        <button onclick="setRotate(180)" disabled>180°</button>
        <button onclick="setRotate(270)" disabled>270°</button>
      </div>
      <div class="wrap" style="margin-top:20px;">
        <a href="/">← カレンダーに戻る</a>
      </div>

      <script>
        let currentAngle = 0;
        function setRotate(angle) { currentAngle = angle; }

        function forceUpdate() {
          const img = document.getElementById('cam');
          img.src = '/latest.jpg?t=' + Date.now();
        }
        setInterval(forceUpdate, 2000);

        async function updateStatus() {
          try {
            const res = await fetch('/api/snapshot_status');
            if (!res.ok) return;
            const data = await res.json();
            const line = document.getElementById('status-line');
            if (data.last_time) {
              line.textContent = 'Last: ' + data.last_time;
            } else {
              line.textContent = 'Last: -';
            }
          } catch (e) {
            const line = document.getElementById('status-line');
            line.textContent = 'status error';
          }
        }
        setInterval(updateStatus, 3000);
        updateStatus();
      </script>
    </body>
    </html>
    """
    return html


# ===== M5StickC Plus2 からの音量ステータスAPI =====
@app.route("/api/sound_status", methods=["POST"])
def api_sound_status():
    data = request.get_json() or {}
    status = data.get("status")
    level = data.get("level")

    now_full = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    now_hms = now_full[-8:]

    with sound_status_lock:
        sound_status["status"] = status
        sound_status["level"] = level
        sound_status["time"] = now_hms

    print("[sound_status]", now_full, status, level)

    conn = get_db()
    conn.execute(
        "INSERT INTO sound_logs (ts, status, level) VALUES (?, ?, ?)",
        (now_full, status, level)
    )
    conn.commit()
    conn.close()

    return jsonify({"ok": True})


@app.route("/api/sound_status_latest")
def api_sound_status_latest():
    with sound_status_lock:
        data = dict(sound_status)
    print("[sound_status_latest]", data)
    return jsonify(data)


# 直近の音量ログ（最新からlimit件）
@app.route("/api/sound_logs_recent")
def api_sound_logs_recent():
    limit = int(request.args.get("limit", 1000))
    conn = get_db()
    cur = conn.execute(
        """
        SELECT ts, status, level
        FROM sound_logs
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,)
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify(rows)


# 日付＋時間帯で検索するAPI
@app.route("/api/sound_logs_query")
def api_sound_logs_query():
    """
    ?date=2026-02-21&from=10:00&to=12:00&limit=500
    のように指定して、その範囲のログを返す。
    """
    date_str = request.args.get("date")   # "YYYY-MM-DD"
    from_str = request.args.get("from")   # "HH:MM"
    to_str   = request.args.get("to")     # "HH:MM"
    limit    = int(request.args.get("limit", 500))

    where_clauses = []
    params = []

    if date_str:
        where_clauses.append("ts LIKE ?")
        params.append(date_str + "%")

    if from_str:
        where_clauses.append("time(ts) >= time(?)")
        params.append(from_str + ":00")

    if to_str:
        where_clauses.append("time(ts) <= time(?)")
        params.append(to_str + ":59")

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    sql = f"""
        SELECT ts, status, level
        FROM sound_logs
        {where_sql}
        ORDER BY ts DESC
        LIMIT ?
    """
    params.append(limit)

    conn = get_db()
    cur = conn.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return jsonify(rows)


# ===== 入口音量モニタ画面 =====
@app.route("/sound")
def sound_page():
    return render_template("sound.html")


# ===== カレンダー系 =====
@app.route("/")
def index():
    mechanics = ["清水", "井戸", "高岸", "貝川", "社長", "駒島", "大山", "崇文", "大杉"]
    return render_template("calendar.html", mechanics=mechanics)


@app.route("/api/events", methods=["GET", "POST"])
def api_events():
    if request.method == "GET":
        conn = get_db()
        cur = conn.execute("SELECT * FROM reservations")
        events = []
        for r in cur.fetchall():
            events.append(
                {
                    "id": r["id"],
                    "title": r["title"],
                    "start": r["start"],
                    "end": r["end"],
                    "mechanic": r["mechanic"],
                    "content": r["content"],
                    "customer_name": r["customer_name"],
                    "pickup_method": r["pickup_method"],
                    "delivery_method": r["delivery_method"],
                    "car_model": r["car_model"],
                    "created_by": r["created_by"],
                }
            )
        conn.close()
        return jsonify(events)

    # POST: 新規予約登録
    data = request.json or {}

    start = data.get("start")
    title = data.get("title")
    if not start or not title:
        return jsonify({"error": "start and title are required"}), 400

    conn = get_db()
    conn.execute(
        """
        INSERT INTO reservations
        (start, end, title, mechanic, content,
         customer_name, pickup_method, delivery_method, car_model, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data["start"],
            data.get("end"),
            data["title"],
            data.get("mechanic", ""),
            data.get("content", ""),
            data.get("customer_name", ""),
            data.get("pickup_method", ""),
            data.get("delivery_method", ""),
            data.get("car_model", ""),
            data.get("created_by", ""),
        ),
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "ok"})


@app.route("/api/events/<int:event_id>", methods=["PUT", "DELETE"])
def api_event_detail(event_id):
    if request.method == "PUT":
        data = request.json or {}

        start = data.get("start")
        title = data.get("title")
        if not start or not title:
            return jsonify({"error": "start and title are required"}), 400

        conn = get_db()
        conn.execute(
            """
            UPDATE reservations
            SET start = ?, end = ?, title = ?,
                mechanic = ?, content = ?,
                customer_name = ?, pickup_method = ?, delivery_method = ?,
                car_model = ?, created_by = ?
            WHERE id = ?
            """,
            (
                data["start"],
                data.get("end"),
                data["title"],
                data.get("mechanic", ""),
                data.get("content", ""),
                data.get("customer_name", ""),
                data.get("pickup_method", ""),
                data.get("delivery_method", ""),
                data.get("car_model", ""),
                data.get("created_by", ""),
                event_id,
            ),
        )
        conn.commit()
        conn.close()
        return jsonify({"status": "updated"})

    if request.method == "DELETE":
        conn = get_db()
        conn.execute("DELETE FROM reservations WHERE id = ?", (event_id,))
        conn.commit()
        conn.close()
        return jsonify({"status": "deleted"})


@app.route("/api/off_days", methods=["GET"])
def api_off_days():
    date = request.args.get("date")
    if not date:
        return jsonify({"error": "date is required"}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT mechanic FROM off_days WHERE date = ?", (date,))
    rows = cur.fetchall()
    conn.close()

    offs = [r["mechanic"] for r in rows]
    return jsonify(offs)


@app.route("/api/off_days", methods=["POST"])
def api_off_days_add():
    data = request.get_json() or {}
    date = data.get("date")
    mechanics = data.get("mechanics", [])

    if not date:
        return jsonify({"error": "date is required"}), 400

    conn = get_db()
    cur = conn.cursor()

    cur.execute("DELETE FROM off_days WHERE date = ?", (date,))

    for mech in mechanics:
        cur.execute(
            "INSERT INTO off_days (date, mechanic) VALUES (?, ?)",
            (date, mech),
        )

    conn.commit()
    conn.close()

    return jsonify({"status": "ok", "date": date, "mechanics": mechanics})


@app.route("/api/off_days/one", methods=["DELETE"])
def api_off_days_delete_one():
    data = request.get_json() or {}
    date = data.get("date")
    mechanic = data.get("mechanic")

    if not date or not mechanic:
        return jsonify({"error": "date and mechanic are required"}), 400

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM off_days WHERE date = ? AND mechanic = ?",
        (date, mechanic),
    )
    conn.commit()
    conn.close()

    return jsonify({"status": "deleted", "date": date, "mechanic": mechanic})


if __name__ == "__main__":
    init_db()
    # カメラスナップショット用バックグラウンドスレッド
    t = threading.Thread(target=snapshot_worker, daemon=True)
    t.start()

    app.run(host="0.0.0.0", port=5000, debug=True)
