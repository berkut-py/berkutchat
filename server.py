# -*- coding: utf-8 -*-
"""
Berkut Messenger Server v1.0
Flask + WebSocket сервер
"""
import os
import json
import time
import threading
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_sock import Sock

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
sock = Sock(app)

# ============================================================
# ХРАНИЛИЩЕ
# ============================================================
users = {}
rooms = {}
history = {}
connections = {}
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


def broadcast(data, room_id=None, exclude=None):
    to_remove = []
    for ws, info in list(connections.items()):
        if ws is exclude:
            continue
        if room_id and room_id not in info.get("rooms", []):
            continue
        try:
            ws.send(json.dumps(data, ensure_ascii=False))
        except Exception:
            to_remove.append(ws)
    for ws in to_remove:
        if ws in connections:
            del connections[ws]


def send_to(ws, data):
    try:
        ws.send(json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


# ============================================================
# HTTP API
# ============================================================
@app.route("/")
def index():
    return jsonify({
        "status": "online",
        "name": "Berkut Messenger Server",
        "version": "1.0",
        "users_online": len(connections),
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
    return jsonify({"ok": True, "name": name, "avatar": users[name].get("avatar", "👤")})


@app.route("/rooms/list", methods=["GET"])
def rooms_list():
    result = []
    for rid, r in rooms.items():
        if not r.get("private"):
            online = sum(1 for i in connections.values() if rid in i.get("rooms", []))
            result.append({"id": rid, "name": r["name"], "type": r["type"], "online": online})
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
# WEBSOCKET
# ============================================================
@sock.route("/ws")
def ws_handler(ws):
    name = None
    try:
        while True:
            raw = ws.receive()
            if not raw:
                break
            try:
                data = json.loads(raw)
            except Exception:
                continue
            action = data.get("action")

            if action == "join":
                join_name = data.get("name", "").strip()
                password = data.get("password", "")
                if join_name not in users or users[join_name]["password"] != password:
                    send_to(ws, {"type": "error", "message": "Неверный логин"})
                    break
                name = join_name
                connections[ws] = {"name": name, "rooms": ["general"]}
                send_to(ws, {
                    "type": "joined", "name": name,
                    "rooms": rooms, "history": history.get("general", []),
                })
                broadcast({"type": "system", "message": name + " присоединился", "time": now_str()})
                broadcast({"type": "online", "users": [i["name"] for i in connections.values()]})
                continue

            if action == "message":
                room_id = data.get("room", "general")
                text = data.get("text", "")
                if not text.strip():
                    continue
                msg = {
                    "from": name, "avatar": users[name].get("avatar", "👤"),
                    "text": text, "time": now_str(), "room": room_id,
                }
                add_to_history(room_id, msg)
                broadcast({"type": "message", **msg}, room_id=room_id)
                continue

            if action == "dm":
                target = data.get("to", "")
                text = data.get("text", "")
                if not text.strip() or target not in users:
                    continue
                msg = {
                    "from": name, "to": target,
                    "avatar": users[name].get("avatar", "👤"),
                    "text": text, "time": now_str(),
                }
                for w, info in list(connections.items()):
                    if info["name"] in (name, target):
                        send_to(w, {"type": "dm", **msg})
                continue

            if action == "switch_room":
                room_id = data.get("room", "general")
                if ws in connections and room_id not in connections[ws]["rooms"]:
                    connections[ws]["rooms"].append(room_id)
                send_to(ws, {"type": "history", "room": room_id, "messages": history.get(room_id, [])})
                continue

            if action == "create_room":
                room_name = data.get("name", "").strip()
                rtype = data.get("type", "text")
                private = data.get("private", False)
                password = data.get("password", "")
                if not room_name:
                    continue
                with lock:
                    rid = "room_" + str(int(time.time() * 1000))
                    rooms[rid] = {
                        "id": rid, "name": room_name, "type": rtype,
                        "private": private, "password": password,
                        "members": [name], "owner": name,
                    }
                    save_rooms()
                    history[rid] = []
                    save_history(rid)
                if ws in connections:
                    connections[ws]["rooms"].append(rid)
                broadcast({"type": "rooms_update", "rooms": rooms})
                continue

            if action == "ping":
                send_to(ws, {"type": "pong"})
                continue

    except Exception as e:
        print("[WS] " + str(e))
    finally:
        if ws in connections:
            info = connections[ws]
            del connections[ws]
            broadcast({"type": "system", "message": info["name"] + " покинул чат", "time": now_str()})
            broadcast({"type": "online", "users": [i["name"] for i in connections.values()]})


# ============================================================
# ЗАПУСК
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("=" * 60)
    print("Berkut Messenger Server")
    print("=" * 60)
    print("Порт: " + str(port))
    print("Пользователей: " + str(len(users)))
    print("Комнат: " + str(len(rooms)))
    print("=" * 60)
    app.run(host="0.0.0.0", port=port, debug=False)