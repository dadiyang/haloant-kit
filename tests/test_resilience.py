import pytest
from haloant_kit.resilience import retry_async, RetryConfig


@pytest.mark.asyncio
async def test_retry_success_on_second_attempt():
    call_count = 0

    async def fn():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise ValueError("not yet")
        return "ok"

    result = await retry_async(fn, config=RetryConfig(max_attempts=3, backoff_factor=0.01))
    assert result == "ok"
    assert call_count == 2


@pytest.mark.asyncio
async def test_retry_all_fail():
    async def fn():
        raise ValueError("always fail")

    with pytest.raises(ValueError, match="always fail"):
        await retry_async(fn, config=RetryConfig(max_attempts=2, backoff_factor=0.01))


@pytest.mark.asyncio
async def test_retry_success_first_attempt():
    """第一次就成功，不需要重试。"""
    async def fn():
        return 42

    result = await retry_async(fn, config=RetryConfig(max_attempts=3, backoff_factor=0.01))
    assert result == 42


@pytest.mark.asyncio
async def test_retry_timeout():
    """超时场景：fn 耗时超过 config.timeout。"""
    import asyncio

    async def slow_fn():
        await asyncio.sleep(10)

    with pytest.raises(TimeoutError):
        await retry_async(
            slow_fn,
            config=RetryConfig(max_attempts=1, timeout=0.05, backoff_factor=0.01),
        )


@pytest.mark.asyncio
async def test_retry_respects_max_attempts():
    """验证重试次数精确等于 max_attempts。"""
    call_count = 0

    async def fn():
        nonlocal call_count
        call_count += 1
        raise RuntimeError("fail")

    with pytest.raises(RuntimeError):
        await retry_async(fn, config=RetryConfig(max_attempts=4, backoff_factor=0.01))

    assert call_count == 4
