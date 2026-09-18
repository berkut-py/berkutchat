# -*- coding: utf-8 -*-
"""
Berkut Messenger Server v2.0
Flask + HTTP Polling (без WebSocket — работает на RelaxDev)
"""
import os
import json
import time
import threading
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory

# ============================================================
# КОНФИГ
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(BASE_DIR, "users.json")
ROOMS_FILE = os.path.join(BASE_DIR, "rooms.json")
HISTORY_DIR = os.path.join(BASE_DIR, "history")
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
MAX_HISTORY = 500
MAX_FILE_SIZE = 10 * 1024 * 1024

for d in [HISTORY_DIR, UPLOADS_DIR]:
    os.makedirs(d, exist_ok=True)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = MAX_FILE_SIZE

# ============================================================
# ХРАНИЛИЩЕ
# ============================================================
users = {}
rooms = {}
history = {}
poll_queues = {}  # {name: {"queue": [], "last_seen": timestamp}}
lock = threading.Lock()


def load_data():
    global users, rooms, history
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                users = json.load(f)
        except Exception:
            users = {}
    if os.path.exists(ROOMS_FILE):
        try:
            with open(ROOMS_FILE, "r", encoding="utf-8") as f:
                rooms = json.load(f)
        except Exception:
            rooms = {}
    for room_id in rooms.keys():
        hist_file = os.path.join(HISTORY_DIR, room_id + ".json")
        if os.path.exists(hist_file):
            try:
                with open(hist_file, "r", encoding="utf-8") as f:
                    history[room_id] = json.load(f)
            except Exception:
                history[room_id] = []
        else:
            history[room_id] = []


def save_users():
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def save_rooms():
    with open(ROOMS_FILE, "w", encoding="utf-8") as f:
        json.dump(rooms, f, ensure_ascii=False, indent=2)


def save_history(room_id):
    hist_file = os.path.join(HISTORY_DIR, room_id + ".json")
    with open(hist_file, "w", encoding="utf-8") as f:
        json.dump(history.get(room_id, []), f, ensure_ascii=False, indent=2)


def add_to_history(room_id, msg):
    if room_id not in history:
        history[room_id] = []
    history[room_id].append(msg)
    if len(history[room_id]) > MAX_HISTORY:
        history[room_id] = history[room_id][-MAX_HISTORY:]
    save_history(room_id)


load_data()

if not rooms:
    rooms["general"] = {
        "id": "general", "name": "Общий", "type": "text",
        "private": False, "password": "", "members": [], "owner": "system",
    }
    save_rooms()


def now_str():
    return datetime.now().strftime("%H:%M:%S")


def push_to_all(msg):
    """Кладёт сообщение в очередь всех пользователей"""
    with lock:
        for u in poll_queues:
            poll_queues[u]["queue"].append(msg)


def push_to_user(name, msg):
    with lock:
        if name not in poll_queues:
            poll_queues[name] = {"queue": [], "last_seen": time.time()}
        poll_queues[name]["queue"].append(msg)


