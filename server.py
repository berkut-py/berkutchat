# -*- coding: utf-8 -*-
"""
BerkutChat Server v3.9
+ Рамки аватарок (avatar_frame, avatar_frame_colors)
"""
import os
import json
import time
import shutil
import base64
import zipfile
import threading
import logging
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory

logging.getLogger('werkzeug').setLevel(logging.ERROR)

# ============================================================
# КОНФИГ
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(BASE_DIR, "users.json")
ROOMS_FILE = os.path.join(BASE_DIR, "rooms.json")
HISTORY_DIR = os.path.join(BASE_DIR, "history")
REACTIONS_FILE = os.path.join(BASE_DIR, "reactions.json")
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
CHUNKS_DIR = os.path.join(BASE_DIR, "chunks")
STICKERS_DIR = os.path.join(BASE_DIR, "stickers")
PACKS_DIR = os.path.join(STICKERS_DIR, "packs")
UPLOADS_META = os.path.join(BASE_DIR, "uploads_meta.json")

MAX_HISTORY = 200
MAX_FILE_SIZE = 100 * 1024 * 1024 * 1024
CHUNK_SIZE = 75 * 1024 * 1024
CHUNK_TTL_HOURS = 24
MAX_STICKER_SIZE = 20 * 1024 * 1024

