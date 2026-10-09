"""Pydantic schemas of the demo service (with field constraints the agent can parse)."""
import re

from pydantic import BaseModel, Field, field_validator

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")


class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=20, description="用户名,3-20位字母数字下划线")
    password: str = Field(..., min_length=8, max_length=32, description="密码,8-32位")
    email: str = Field(..., description="邮箱地址")
    age: int | None = Field(None, ge=1, le=120, description="年龄,1-120,可为空")

    @field_validator("username")
    @classmethod
    def username_format(cls, v):
        if not USERNAME_RE.match(v):
            raise ValueError("用户名只能包含字母数字下划线,长度3-20")
        return v

    @field_validator("email")
    @classmethod
    def email_format(cls, v):
        if not EMAIL_RE.match(v):
            raise ValueError("邮箱格式不正确")
        return v


class UserLogin(BaseModel):
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")


class UserUpdate(BaseModel):
    email: str | None = Field(None, description="新邮箱")
    age: int | None = Field(None, ge=1, le=120, description="新年龄,1-120")

    @field_validator("email")
    @classmethod
    def email_format(cls, v):
        if v is not None and not EMAIL_RE.match(v):
            raise ValueError("邮箱格式不正确")
        return v


class ProductCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=50, description="商品名称,2-50字符")
    price: float = Field(..., ge=0.01, le=999999, description="商品价格,0.01-999999")
    category: str = Field(..., description="分类:electronics/books/clothes/food")
    stock: int = Field(0, ge=0, description="库存,>=0")
    description: str | None = Field(None, max_length=200, description="商品描述,最长200")

    @field_validator("category")
    @classmethod
    def category_allowed(cls, v):
        if v not in ("electronics", "books", "clothes", "food"):
            raise ValueError("分类必须是 electronics/books/clothes/food 之一")
        return v