# ============================================================
# HTTP API
# ============================================================
@app.route("/")
def index():
    return jsonify({
        "status": "online",
        "name": "Berkut Messenger Server",
        "version": "2.0-polling",
        "users_online": len(poll_queues),
        "users_total": len(users),
        "rooms_total": len(rooms),
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
        users[name] = {"password": password, "avatar": "👤", "created": now_str()}
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
    # Регистрируем в очереди
    with lock:
        poll_queues[name] = {"queue": [], "last_seen": time.time()}
    # Оповещаем всех что зашёл
    push_to_all({"type": "system", "message": name + " присоединился", "time": now_str()})
    return jsonify({"ok": True, "name": name, "avatar": users[name].get("avatar", "👤")})


@app.route("/logout", methods=["POST"])
def logout():
    data = request.get_json()
    name = data.get("name", "").strip()
    with lock:
        if name in poll_queues:
            del poll_queues[name]
    push_to_all({"type": "system", "message": name + " покинул чат", "time": now_str()})
    return jsonify({"ok": True})


@app.route("/poll/send", methods=["POST"])
def poll_send():
    data = request.get_json()
    name = data.get("name", "").strip()
    password = data.get("password", "")
    if name not in users or users[name]["password"] != password:
        return jsonify({"ok": False, "error": "Неверный логин"}), 401

    msg_type = data.get("type", "message")
    room = data.get("room", "general")
    text = data.get("text", "").strip()
    target = data.get("to", "")

    # Обновляем last_seen
    with lock:
        if name in poll_queues:
            poll_queues[name]["last_seen"] = time.time()

    if msg_type == "message":
        if not text:
            return jsonify({"ok": False, "error": "Пустое сообщение"})
        msg = {
            "type": "message", "from": name, "avatar": users[name].get("avatar", "👤"),
            "text": text, "time": now_str(), "room": room,
        }
        add_to_history(room, msg)
        push_to_all(msg)
        return jsonify({"ok": True})

    elif msg_type == "dm":
        if not text or target not in users:
            return jsonify({"ok": False, "error": "Неверный получатель"})
        msg = {
            "type": "dm", "from": name, "to": target,
            "avatar": users[name].get("avatar", "👤"),
            "text": text, "time": now_str(),
        }
        push_to_user(name, msg)
        push_to_user(target, msg)
        return jsonify({"ok": True})

    elif msg_type == "create_room":
        room_name = data.get("room_name", "").strip()
        rtype = data.get("room_type", "text")
        private = data.get("private", False)
        password_r = data.get("password", "")
        if not room_name:
            return jsonify({"ok": False, "error": "Нет названия"})
        with lock:
            rid = "room_" + str(int(time.time() * 1000))
            rooms[rid] = {
                "id": rid, "name": room_name, "type": rtype,
                "private": private, "password": password_r,
                "members": [name], "owner": name,
            }
            save_rooms()
            history[rid] = []
            save_history(rid)
        push_to_all({"type": "rooms_update", "rooms": rooms})
        return jsonify({"ok": True, "id": rid})

    return jsonify({"ok": False, "error": "Неизвестный тип"})


@app.route("/poll/get", methods=["POST"])
def poll_get():
    """Клиент дёргает каждую секунду — отдаём новые сообщения"""
    data = request.get_json()
    name = data.get("name", "").strip()
    password = data.get("password", "")
    if name not in users or users[name]["password"] != password:
        return jsonify({"ok": False, "error": "Неверный логин"}), 401

    with lock:
        if name not in poll_queues:
            poll_queues[name] = {"queue": [], "last_seen": time.time()}
        queue = poll_queues[name]["queue"]
        poll_queues[name]["queue"] = []
        poll_queues[name]["last_seen"] = time.time()
        online = list(poll_queues.keys())

    return jsonify({
        "ok": True,
        "messages": queue,
        "online": online,
        "rooms": rooms,
    })


@app.route("/poll/history", methods=["POST"])
def poll_history():
    """История конкретной комнаты"""
    data = request.get_json()
    name = data.get("name", "").strip()
    password = data.get("password", "")
    room = data.get("room", "general")
    if name not in users or users[name]["password"] != password:
        return jsonify({"ok": False, "error": "Неверный логин"}), 401
    return jsonify({"ok": True, "messages": history.get(room, [])})


@app.route("/rooms/list", methods=["GET"])
def rooms_list():
    result = []
    for rid, r in rooms.items():
        if not r.get("private"):
            online = sum(1 for u in poll_queues if True)
            result.append({"id": rid, "name": r["name"], "type": r["type"], "online": 0})
    return jsonify(result)


@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "Нет файла"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"ok": False, "error": "Пустое имя"}), 400
    filename = str(int(time.time() * 1000)) + "_" + file.filename
    path = os.path.join(UPLOADS_DIR, filename)
    file.save(path)
    size = os.path.getsize(path)
    if size > MAX_FILE_SIZE:
        os.remove(path)
        return jsonify({"ok": False, "error": "Файл больше 10 МБ"}), 400
    return jsonify({"ok": True, "url": "/uploads/" + filename, "name": file.filename, "size": size})


@app.route("/uploads/<filename>")
def get_upload(filename):
    return send_from_directory(UPLOADS_DIR, filename)


# ============================================================
# ЗАПУСК
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("=" * 60)
    print("Berkut Messenger Server v2.0 (POLLING)")
    print("=" * 60)
    print("Порт: " + str(port))
    print("Пользователей: " + str(len(users)))
    print("Комнат: " + str(len(rooms)))
    print("=" * 60)
    app.run(host="0.0.0.0", port=port, debug=False)
