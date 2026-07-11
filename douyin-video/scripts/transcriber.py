"""
并行音频转录器

使用 ThreadPoolExecutor 并行调用 ASR API，支持：
- 3 并发转录，提速 2-3 倍
- SSL 错误自动重试（指数退避）
- 429 速率限制自动退避
- 段顺序保证，错误隔离
- 进度回调
"""

import time
import threading
import requests
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable, List

# 默认参数
DEFAULT_CONCURRENCY = 3
DEFAULT_MAX_RETRIES = 5
RETRY_DELAY_BASE = 3
MIN_REQUEST_INTERVAL = 5  # 请求间最小间隔（秒）


@dataclass
class SegmentResult:
    """单段转录结果"""
    index: int
    success: bool
    text: str = ""
    error: str = ""


@dataclass
class TranscriptionProgress:
    """线程安全的进度跟踪"""
    total: int = 0
    completed: int = 0
    failed: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _last_request_time: float = field(default=0.0)

    def on_segment_done(self, result: SegmentResult):
        with self._lock:
            if result.success:
                self.completed += 1
            else:
                self.failed += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "total": self.total,
                "completed": self.completed,
                "failed": self.failed,
            }

    def acquire_request_slot(self, min_interval: float = MIN_REQUEST_INTERVAL):
        """控制请求频率"""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
            self._last_request_time = time.monotonic()


def _transcribe_segment(
    index: int,
    audio_path: Path,
    api_key: str,
    api_base_url: str,
    model: str,
    max_retries: int,
    progress: TranscriptionProgress,
    timeout: int = 180,
) -> SegmentResult:
    """转录单个音频段，含 SSL/429/超时重试"""
    last_error = ""

    for attempt in range(1, max_retries + 1):
        progress.acquire_request_slot()

        try:
            headers = {"Authorization": f"Bearer {api_key}"}
            with open(audio_path, 'rb') as f:
                files = {
                    'file': (audio_path.name, f, 'audio/mpeg'),
                    'model': (None, model),
                }
                response = requests.post(
                    api_base_url,
                    files=files,
                    headers=headers,
                    timeout=timeout,
                )

            if response.status_code == 429:
                wait = RETRY_DELAY_BASE * (2 ** min(attempt - 1, 4))
                last_error = f"速率限制(429)，等待{wait}s重试"
                time.sleep(wait)
                continue

            response.raise_for_status()
            result = response.json()
            return SegmentResult(index=index, success=True, text=result.get('text', ''))

        except requests.exceptions.SSLError as e:
            # SSL 错误：等一下重试即可
            wait = RETRY_DELAY_BASE * (2 ** min(attempt - 1, 3))
            last_error = f"SSL错误，等待{wait}s重试: {str(e)[:80]}"
            time.sleep(wait)

        except requests.exceptions.Timeout:
            wait = RETRY_DELAY_BASE * (2 ** min(attempt - 1, 2))
            last_error = f"请求超时({timeout}s)，等待{wait}s重试"
            time.sleep(wait)

        except requests.exceptions.ConnectionError as e:
            wait = RETRY_DELAY_BASE * (2 ** min(attempt - 1, 3))
            last_error = f"连接错误，等待{wait}s重试: {str(e)[:80]}"
            time.sleep(wait)

        except Exception as e:
            last_error = str(e)[:200]
            if attempt < max_retries:
                time.sleep(RETRY_DELAY_BASE)

    return SegmentResult(index=index, success=False, error=last_error)


def transcribe_parallel(
    segments: List[Path],
    api_key: str,
    api_base_url: str = "https://api.siliconflow.cn/v1/audio/transcriptions",
    model: str = "FunAudioLLM/SenseVoiceSmall",
    max_workers: int = DEFAULT_CONCURRENCY,
    max_retries: int = DEFAULT_MAX_RETRIES,
    on_progress: Optional[Callable[[dict], None]] = None,
) -> str:
    """
    并行转录多个音频段，按顺序合并结果

    参数:
        segments: 音频文件路径列表（已按顺序排列）
        api_key: API Key
        api_base_url: API 地址
        model: 模型名称
        max_workers: 并发数（默认 3）
        max_retries: 每段最大重试次数（默认 5）
        on_progress: 进度回调，接收 {"total": x, "completed": y, "failed": z}

    返回:
        合并后的完整文本
    """
    if not segments:
        return ""

    # 单段直接串行（无需线程池开销）
    if len(segments) == 1:
        progress = TranscriptionProgress(total=1)
        result = _transcribe_segment(
            0, segments[0], api_key, api_base_url, model, max_retries, progress
        )
        if not result.success:
            raise Exception(f"转录失败: {result.error}")
        return result.text

    progress = TranscriptionProgress(total=len(segments))
    results = [None] * len(segments)  # 预分配，保证顺序

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {}
        for i, seg_path in enumerate(segments):
            future = executor.submit(
                _transcribe_segment,
                i, seg_path, api_key, api_base_url, model, max_retries, progress,
            )
            future_to_index[future] = i

        # 收集结果（as_completed 不保证顺序，用 index 写入正确位置）
        for future in concurrent.futures.as_completed(future_to_index):
            idx = future_to_index[future]
            result = future.result()
            results[idx] = result
            progress.on_segment_done(result)

            if on_progress:
                on_progress(progress.snapshot())

    # 按顺序合并文本
    text_parts = []
    failed_count = 0
    for r in results:
        if r and r.success:
            text_parts.append(r.text)
        elif r:
            failed_count += 1
            text_parts.append(f"\n[第{r.index + 1}段转录失败: {r.error}]\n")

    if failed_count == len(segments):
        raise Exception("所有分段转录均失败")

    return "".join(text_parts)


# 需要显式导入
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
