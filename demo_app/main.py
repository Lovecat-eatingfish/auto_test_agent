"""Demo FastAPI application: user & product service (the scan target)."""
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from demo_app import schemas
from demo_app.store import store

app = FastAPI(title="Demo User & Product Service", version="1.0.0")
store.seed()


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    msg = "参数校验失败"
    try:
        first = exc.errors()[0]
        msg = f"参数校验失败: {first.get('msg', '')}"
    except Exception:
        pass
    return JSONResponse(status_code=400, content={"code": 400, "message": msg, "data": None})


def envelope(data=None, message="ok"):
    return {"code": 0, "message": message, "data": data}


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(status_code=exc.status_code,
                        content={"code": exc.status_code, "message": exc.detail, "data": None})


def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录或token无效")
    user = store.user_by_token(authorization[7:])
    if user is None:
        raise HTTPException(status_code=401, detail="未登录或token无效")
    return user


@app.get("/health", summary="健康检查")
def health():
    return {"status": "up"}


@app.post("/api/register", summary="用户注册")
def register(payload: schemas.UserCreate):
    if store.username_exists(payload.username):
        raise HTTPException(status_code=409, detail="用户名已存在")
    user = store.create_user(payload.username, payload.password, payload.email, payload.age)
    return envelope({"id": user["id"], "username": user["username"]})


@app.post("/api/login", summary="用户登录", description="登录成功返回 token,后续接口通过 Authorization: Bearer <token> 鉴权")
def login(payload: schemas.UserLogin):
    token = store.check_login(payload.username, payload.password)
    if token is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return envelope({"token": token, "username": payload.username})


@app.get("/api/users", summary="分页查询用户列表")
def list_users(
    page: int = Query(1, ge=1, description="页码,从1开始"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数,1-100"),
    keyword: str | None = Query(None, description="用户名模糊搜索"),
    _: dict = Depends(get_current_user),
):
    total, items = store.list_users(page, page_size, keyword)
    return envelope({"total": total, "page": page, "page_size": page_size, "items": items})


@app.get("/api/users/{user_id}", summary="查询单个用户")
def get_user(user_id: int, _: dict = Depends(get_current_user)):
    user = store.user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    user.pop("password", None)
    return envelope(user)


@app.put("/api/users/{user_id}", summary="更新用户信息")
def update_user(user_id: int, payload: schemas.UserUpdate, _: dict = Depends(get_current_user)):
    updated = store.update_user(user_id, {"email": payload.email, "age": payload.age})
    if updated is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    updated.pop("password", None)
    return envelope(updated)


@app.delete("/api/users/{user_id}", summary="删除用户")
def delete_user(user_id: int, _: dict = Depends(get_current_user)):
    if not store.delete_user(user_id):
        raise HTTPException(status_code=404, detail="用户不存在")
    return envelope(None, message="删除成功")


@app.get("/api/products", summary="分页+多条件查询商品列表")
def list_products(
    category: str | None = Query(None, description="商品分类"),
    min_price: float | None = Query(None, ge=0, description="最低价格"),
    max_price: float | None = Query(None, ge=0, description="最高价格"),
    in_stock: bool | None = Query(None, description="是否只看有货商品"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if min_price is not None and max_price is not None and min_price > max_price:
        raise HTTPException(status_code=400, detail="min_price不能大于max_price")
    total, items = store.list_products(category, min_price, max_price, in_stock, page, page_size)
    return envelope({"total": total, "page": page, "page_size": page_size, "items": items})


@app.post("/api/products", summary="创建商品")
def create_product(payload: schemas.ProductCreate, _: dict = Depends(get_current_user)):
    product = store.create_product(payload.name, payload.price, payload.category,
                                   payload.stock, payload.description)
    return envelope(product)


@app.get("/api/products/{product_id}", summary="查询单个商品")
def get_product(product_id: int):
    product = store.product_by_id(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="商品不存在")
    return envelope(product)
