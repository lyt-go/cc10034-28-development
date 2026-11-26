"""业务路由汇总：跨模块链接/保留复核路由必须先于通用资源路由注册。"""
from __future__ import annotations

from app.routers import cross_links, retention, resource

ROUTERS = [cross_links, retention, resource]
