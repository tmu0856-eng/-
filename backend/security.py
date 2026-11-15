"""可选评审登录与公开演示请求限额。"""
import hashlib
import hmac
import os
import secrets
import threading
import time
from collections import defaultdict,deque

COOKIE='researchdock_session'
_BUCKETS=defaultdict(deque)
_LOCK=threading.Lock()


def enabled():
    return bool(os.getenv('DEMO_PASSWORD',''))


def authenticated(request):
    if not enabled():
        return True
    token=request.cookies.get(COOKIE,'')
    try:
        expires,nonce,signature=token.split('.')
        expected=hmac.new(_key(),(expires+'.'+nonce).encode(),hashlib.sha256).hexdigest()
        return int(expires)>time.time() and len(nonce)==32 and hmac.compare_digest(signature,expected)
    except (ValueError,TypeError):
        return False


def _key():
    return hashlib.sha256((os.getenv('DEMO_SESSION_SECRET') or os.environ['DEMO_PASSWORD']).encode()).digest()


def credentials(username,password):
    return (enabled() and hmac.compare_digest(username.encode(),os.getenv('DEMO_USERNAME','reviewer').encode())
            and hmac.compare_digest(password.encode(),os.environ['DEMO_PASSWORD'].encode()))


def create_session():
    payload=str(int(time.time())+8*3600)+'.'+secrets.token_hex(16)
    return payload+'.'+hmac.new(_key(),payload.encode(),hashlib.sha256).hexdigest()


def within_limit(key,limit,window=60):
    now=time.monotonic()
    with _LOCK:
        if len(_BUCKETS)>4096:
            for old in list(_BUCKETS):
                if not _BUCKETS[old] or _BUCKETS[old][-1]<now-window:
                    del _BUCKETS[old]
        if key not in _BUCKETS and len(_BUCKETS)>=4096:
            del _BUCKETS[next(iter(_BUCKETS))]
        bucket=_BUCKETS[key]
        while bucket and bucket[0]<=now-window:
            bucket.popleft()
        if len(bucket)>=limit:
            return False
        bucket.append(now)
        return True
