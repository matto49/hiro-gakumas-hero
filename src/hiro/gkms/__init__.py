"""GKMS 育成策略插件的适配层（spec docs/ryugi/specs/2026-11-26-gkms-produce-plugin-design.md）。

GKMS（yuzeis/GKMSProject）的代码不进本仓库：这里只找到本机装的 GKMS，在它自己的 Python 子进程里调用
路线规划（hajime_route / nia_route）和选卡包 pickv，把结果换成 hiro 的 Decision。
"""
