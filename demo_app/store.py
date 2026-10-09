"""In-memory data store for the demo service."""
import itertools
import threading
import uuid


class Store:
    def __init__(self):
        self._lock = threading.Lock()
        self.users = {}      # username -> user dict
        self.products = {}   # product_id -> product dict
        self.tokens = {}     # token -> username
        self._uid = itertools.count(1)
        self._pid = itertools.count(1)

    # ---- users ----
    def seed(self):
        with self._lock:
            self._create_user("admin", "Admin12345", "admin@demo.com", 30)
            for p in self._seed_products():
                pid = next(self._pid)
                p["id"] = pid
                self.products[pid] = p

    def _seed_products(self):
        return [
            {"name": "机械键盘", "price": 399.0, "category": "electronics", "stock": 15, "description": "青轴"},
            {"name": "无线鼠标", "price": 129.0, "category": "electronics", "stock": 0, "description": ""},
            {"name": "Python Cookbook", "price": 89.5, "category": "books", "stock": 40, "description": ""},
            {"name": "三体", "price": 45.0, "category": "books", "stock": 100, "description": "科幻小说"},
            {"name": "纯棉T恤", "price": 59.9, "category": "clothes", "stock": 66, "description": ""},
            {"name": "黑咖啡豆", "price": 78.0, "category": "food", "stock": 8, "description": "500g"},
        ]

    def _create_user(self, username, password, email, age):
        user = {
            "id": next(self._uid),
            "username": username,
            "password": password,
            "email": email,
            "age": age,
        }
        self.users[username] = user
        return user

    def username_exists(self, username):
        return username in self.users

    def create_user(self, username, password, email, age):
        with self._lock:
            return self._create_user(username, password, email, age)

    def check_login(self, username, password):
        user = self.users.get(username)
        if user and user["password"] == password:
            token = uuid.uuid4().hex
            self.tokens[token] = username
            return token
        return None

    def user_by_token(self, token):
        return self.users.get(self.tokens.get(token))

    def user_by_id(self, user_id):
        for u in self.users.values():
            if u["id"] == user_id:
                return dict(u)
        return None

    def list_users(self, page, page_size, keyword=None):
        users = [dict(u) for u in self.users.values()]
        if keyword:
            users = [u for u in users if keyword in u["username"]]
        users.sort(key=lambda u: u["id"])
        total = len(users)
        start = (page - 1) * page_size
        items = users[start:start + page_size]
        for u in items:
            u.pop("password", None)
        return total, items

    def update_user(self, user_id, fields):
        with self._lock:
            target = None
            for u in self.users.values():
                if u["id"] == user_id:
                    target = u
                    break
            if target is None:
                return None
            for k, v in fields.items():
                if v is not None:
                    target[k] = v
            return dict(target)

    def delete_user(self, user_id):
        with self._lock:
            for username, u in self.users.items():
                if u["id"] == user_id:
                    del self.users[username]
                    return True
            return False

    # ---- products ----
    def create_product(self, name, price, category, stock, description):
        with self._lock:
            pid = next(self._pid)
            p = {"id": pid, "name": name, "price": price,
                 "category": category, "stock": stock,
                 "description": description or ""}
            self.products[pid] = p
            return dict(p)

    def product_by_id(self, product_id):
        p = self.products.get(product_id)
        return dict(p) if p else None

    def list_products(self, category=None, min_price=None, max_price=None,
                      in_stock=None, page=1, page_size=20):
        items = list(self.products.values())
        if category:
            items = [p for p in items if p["category"] == category]
        if min_price is not None:
            items = [p for p in items if p["price"] >= min_price]
        if max_price is not None:
            items = [p for p in items if p["price"] <= max_price]
        if in_stock is not None:
            items = [p for p in items if (p["stock"] > 0) == in_stock]
        items.sort(key=lambda p: p["id"])
        total = len(items)
        start = (page - 1) * page_size
        return total, [dict(p) for p in items[start:start + page_size]]


store = Store()
