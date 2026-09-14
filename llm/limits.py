"""Distributed model concurrency leases and a global sliding one-minute budget."""
from contextlib import contextmanager
import os
import time
from uuid import uuid4

ACQUIRE = """
local t=redis.call('TIME'); local now=t[1]*1000+math.floor(t[2]/1000)
redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',now)
if redis.call('ZCARD',KEYS[1])>=tonumber(ARGV[2]) then return 0 end
redis.call('ZADD',KEYS[1],now+120000,ARGV[1]); redis.call('PEXPIRE',KEYS[1],180000)
return 1
"""

RATE = """
local t=redis.call('TIME'); local now=t[1]*1000+math.floor(t[2]/1000)
redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',now-60000)
if redis.call('ZCARD',KEYS[1])>=tonumber(ARGV[2]) then return 0 end
redis.call('ZADD',KEYS[1],now,ARGV[1]); redis.call('PEXPIRE',KEYS[1],120000)
return 1
"""


def reserve_request():
    if os.getenv("LLM_DISTRIBUTED_LIMITS", "false").lower() != "true":
        return
    from services.broker import client
    from llm.client import DeepSeekUnavailableError
    if not client().eval(RATE, 1, "qa:llm:minute", uuid4().hex,
                         int(os.getenv("LLM_REQUESTS_PER_MINUTE", "60"))):
        raise DeepSeekUnavailableError("Model request budget exhausted; retry later")


@contextmanager
def model_slot():
    if os.getenv("LLM_DISTRIBUTED_LIMITS", "false").lower() != "true":
        yield
        return
    from services.broker import client
    from llm.client import DeepSeekUnavailableError
    token = uuid4().hex
    deadline = time.monotonic() + 5
    acquired = False
    try:
        while time.monotonic() < deadline:
            acquired = bool(client().eval(ACQUIRE, 1, "qa:llm:active", token,
                                         int(os.getenv("LLM_MAX_CONCURRENCY", "4"))))
            if acquired:
                break
            time.sleep(.1)
        if not acquired:
            raise DeepSeekUnavailableError("Model capacity exhausted; inspection requires retry")
        yield
    finally:
        if acquired:
            try:
                client().zrem("qa:llm:active", token)
            except Exception:
                # Lease expires even if Redis becomes unavailable during I/O.
                pass
