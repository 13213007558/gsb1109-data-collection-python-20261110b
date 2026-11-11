"""本地复现：Date 早 2 分钟 + Age: 40 + 服务端/调用方双过期时间时，
requests-cache 1.3.4 实际依据什么决定 expires，以及重启后的判断是否变化。

直接运行：.venv/bin/python repro_age_expiration.py
"""

import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import requests
import requests_mock
from freezegun import freeze_time

from requests_cache import CachedSession
from requests_cache.policy import get_expiration_datetime

URL = 'https://test.local/resource'
T0 = datetime(2026, 11, 11, 12, 0, 0, tzinfo=timezone.utc)


def httpdate(dt):
    return format_datetime(dt.astimezone(timezone.utc))


def make_session(db_path, response_headers, **kwargs):
    session = CachedSession(db_path, backend='sqlite', **kwargs)
    adapter = requests_mock.Adapter()
    session.mount('https://', adapter)
    adapter.register_uri('GET', URL, text='hello', headers=response_headers)
    return session


def line(title):
    print(f'\n{"=" * 72}\n{title}\n{"=" * 72}')


# 公共响应头：Date 比“收到时间”T0 早 120s，Age: 40
DATE_HDR = httpdate(T0 - timedelta(seconds=120))
AGE_HDR = '40'
# 与 max-age=60 等价的绝对 Expires：Date + 60s，即 T0 - 60s（收到时已过期）
EXPIRES_HDR = httpdate(T0 - timedelta(seconds=60))

# ----------------------------------------------------------------------
# 对照 1：cache_control=True，服务端 max-age=60，调用方 session expire_after=3600
# ----------------------------------------------------------------------
line('对照1  cache_control=True: Cache-Control max-age=60 vs 调用方 3600s')
with freeze_time(T0):
    tmp = Path(tempfile.mkdtemp()) / 'c1.sqlite'
    session = make_session(
        tmp,
        {'Date': DATE_HDR, 'Age': AGE_HDR, 'Cache-Control': 'max-age=60'},
        cache_control=True,
        expire_after=3600,
    )
    resp = session.get(URL)
    cached = session.cache.get_response(resp.cache_key)
    print(f'Date 头           : {DATE_HDR}  (T0 - 120s)')
    print(f'Age 头            : {AGE_HDR}')
    print(f'缓存 created_at   : {cached.created_at.isoformat()}  (锚点=本地收到时刻 T0)')
    print(f'缓存 expires      : {cached.expires.isoformat()}  (T0 + '
          f'{int((cached.expires - T0).total_seconds())}s)')
    print(f'T0 时 is_expired  : {cached.is_expired}')
    print('RFC9111 对照      : current_age=max(Age=40, apparent_age=120)=120 > lifetime=60'
          ' -> 按 RFC 收到即过期；requests-cache 判定还有 60s 新鲜期')
    assert cached.expires == T0 + timedelta(seconds=60)
    assert cached.is_expired is False
    session.close()

# ----------------------------------------------------------------------
# 对照 2：cache_control=True，用 Expires 表达同样的 60s 寿命（Date+60）
# ----------------------------------------------------------------------
line('对照2  cache_control=True: Expires=Date+60（绝对时间，收到时已是过去）')
with freeze_time(T0):
    tmp = Path(tempfile.mkdtemp()) / 'c2.sqlite'
    session = make_session(
        tmp,
        {'Date': DATE_HDR, 'Age': AGE_HDR, 'Expires': EXPIRES_HDR},
        cache_control=True,
        expire_after=3600,
    )
    resp = session.get(URL)
    cached = session.cache.get_response(resp.cache_key)
    print(f'Expires 头        : {EXPIRES_HDR}  (T0 - 60s)')
    print(f'缓存 expires      : {cached.expires.isoformat()}')
    print(f'T0 时 is_expired  : {cached.is_expired}')
    print('结论：同样是“服务端寿命 60s”，max-age 以本地时钟 T0 为锚点，'
          'Expires 按绝对字面值；Age 在两条路径里都不参与')
    assert cached.expires == T0 - timedelta(seconds=60)
    assert cached.is_expired is True
    session.close()

# ----------------------------------------------------------------------
# 对照 3：cache_control=False（默认），同样的服务端头 + 调用方 3600s
# ----------------------------------------------------------------------
line('对照3  cache_control=False（默认）: 服务端头整体被忽略，只认调用方 3600s')
with freeze_time(T0):
    tmp = Path(tempfile.mkdtemp()) / 'c3.sqlite'
    session = make_session(
        tmp,
        {'Date': DATE_HDR, 'Age': AGE_HDR, 'Cache-Control': 'max-age=60'},
        cache_control=False,
        expire_after=3600,
    )
    resp = session.get(URL)
    cached = session.cache.get_response(resp.cache_key)
    print(f'缓存 expires      : {cached.expires.isoformat()}  (T0 + '
          f'{int((cached.expires - T0).total_seconds())}s)')
    print(f'T0 时 is_expired  : {cached.is_expired}')
    assert cached.expires == T0 + timedelta(seconds=3600)
    assert cached.is_expired is False
    session.close()

# ----------------------------------------------------------------------
# 对照 4：重启（新 session 实例、甚至换成 cache_control=True）后判断是否一致
# ----------------------------------------------------------------------
line('对照4  SQLite 持久化后重开：expires 是写死的绝对时刻，不按 Date/Age 重算')
tmp = Path(tempfile.mkdtemp()) / 'c4.sqlite'
key = None
with freeze_time(T0):
    session = make_session(
        tmp,
        {'Date': DATE_HDR, 'Age': AGE_HDR, 'Cache-Control': 'max-age=60'},
        expire_after=100,
    )
    resp = session.get(URL)
    key = resp.cache_key
    print(f'写入时 expires     : {resp.expires.isoformat()}  (T0 + 100s)')
    session.close()

raw = sqlite3.connect(tmp).execute(
    'SELECT expires FROM responses WHERE key=?', (key,)).fetchone()[0]
print(f'SQLite expires 列  : {raw} (unix ts) == '
      f'{datetime.fromtimestamp(raw, timezone.utc).isoformat()}')

# 重开时故意改配置：session 默认值改成 9999，并打开 cache_control
with freeze_time(T0 + timedelta(seconds=50)):
    session2 = make_session(tmp, {}, cache_control=True, expire_after=9999)
    cached2 = session2.cache.get_response(key)
    print(f'T0+50s 重开 expires: {cached2.expires.isoformat()}（未变）, '
          f'is_expired={cached2.is_expired}, 头里仍有 Age={cached2.headers.get("Age")}')
    assert cached2.expires == T0 + timedelta(seconds=100)
    assert cached2.is_expired is False
    session2.close()

with freeze_time(T0 + timedelta(seconds=101)):
    session3 = make_session(tmp, {}, cache_control=True, expire_after=9999)
    cached3 = session3.cache.get_response(key)
    print(f'T0+101s 重开       : is_expired={cached3.is_expired}'
          '（只比对墙上时钟 vs 存储的绝对 expires）')
    assert cached3.is_expired is True
    session3.close()

# ----------------------------------------------------------------------
# 函数级证据：过期计算入口根本不接受 Date/Age 参数
# ----------------------------------------------------------------------
line('附：策略层函数签名（没有 Date/Age 入参，Age 结构上无法参与计算）')
with freeze_time(T0):
    print('get_expiration_datetime(60) =', get_expiration_datetime(60).isoformat(),
          '  # 锚点就是 utcnow()=T0')
print('\n全部断言通过。')
