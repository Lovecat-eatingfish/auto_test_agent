# Demo User & Product Service

一个用于演示「接口自动化测试 Agent」的被测项目:基于 FastAPI 的用户/商品服务。

## 接口概览

| 方法 | 路径 | 说明 | 鉴权 |
| --- | --- | --- | --- |
| GET | /health | 健康检查 | 否 |
| POST | /api/register | 用户注册 | 否 |
| POST | /api/login | 用户登录,返回 token | 否 |
| GET | /api/users | 分页查询用户列表 | 是 |
| GET | /api/users/{user_id} | 查询单个用户 | 是 |
| PUT | /api/users/{user_id} | 更新用户信息 | 是 |
| DELETE | /api/users/{user_id} | 删除用户 | 是 |
| GET | /api/products | 分页+多条件查询商品 | 否 |
| POST | /api/products | 创建商品 | 是 |
| GET | /api/products/{product_id} | 查询单个商品 | 否 |

## 默认测试账号

登录账号: admin
登录密码: Admin12345

## 业务规则

- 用户名:3-20 位字母/数字/下划线,全局唯一(重复注册返回 409)
- 密码:8-32 位
- age:1-120 的整数,可为空
- 分页:page >= 1,page_size 1-100
- 商品价格:0.01-999999;category 取值 electronics/books/clothes/food
- 商品列表同时传 min_price 与 max_price 时,min_price > max_price 返回 400
- 参数校验失败统一返回 HTTP 400;未登录/token 无效返回 HTTP 401;资源不存在返回 HTTP 404
- 成功响应统一为 {"code": 0, "message": "ok", "data": ...}

## 启动

```bash
uvicorn demo_app.main:app --host 127.0.0.1 --port 8100
```