os.makedirs(HISTORY_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(CHUNKS_DIR, exist_ok=True)
os.makedirs(STICKERS_DIR, exist_ok=True)
os.makedirs(PACKS_DIR, exist_ok=True)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = CHUNK_SIZE + 10 * 1024 * 1024

# ============================================================
# ХРАНИЛИЩЕ
# ============================================================
users = {}
rooms = {}
history = {}
online = {}
queues = {}
reactions_store = {}
profiles = {}
uploads_meta = {}
packs = []

lock = threading.RLock()


def load_data():
    global users, rooms, history, reactions_store, profiles, uploads_meta
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                users = data.get("users", {})
                profiles = data.get("profiles", {})
        except Exception:
            users = {}
            profiles = {}
    if os.path.exists(ROOMS_FILE):
        try:
            with open(ROOMS_FILE, "r", encoding="utf-8") as f:
                rooms = json.load(f)
        except Exception:
            rooms = {}
    if os.path.exists(REACTIONS_FILE):
        try:
            with open(REACTIONS_FILE, "r", encoding="utf-8") as f:
                reactions_store = json.load(f)
        except Exception:
            reactions_store = {}
    if os.path.exists(UPLOADS_META):
        try:
            with open(UPLOADS_META, "r", encoding="utf-8") as f:
                uploads_meta = json.load(f)
        except Exception:
            uploads_meta = {}
    for room_id in rooms.keys():
        path = os.path.join(HISTORY_DIR, room_id + ".json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    history[room_id] = json.load(f)
            except Exception:
                history[room_id] = []
        else:
            history[room_id] = []


def save_users():
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump({"users": users, "profiles": profiles},
                  f, ensure_ascii=False, indent=2)


def save_rooms():
    with open(ROOMS_FILE, "w", encoding="utf-8") as f:
        json.dump(rooms, f, ensure_ascii=False, indent=2)


def save_history(room_id):
    path = os.path.join(HISTORY_DIR, room_id + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history.get(room_id, []), f, ensure_ascii=False, indent=2)


def save_reactions():
    with open(REACTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(reactions_store, f, ensure_ascii=False, indent=2)


def save_uploads_meta():
    with open(UPLOADS_META, "w", encoding="utf-8") as f:
        json.dump(uploads_meta, f, ensure_ascii=False, indent=2)


def safe_extract_zip(zip_path, dest_dir):
    with zipfile.ZipFile(zip_path, "r") as z:
        for member in z.namelist():
            member_path = os.path.join(dest_dir, member)
            abs_dest = os.path.abspath(dest_dir)
            abs_member = os.path.abspath(member_path)
            if not abs_member.startswith(abs_dest):
                continue
            z.extract(member, dest_dir)


def scan_stickers():
    global packs
    try:
        packs = []
        allowed_img = (".gif", ".webp", ".png", ".jpg", ".jpeg")
        if not os.path.exists(STICKERS_DIR):
            return

        for fname in sorted(os.listdir(STICKERS_DIR)):
            if not fname.lower().endswith(".zip"):
                continue
            zip_path = os.path.join(STICKERS_DIR, fname)
            if not os.path.isfile(zip_path):
                continue

            pack_name = os.path.splitext(fname)[0]
            pack_dir = os.path.join(PACKS_DIR, pack_name)

            need_extract = True
            if os.path.exists(pack_dir):
                try:
                    zip_mtime = os.path.getmtime(zip_path)
                    dir_mtime = os.path.getmtime(pack_dir)
                    if dir_mtime > zip_mtime:
                        need_extract = False
                except Exception:
                    pass

            if need_extract:
                try:
                    if os.path.exists(pack_dir):
                        shutil.rmtree(pack_dir)
                    os.makedirs(pack_dir, exist_ok=True)
                    safe_extract_zip(zip_path, pack_dir)
                except Exception as e:
                    print("[ZIP ERROR] " + str(e))
                    continue

            pack_stickers = []
            cover_url = None

            for root, dirs, files in os.walk(pack_dir):
                for sf in sorted(files):
                    if not sf.lower().endswith(allowed_img):
                        continue
                    full = os.path.join(root, sf)
                    try:
                        size = os.path.getsize(full)
                        if size > MAX_STICKER_SIZE:
                            continue
                        ext = os.path.splitext(sf)[1].lower()
                        stype = "gif" if ext == ".gif" else (
                            "webp" if ext == ".webp" else "png"
                        )
                        rel = os.path.relpath(full, STICKERS_DIR).replace("\\", "/")
                        url = "/stickers/" + rel
                        base = os.path.splitext(sf)[0].lower()
                        if base == "cover" and cover_url is None:
                            cover_url = url
                            continue
                        pack_stickers.append({
                            "name": sf, "url": url,
                            "type": stype, "size": size,
                        })
                    except Exception:
                        pass

            if not pack_stickers:
                continue
            if not cover_url:
                cover_url = pack_stickers[0]["url"]

            packs.append({
                "name": pack_name, "cover_url": cover_url,
                "count": len(pack_stickers), "stickers": pack_stickers,
            })

        # Файлы в корне
        root_files = []
        for fname in sorted(os.listdir(STICKERS_DIR)):
            fpath = os.path.join(STICKERS_DIR, fname)
            if not os.path.isfile(fpath):
                continue
            if not fname.lower().endswith(allowed_img):
                continue
            try:
                size = os.path.getsize(fpath)
                if size > MAX_STICKER_SIZE:
                    continue
                ext = os.path.splitext(fname)[1].lower()
                stype = "gif" if ext == ".gif" else (
                    "webp" if ext == ".webp" else "png"
                )
                root_files.append({
                    "name": fname, "url": "/stickers/" + fname,
                    "type": stype, "size": size,
                })
            except Exception:
                pass

        if root_files:
            packs.append({
                "name": "Разное", "cover_url": root_files[0]["url"],
                "count": len(root_files), "stickers": root_files,
            })

        total = sum(p["count"] for p in packs)
        print("[STICKERS] Паков: " + str(len(packs)) + ", стикеров: " + str(total))
    except Exception as e:
        print("[STICKERS ERROR] " + str(e))
        packs = []


def cleanup_old_chunks():
    try:
        now = time.time()
        ttl = CHUNK_TTL_HOURS * 3600
        if not os.path.exists(CHUNKS_DIR):
            return
        for folder in os.listdir(CHUNKS_DIR):
            fpath = os.path.join(CHUNKS_DIR, folder)
            if os.path.isdir(fpath):
                try:
                    mtime = os.path.getmtime(fpath)
                    if now - mtime > ttl:
                        shutil.rmtree(fpath)
                        if folder in uploads_meta:
                            del uploads_meta[folder]
                except Exception:
                    pass
        save_uploads_meta()
    except Exception as e:
        print("[CLEANUP ERROR] " + str(e))


load_data()
cleanup_old_chunks()
scan_stickers()

if not rooms:
    rooms["general"] = {
        "id": "general", "name": "Общий", "type": "text",
        "private": False, "password": "", "owner": "system",
    }
    save_rooms()

if "Ярослав" in users:
    if "Ярослав" not in profiles:
        profiles["Ярослав"] = {}
    profiles["Ярослав"]["is_premium"] = True
    profiles["Ярослав"]["is_dev"] = True
    profiles["Ярослав"]["nick_rgb"] = True
    if "nick_colors" not in profiles["Ярослав"]:
        profiles["Ярослав"]["nick_colors"] = ["#ff0000", "#ff7f00", "#ffff00",
                                                "#00ff00", "#00bfff", "#0000ff", "#8a2be2"]
    save_users()


def now_str():
    return datetime.now().strftime("%H:%M:%S")


def push_message(target, msg):
    with lock:
        if target not in queues:
            queues[target] = []
        queues[target].append(msg)


def push_broadcast(msg):
    with lock:
        for name in list(queues.keys()):
            queues[name].append(msg)


def get_default_profile():
    return {
        "avatar_b64": "",
        "description": "",
        "birthday": "",
        "banner_b64": "",
        "is_premium": False,
        "is_dev": False,
        "nick_rgb": False,
        "nick_colors": None,
        # НОВЫЕ ПОЛЯ:
        "avatar_frame": "none",           # тип рамки
        "avatar_frame_colors": None,      # цвета рамки (список)
    }


def format_size(b):
    for unit in ["Б", "КБ", "МБ", "ГБ", "ТБ"]:
        if b < 1024:
            return "{:.1f} {}".format(b, unit)
        b /= 1024
    return "{:.1f} ПБ".format(b)


def decode_b64_header(val):
    if not val:
        return ""
    try:
        return base64.b64decode(val.encode("ascii")).decode("utf-8")
    except Exception:
        return ""


# ============================================================
# API
# ============================================================
@app.route("/")
def index():
    total_stickers = sum(p["count"] for p in packs)
    return jsonify({
        "status": "online",
        "name": "BerkutChat Server",
        "version": "3.9",
        "users_total": len(users),
        "profiles_total": len(profiles),
        "rooms_total": len(rooms),
        "online_count": len(online),
        "uploads_total": len(uploads_meta),
        "packs_total": len(packs),
        "stickers_total": total_stickers,
        "max_file_size": MAX_FILE_SIZE,
        "chunk_size": CHUNK_SIZE,
    })


@app.route("/register", methods=["POST"])
def register():
    data = request.get_json()
    name = data.get("name", "").strip()
    password = data.get("password", "")
    if not name or not password:
        return jsonify({"ok": False, "error": "Имя и пароль обязательны"}), 400
    if len(name) < 2 or len(name) > 20:
        return jsonify({"ok": False, "error": "Имя от 2 до 20 символов"}), 400
    with lock:
        if name in users:
            return jsonify({"ok": False, "error": "Имя занято"}), 400
        users[name] = {"password": password, "created": now_str()}
        profiles[name] = get_default_profile()
        if name in ("Ярослав", "Ярик"):
            profiles[name]["is_premium"] = True
            profiles[name]["is_dev"] = True
            profiles[name]["nick_rgb"] = True
            profiles[name]["nick_colors"] = ["#ff0000", "#ff7f00", "#ffff00",
                                             "#00ff00", "#00bfff", "#0000ff", "#8a2be2"]
        save_users()
    return jsonify({"ok": True, "name": name})


@app.route("/login", methods=["POST"])
def login():
    data = request.get_json()
    name = data.get("name", "").strip()
    password = data.get("password", "")
    if name not in users:
        return jsonify({"ok": False, "error": "Пользователь не найден"}), 401
    if users[name]["password"] != password:
        return jsonify({"ok": False, "error": "Неверный пароль"}), 401
    with lock:
        online[name] = time.time()
        if name not in queues:
            queues[name] = []
        if name not in profiles:
            profiles[name] = get_default_profile()
            save_users()
    push_broadcast({"type": "system",
                    "message": name + " присоединился",
                    "time": now_str()})
    return jsonify({"ok": True, "name": name})


@app.route("/logout", methods=["POST"])
def logout():
    data = request.get_json()
    name = data.get("name", "").strip()
    with lock:
        if name in online:
            del online[name]
        if name in queues:
            del queues[name]
    push_broadcast({"type": "system",
                    "message": name + " покинул чат",
                    "time": now_str()})
    return jsonify({"ok": True})


# ============================================================
# СТИКЕРЫ
# ============================================================
@app.route("/stickers/packs", methods=["POST"])
def stickers_packs_api():
    data = request.get_json()
    name = data.get("name", "").strip()
    password = data.get("password", "")
    if name not in users or users[name]["password"] != password:
        return jsonify({"ok": False, "error": "Неверный логин"}), 401
    packs_short = []
    for p in packs:
        packs_short.append({
            "name": p["name"], "cover_url": p["cover_url"], "count": p["count"],
        })
    return jsonify({"ok": True, "packs": packs_short})


@app.route("/stickers/pack", methods=["POST"])
def stickers_pack_api():
    data = request.get_json()
    name = data.get("name", "").strip()
    password = data.get("password", "")
    pack_name = data.get("pack_name", "").strip()
    if name not in users or users[name]["password"] != password:
        return jsonify({"ok": False, "error": "Неверный логин"}), 401
    for p in packs:
        if p["name"] == pack_name:
            return jsonify({"ok": True, "pack": p})
    return jsonify({"ok": False, "error": "Пак не найден"}), 404


@app.route("/stickers/reload", methods=["POST"])
def stickers_reload():
    data = request.get_json()
    name = data.get("name", "").strip()
    password = data.get("password", "")
    if name not in users or users[name]["password"] != password:
        return jsonify({"ok": False, "error": "Неверный логин"}), 401
    if name not in ("Ярослав", "Ярик"):
        return jsonify({"ok": False, "error": "Только админ"}), 403
    scan_stickers()
    packs_short = []
    for p in packs:
        packs_short.append({
            "name": p["name"], "cover_url": p["cover_url"], "count": p["count"],
        })
    return jsonify({"ok": True, "packs": packs_short})


@app.route("/stickers/<path:file_path>")
def stickers_file(file_path):
    if ".." in file_path or file_path.startswith("/"):
        return jsonify({"ok": False, "error": "Плохое имя"}), 400
    safe_path = file_path.replace("\\", "/")
    full_path = os.path.join(STICKERS_DIR, safe_path)
    if not os.path.exists(full_path) or not os.path.isfile(full_path):
        return jsonify({"ok": False, "error": "Не найден"}), 404
    directory = os.path.dirname(full_path)
    fname = os.path.basename(full_path)
    return send_from_directory(directory, fname)


@app.route("/send_sticker", methods=["POST"])
def send_sticker():
    data = request.get_json()
    name = data.get("name", "").strip()
    password = data.get("password", "")
    sticker_url = data.get("sticker_url", "").strip()
    room = data.get("room", "general")

    if name not in users or users[name]["password"] != password:
        return jsonify({"ok": False, "error": "Неверный логин"}), 401
    if not sticker_url:
        return jsonify({"ok": False, "error": "Нет стикера"}), 400
    if not sticker_url.startswith("/stickers/"):
        return jsonify({"ok": False, "error": "Неверный URL"}), 400

    rel_path = sticker_url[len("/stickers/"):]
    if ".." in rel_path:
        return jsonify({"ok": False, "error": "Плохое имя"}), 400

    full_path = os.path.join(STICKERS_DIR, rel_path.replace("/", os.sep))
    if not os.path.exists(full_path):
        return jsonify({"ok": False, "error": "Файл не найден"}), 404

    ext = os.path.splitext(full_path)[1].lower()
    stype = "gif" if ext == ".gif" else ("webp" if ext == ".webp" else "png")

    msg_id = name + "_sticker_" + str(int(time.time() * 1000))
    msg = {
        "type": "sticker", "from": name, "text": "", "time": now_str(),
        "room": room, "msg_id": msg_id, "reactions": {}, "deleted_for": [],
        "sticker_name": os.path.basename(full_path),
        "sticker_url": sticker_url, "sticker_type": stype,
    }

    if room not in history:
        history[room] = []
    history[room].append(msg)
    if len(history[room]) > MAX_HISTORY:
        history[room] = history[room][-MAX_HISTORY:]
    save_history(room)

    push_broadcast(msg)
    return jsonify({"ok": True, "msg_id": msg_id})


# ============================================================
# ПРОФИЛИ
# ============================================================
@app.route("/profile/get", methods=["POST"])
def profile_get():
    data = request.get_json()
    name = data.get("name", "").strip()
    target = data.get("target", "").strip()
    password = data.get("password", "")
    if name not in users or users[name]["password"] != password:
        return jsonify({"ok": False, "error": "Неверный логин"}), 401
    if not target:
        target = name
    if target not in profiles:
        profiles[target] = get_default_profile()
        save_users()
    return jsonify({
        "ok": True,
        "profile": profiles.get(target, get_default_profile()),
        "name": target,
    })


@app.route("/profile/update", methods=["POST"])
def profile_update():
    """Обновление профиля. Теперь принимает avatar_frame и avatar_frame_colors."""
    data = request.get_json()
    name = data.get("name", "").strip()
    password = data.get("password", "")

    if name not in users or users[name]["password"] != password:
        return jsonify({"ok": False, "error": "Неверный логин"}), 401

    if name not in profiles:
        profiles[name] = get_default_profile()

    # ⚡ РАСШИРЕННЫЙ СПИСОК ПОЛЕЙ
    for key in ["avatar_b64", "description", "birthday", "banner_b64",
                "avatar_frame", "avatar_frame_colors"]:
        if key in data:
            profiles[name][key] = data[key]

    save_users()

    push_broadcast({
        "type": "profile_update",
        "name": name,
        "profile": profiles[name],
    })

    print("[PROFILE] " + name + " обновил профиль. Рамка: " +
          str(profiles[name].get("avatar_frame", "none")))

    return jsonify({"ok": True, "profile": profiles[name]})


@app.route("/profile/set_premium", methods=["POST"])
def profile_set_premium():
    data = request.get_json()
    admin = data.get("admin", "").strip()
    password = data.get("password", "")
    target = data.get("target", "").strip()
    field = data.get("field", "")
    value = data.get("value", False)

    if admin not in users or users[admin]["password"] != password:
        return jsonify({"ok": False, "error": "Неверный логин"}), 401
    if admin not in ("Ярослав", "Ярик"):
        return jsonify({"ok": False, "error": "Только админ"}), 403
    if target not in profiles:
        profiles[target] = get_default_profile()

    if field in ("is_premium", "nick_rgb", "is_dev"):
        profiles[target][field] = bool(value)
        save_users()
        push_broadcast({
            "type": "profile_update",
            "name": target,
            "profile": profiles[target],
        })
        return jsonify({"ok": True, "profile": profiles[target]})

    return jsonify({"ok": False, "error": "Неверное поле"})


@app.route("/profile/update_colors", methods=["POST"])
def profile_update_colors():
    data = request.get_json()
    admin = data.get("name", "").strip()
    password = data.get("password", "")
    target = data.get("target", "").strip()
    colors = data.get("colors", [])
    if admin not in users or users[admin]["password"] != password:
        return jsonify({"ok": False, "error": "Неверный логин"}), 401
    if admin not in ("Ярослав", "Ярик"):
        return jsonify({"ok": False, "error": "Только админ"}), 403
    if target not in profiles:
        profiles[target] = get_default_profile()
    profiles[target]["nick_colors"] = colors
    save_users()
    push_broadcast({
        "type": "profile_update",
        "name": target,
        "profile": profiles[target],
    })
    return jsonify({"ok": True, "profile": profiles[target]})


@app.route("/profile/set_frame", methods=["POST"])
def profile_set_frame():
    """Админ может выдать рамку другому юзеру"""
    data = request.get_json()
    admin = data.get("admin", "").strip()
    password = data.get("password", "")
    target = data.get("target", "").strip()
    frame = data.get("frame", "none")
    colors = data.get("colors", None)

    if admin not in users or users[admin]["password"] != password:
        return jsonify({"ok": False, "error": "Неверный логин"}), 401
    if admin not in ("Ярослав", "Ярик"):
        return jsonify({"ok": False, "error": "Только админ"}), 403
    if target not in profiles:
        profiles[target] = get_default_profile()

    profiles[target]["avatar_frame"] = frame
    if colors is not None:
        profiles[target]["avatar_frame_colors"] = colors
    save_users()

    push_broadcast({
        "type": "profile_update",
        "name": target,
        "profile": profiles[target],
    })

    return jsonify({"ok": True, "profile": profiles[target]})


@app.route("/profiles/all", methods=["POST"])
def profiles_all():
    data = request.get_json()
    name = data.get("name", "").strip()
    password = data.get("password", "")
    if name not in users or users[name]["password"] != password:
        return jsonify({"ok": False, "error": "Неверный логин"}), 401
    return jsonify({"ok": True, "profiles": profiles})


# ============================================================
# CHUNK UPLOAD
# ============================================================
@app.route("/upload/init", methods=["POST"])
def upload_init():
    data = request.get_json()
    name = data.get("name", "").strip()
    password = data.get("password", "")
    file_name = data.get("file_name", "file")
    file_size = int(data.get("file_size", 0))
    file_type = data.get("file_type", "application/octet-stream")
    room = data.get("room", "general")

    if name not in users or users[name]["password"] != password:
        return jsonify({"ok": False, "error": "Неверный логин"}), 401
    if file_size <= 0:
        return jsonify({"ok": False, "error": "Неверный размер"}), 400
    if file_size > MAX_FILE_SIZE:
        return jsonify({
            "ok": False,
            "error": "Файл больше " + format_size(MAX_FILE_SIZE)
        }), 400

    file_id = "file_" + str(int(time.time() * 1000)) + "_" + name.replace(" ", "_")
    total_chunks = (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE
    chunk_dir = os.path.join(CHUNKS_DIR, file_id)
    os.makedirs(chunk_dir, exist_ok=True)

    with lock:
        uploads_meta[file_id] = {
            "file_id": file_id, "file_name": file_name, "file_size": file_size,
            "file_type": file_type, "uploader": name, "room": room,
            "total_chunks": total_chunks, "received_chunks": [],
            "created": now_str(), "created_ts": time.time(), "status": "uploading",
        }
        save_uploads_meta()

    return jsonify({
        "ok": True, "file_id": file_id,
        "total_chunks": total_chunks, "chunk_size": CHUNK_SIZE,
    })


@app.route("/upload/chunk", methods=["POST"])
def upload_chunk():
    name = decode_b64_header(request.headers.get("X-User-B64", ""))
    password = decode_b64_header(request.headers.get("X-Pass-B64", ""))
    file_id = request.args.get("file_id", "").strip()
    try:
        chunk_index = int(request.args.get("chunk_index", "0"))
    except ValueError:
        chunk_index = 0

    if name not in users or users[name]["password"] != password:
        return jsonify({"ok": False, "error": "Неверный логин"}), 401
    if not file_id or file_id not in uploads_meta:
        return jsonify({"ok": False, "error": "Неизвестный file_id"}), 404

    meta = uploads_meta[file_id]
    if meta.get("uploader") != name:
        return jsonify({"ok": False, "error": "Не твой файл"}), 403

    chunk_data = request.get_data()
    if not chunk_data:
        return jsonify({"ok": False, "error": "Пустой чанк"}), 400

    chunk_dir = os.path.join(CHUNKS_DIR, file_id)
    os.makedirs(chunk_dir, exist_ok=True)
    chunk_path = os.path.join(chunk_dir, "chunk_{:06d}".format(chunk_index))
    with open(chunk_path, "wb") as f:
        f.write(chunk_data)

    with lock:
        if chunk_index not in meta["received_chunks"]:
            meta["received_chunks"].append(chunk_index)
        save_uploads_meta()

    return jsonify({
        "ok": True, "chunk_index": chunk_index,
        "received": len(meta["received_chunks"]),
        "total": meta["total_chunks"],
    })


@app.route("/upload/finish", methods=["POST"])
def upload_finish():
    data = request.get_json()
    name = data.get("name", "").strip()
    password = data.get("password", "")
    file_id = data.get("file_id", "").strip()
    room = data.get("room", "general")

    if name not in users or users[name]["password"] != password:
        return jsonify({"ok": False, "error": "Неверный логин"}), 401
    if file_id not in uploads_meta:
        return jsonify({"ok": False, "error": "Неизвестный file_id"}), 404

    meta = uploads_meta[file_id]
    if meta.get("uploader") != name:
        return jsonify({"ok": False, "error": "Не твой файл"}), 403

    total = meta["total_chunks"]
    received = meta["received_chunks"]
    if len(received) < total:
        missing = [i for i in range(total) if i not in received]
        return jsonify({"ok": False, "error": "Не все чанки",
                        "missing": missing[:10]}), 400

    chunk_dir = os.path.join(CHUNKS_DIR, file_id)
    final_name = file_id + "_" + meta["file_name"].replace(" ", "_")
    final_path = os.path.join(UPLOADS_DIR, final_name)

    try:
        with open(final_path, "wb") as out:
            for i in range(total):
                chunk_path = os.path.join(chunk_dir, "chunk_{:06d}".format(i))
                if not os.path.exists(chunk_path):
                    return jsonify({"ok": False, "error": "Пропал чанк"}), 500
                with open(chunk_path, "rb") as cf:
                    while True:
                        buf = cf.read(1024 * 1024)
                        if not buf:
                            break
                        out.write(buf)
    except Exception as e:
        return jsonify({"ok": False, "error": "Ошибка склейки"}), 500

    try:
        shutil.rmtree(chunk_dir)
    except Exception:
        pass

    with lock:
        meta["status"] = "done"
        meta["final_name"] = final_name
        meta["finished"] = now_str()
        save_uploads_meta()

    msg_id = name + "_file_" + str(int(time.time() * 1000))
    msg = {
        "type": "file", "from": name, "text": "", "time": now_str(),
        "room": room, "msg_id": msg_id, "reactions": {}, "deleted_for": [],
        "file_id": file_id, "file_name": meta["file_name"],
        "file_size": meta["file_size"], "file_type": meta["file_type"],
        "file_url": "/uploads/" + final_name,
    }
    if room not in history:
        history[room] = []
    history[room].append(msg)
    if len(history[room]) > MAX_HISTORY:
        history[room] = history[room][-MAX_HISTORY:]
    save_history(room)
    push_broadcast(msg)
    return jsonify({"ok": True, "file_url": msg["file_url"], "msg_id": msg_id})


@app.route("/upload/cancel", methods=["POST"])
def upload_cancel():
    data = request.get_json()
    name = data.get("name", "").strip()
    password = data.get("password", "")
    file_id = data.get("file_id", "").strip()
    if name not in users or users[name]["password"] != password:
        return jsonify({"ok": False, "error": "Неверный логин"}), 401
    if file_id not in uploads_meta:
        return jsonify({"ok": False, "error": "Неизвестный file_id"}), 404
    chunk_dir = os.path.join(CHUNKS_DIR, file_id)
    try:
        if os.path.exists(chunk_dir):
            shutil.rmtree(chunk_dir)
    except Exception:
        pass
    with lock:
        uploads_meta.pop(file_id, None)
        save_uploads_meta()
    return jsonify({"ok": True})


@app.route("/uploads/<file_name>")
def download_file(file_name):
    if ".." in file_name or "/" in file_name or "\\" in file_name:
        return jsonify({"ok": False, "error": "Плохое имя"}), 400
    file_path = os.path.join(UPLOADS_DIR, file_name)
    if not os.path.exists(file_path):
        return jsonify({"ok": False, "error": "Файл не найден"}), 404
    return send_from_directory(UPLOADS_DIR, file_name, as_attachment=True)


# ============================================================
# СООБЩЕНИЯ
# ============================================================
@app.route("/send", methods=["POST"])
def send():
    data = request.get_json()
    name = data.get("name", "").strip()
    password = data.get("password", "")
    if name not in users or users[name]["password"] != password:
        return jsonify({"ok": False, "error": "Неверный логин"}), 401
    text = data.get("text", "").strip()
    room = data.get("room", "general")
    if not text:
        return jsonify({"ok": False, "error": "Пустое сообщение"})
    msg_id = name + "_" + str(int(time.time() * 1000))
    msg = {
        "type": "message", "from": name, "text": text, "time": now_str(),
        "room": room, "msg_id": msg_id, "reactions": {}, "deleted_for": [],
    }
    if room not in history:
        history[room] = []
    history[room].append(msg)
    if len(history[room]) > MAX_HISTORY:
        history[room] = history[room][-MAX_HISTORY:]
    save_history(room)
    push_broadcast(msg)
    return jsonify({"ok": True, "msg_id": msg_id})


@app.route("/delete_message", methods=["POST"])
def delete_message():
    data = request.get_json()
    name = data.get("name", "").strip()
    password = data.get("password", "")
    msg_id = data.get("msg_id", "").strip()
    mode = data.get("mode", "all")
    room = data.get("room", "general")
    if name not in users or users[name]["password"] != password:
        return jsonify({"ok": False, "error": "Неверный логин"}), 401
    if not msg_id:
        return jsonify({"ok": False, "error": "Нет msg_id"})
    with lock:
        msgs = history.get(room, [])
        target_msg = None
        target_idx = -1
        for i, m in enumerate(msgs):
            if m.get("msg_id") == msg_id:
                target_msg = m
                target_idx = i
                break
        if target_msg is None:
            return jsonify({"ok": False, "error": "Сообщение не найдено"})
        author = target_msg.get("from", "")
        is_admin = name in ("Ярослав", "Ярик")
        if mode == "all":
            if author != name and not is_admin:
                return jsonify({"ok": False, "error": "Только автор"}), 403
            del msgs[target_idx]
            save_history(room)
            push_broadcast({"type": "message_deleted", "msg_id": msg_id,
                            "room": room, "mode": "all"})
            return jsonify({"ok": True})
        elif mode == "self":
            if "deleted_for" not in target_msg:
                target_msg["deleted_for"] = []
            if name not in target_msg["deleted_for"]:
                target_msg["deleted_for"].append(name)
            save_history(room)
            push_message(name, {"type": "message_deleted", "msg_id": msg_id,
                                "room": room, "mode": "self"})
            return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Неверный режим"})


@app.route("/reaction", methods=["POST"])
def reaction():
    data = request.get_json()
    name = data.get("name", "").strip()
    password = data.get("password", "")
    if name not in users or users[name]["password"] != password:
        return jsonify({"ok": False, "error": "Неверный логин"}), 401
    msg_id = data.get("msg_id", "")
    emoji = data.get("emoji", "")
    room = data.get("room", "general")
    if not msg_id or not emoji:
        return jsonify({"ok": False, "error": "Нет msg_id или emoji"})
    with lock:
        if msg_id not in reactions_store:
            reactions_store[msg_id] = {}
        if emoji not in reactions_store[msg_id]:
            reactions_store[msg_id][emoji] = []
        if name in reactions_store[msg_id][emoji]:
            reactions_store[msg_id][emoji].remove(name)
            if not reactions_store[msg_id][emoji]:
                del reactions_store[msg_id][emoji]
        else:
            reactions_store[msg_id][emoji].append(name)
        if msg_id in reactions_store and not reactions_store[msg_id]:
            del reactions_store[msg_id]
        for m in history.get(room, []):
            if m.get("msg_id") == msg_id:
                m["reactions"] = reactions_store.get(msg_id, {})
                break
        save_reactions()
        save_history(room)
    push_broadcast({"type": "reaction_update", "msg_id": msg_id,
                    "reactions": reactions_store.get(msg_id, {}), "room": room})
    return jsonify({"ok": True, "reactions": reactions_store.get(msg_id, {})})


@app.route("/poll", methods=["POST"])
def poll():
    data = request.get_json()
    name = data.get("name", "").strip()
    password = data.get("password", "")
    if name not in users or users[name]["password"] != password:
        return jsonify({"ok": False, "error": "Неверный логин"}), 401
    with lock:
        online[name] = time.time()
        if name not in queues:
            queues[name] = []
        my_queue = queues[name][:]
        queues[name] = []
    my_queue = [m for m in my_queue if name not in m.get("deleted_for", [])]
    now = time.time()
    with lock:
        for n in list(online.keys()):
            if now - online[n] > 15:
                del online[n]
    return jsonify({
        "ok": True, "messages": my_queue,
        "online": list(online.keys()), "rooms": rooms, "profiles": profiles,
    })


@app.route("/history", methods=["POST"])
def get_history():
    data = request.get_json()
    name = data.get("name", "").strip()
    password = data.get("password", "")
    room = data.get("room", "general")
    if name not in users or users[name]["password"] != password:
        return jsonify({"ok": False, "error": "Неверный логин"}), 401
    msgs = history.get(room, [])
    result = []
    for m in msgs:
        if name in m.get("deleted_for", []):
            continue
        mid = m.get("msg_id", "")
        if mid and mid in reactions_store:
            m["reactions"] = reactions_store[mid]
        elif "reactions" not in m:
            m["reactions"] = {}
        result.append(m)
    return jsonify({"ok": True, "messages": result})


@app.route("/create_room", methods=["POST"])
def create_room():
    data = request.get_json()
    name = data.get("name", "").strip()
    password = data.get("password", "")
    room_name = data.get("room_name", "").strip()
    is_private = data.get("private", False)
    room_password = data.get("room_password", "")
    if name not in users or users[name]["password"] != password:
        return jsonify({"ok": False, "error": "Неверный логин"}), 401
    if not room_name:
        return jsonify({"ok": False, "error": "Введи название"}), 400
    with lock:
        rid = "room_" + str(int(time.time() * 1000))
        rooms[rid] = {"id": rid, "name": room_name, "type": "text",
                      "private": is_private, "password": room_password,
                      "owner": name}
        save_rooms()
        history[rid] = []
        save_history(rid)
    push_broadcast({"type": "rooms_update", "rooms": rooms})
    return jsonify({"ok": True, "id": rid})


@app.route("/delete_room", methods=["POST"])
def delete_room():
    data = request.get_json()
    name = data.get("name", "").strip()
    password = data.get("password", "")
    room_id = data.get("room_id", "").strip()
    if name not in users or users[name]["password"] != password:
        return jsonify({"ok": False, "error": "Неверный логин"}), 401
    if room_id == "general":
        return jsonify({"ok": False, "error": "Общий удалить нельзя"}), 403
    if room_id not in rooms:
        return jsonify({"ok": False, "error": "Комната не найдена"}), 404
    owner = rooms[room_id].get("owner", "")
    is_admin = name in ("Ярослав", "Ярик")
    if not is_admin and owner != name:
        return jsonify({"ok": False, "error": "Только владелец"}), 403
    with lock:
        del rooms[room_id]
        save_rooms()
        if room_id in history:
            del history[room_id]
        hist_file = os.path.join(HISTORY_DIR, room_id + ".json")
        if os.path.exists(hist_file):
            try:
                os.remove(hist_file)
            except Exception:
                pass
    push_broadcast({"type": "rooms_update", "rooms": rooms})
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("=" * 60)
    print("BerkutChat Server v3.9 (Рамки аватарок)")
    print("=" * 60)
    print("Порт: " + str(port))
    print("Пользователей: " + str(len(users)))
    print("Профилей: " + str(len(profiles)))
    print("Комнат: " + str(len(rooms)))
    print("Паков: " + str(len(packs)))
    print("Стикеров: " + str(sum(p["count"] for p in packs)))
    print("=" * 60)
    print("НОВОЕ: /profile/set_frame — админ выдаёт рамки")
    print("=" * 60)
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
