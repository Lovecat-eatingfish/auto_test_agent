-- SQLite 建表脚本，均为 IF NOT EXISTS，重启不会重复建表
CREATE TABLE IF NOT EXISTS users (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL,                -- BCrypt 哈希，不存明文
    name     TEXT NOT NULL,
    balance  REAL NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS orders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    product    TEXT NOT NULL,
    quantity   INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
