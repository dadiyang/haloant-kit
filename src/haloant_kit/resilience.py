"""重试 + 超时 + 指数退避。"""
import asyncio
import logging
import random
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RetryConfig:
    max_attempts: int = 3
    timeout: float = 30.0
    backoff_factor: float = 2.0
    max_delay: float = 60.0
    jitter: bool = True


async def retry_async(fn, *, config: RetryConfig | None = None, logger_override=None):
    """异步重试：指数退避 + jitter + asyncio.wait_for 超时。

    fn: async callable（无参数），每次重试调用一次。
    返回 fn 的返回值，所有重试失败后 raise 最后一次异常。
    """
    cfg = config or RetryConfig()
    log = logger_override or logger
    last_exc = None

    for attempt in range(1, cfg.max_attempts + 1):
        try:
            return await asyncio.wait_for(fn(), timeout=cfg.timeout)
        except asyncio.TimeoutError:
            last_exc = TimeoutError(
                f"Attempt {attempt}/{cfg.max_attempts} timed out after {cfg.timeout}s"
            )
            log.warning("retry_async attempt %d/%d: timeout after %.1fs",
                        attempt, cfg.max_attempts, cfg.timeout)
        except Exception as e:
            last_exc = e
            log.warning("retry_async attempt %d/%d: %s: %s",
                        attempt, cfg.max_attempts, type(e).__name__, e)

        if attempt < cfg.max_attempts:
            delay = min(cfg.backoff_factor ** (attempt - 1), cfg.max_delay)
            if cfg.jitter:
                delay *= (0.5 + random.random())
            log.debug("retry_async: sleeping %.1fs before attempt %d", delay, attempt + 1)
            await asyncio.sleep(delay)

    assert last_exc is not None, "unreachable: max_attempts >= 1"
    raise last_exc
