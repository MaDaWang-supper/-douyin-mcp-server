"""
B站视频解析和下载模块

使用 yt-dlp 提取视频信息和下载音频，支持多种链接格式：
- 完整URL: https://www.bilibili.com/video/BV1ScfFBZE3y
- 短链接: https://b23.tv/abc123
- BV号: BV1ScfFBZE3y
- AV号: av123456
"""

import re
import os
import json
import shutil
import tempfile
import subprocess
from pathlib import Path
from typing import Optional
from datetime import datetime

# FFmpeg 完整版路径探测（与 douyin_downloader 保持一致）
_FFMPEG_FULL_DIR = None
for _candidate in [
    r'c:\Users\ASUS\.trae-cn\work\ffmpeg\ffmpeg-8.1.2-essentials_build\bin',
    r'c:\ffmpeg\bin',
    r'c:\tools\ffmpeg\bin',
]:
    if os.path.isdir(_candidate):
        _ffmpeg_bin = os.path.join(_candidate, 'ffmpeg.exe')
        if os.path.isfile(_ffmpeg_bin):
            _FFMPEG_FULL_DIR = _candidate
            os.environ['PATH'] = _candidate + os.pathsep + os.environ.get('PATH', '')
            break


# 链接解析正则
BILIBILI_PATTERNS = [
    re.compile(r'bilibili\.com/video/(BV[a-zA-Z0-9]+)'),
    re.compile(r'b23\.tv/([a-zA-Z0-9]+)'),
    re.compile(r'^(BV[a-zA-Z0-9]{10,})$'),
    re.compile(r'^av(\d+)$'),
    re.compile(r'bilibili\.com/video/(av\d+)'),
]


def is_bilibili_url(url: str) -> bool:
    """判断是否为B站链接"""
    for pattern in BILIBILI_PATTERNS:
        if pattern.search(url):
            return True
    return False


def parse_bilibili_url(url: str) -> str:
    """
    从B站链接中提取视频ID（BV号或AV号）

    参数:
        url: B站视频链接，支持完整URL、短链接、BV号、AV号

    返回:
        str: 视频ID（如 BV1ScfFBZE3y 或 av123456）
    """
    # 直接是BV号
    bv_match = re.match(r'^(BV[a-zA-Z0-9]{10,})$', url.strip())
    if bv_match:
        return bv_match.group(1)

    # 直接是AV号
    av_match = re.match(r'^av(\d+)$', url.strip(), re.IGNORECASE)
    if av_match:
        return f"av{av_match.group(1)}"

    # 从完整URL中提取
    for pattern in BILIBILI_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)

    raise ValueError(f"无法从链接中提取B站视频ID: {url}")


def resolve_bilibili_short_url(short_url: str) -> str:
    """
    解析B站短链接（b23.tv），获取实际URL

    参数:
        short_url: B站短链接

    返回:
        str: 重定向后的实际URL
    """
    import requests

    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://www.bilibili.com/',
    }

    try:
        response = requests.get(short_url, headers=HEADERS, allow_redirects=True, timeout=15)
        return response.url
    except Exception:
        raise ValueError(f"无法解析B站短链接: {short_url}")


def get_bilibili_video_info(url: str) -> dict:
    """
    获取B站视频信息（标题、视频ID、时长、作者等）

    参数:
        url: B站视频链接

    返回:
        dict: 视频信息 {
            'video_id': 视频ID,
            'title': 视频标题,
            'url': 视频页面URL,
            'duration': 视频时长(秒),
            'uploader': 上传者,
            'description': 视频简介
        }
    """
    try:
        import yt_dlp
    except ImportError:
        raise ImportError("请先安装 yt-dlp: pip install yt-dlp")

    # 解析短链接
    if 'b23.tv' in url:
        url = resolve_bilibili_short_url(url)

    video_id = parse_bilibili_url(url)

    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,  # 只提取信息，不下载
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        raise ValueError(f"无法获取B站视频信息: {url}")

    # 构建返回数据，与抖音格式对齐
    title = info.get('title', f'bilibili_{video_id}')
    # 替换文件名中的非法字符
    title = re.sub(r'[\\/:*?"<>|]', '_', title)

    return {
        'video_id': video_id,
        'title': title,
        'url': info.get('webpage_url', url),
        'duration': info.get('duration', 0),
        'uploader': info.get('uploader', ''),
        'description': info.get('description', ''),
    }


def download_bilibili_audio(url: str, output_dir: Optional[str] = None, show_progress: bool = True) -> Path:
    """
    使用 yt-dlp 下载B站视频音频（比下载完整视频快 300 倍）

    参数:
        url: B站视频链接
        output_dir: 输出目录，默认为临时目录
        show_progress: 是否显示进度

    返回:
        Path: 下载的音频文件路径（mp3 格式）
    """
    try:
        import yt_dlp
    except ImportError:
        raise ImportError("请先安装 yt-dlp: pip install yt-dlp")

    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix='bilibili_')
    else:
        os.makedirs(output_dir, exist_ok=True)

    output_dir = Path(output_dir)

    # 解析短链接
    if 'b23.tv' in url:
        url = resolve_bilibili_short_url(url)

    if show_progress:
        print("正在下载B站音频（仅音频流，速度很快）...")

    # 使用安全的文件名模板
    safe_id = 'bili_audio'
    output_template = str(output_dir / ('%(id)s.%(ext)s'))

    # 先下载原始音频（不转换），再用 ffmpeg-python 转为 mp3
    # 避免 yt-dlp 的 FFmpegExtractAudio postprocessor 在 Windows 上的路径问题
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_template,
        # 不使用 postprocessors，手动转换
        'quiet': not show_progress,
        'no_warnings': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://www.bilibili.com/',
        },
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # 找到下载的文件
        raw_audio = None
        for f in output_dir.iterdir():
            if f.is_file() and f.suffix in ('.m4a', '.webm', '.opus', '.aac', '.flac', '.wav'):
                raw_audio = f
                break
        if not raw_audio:
            # yt-dlp prepare_filename 回退
            raw_audio = Path(ydl.prepare_filename(info))
            if not raw_audio.exists():
                for f in output_dir.iterdir():
                    if f.is_file() and f.suffix != '.mp3':
                        raw_audio = f
                        break

    if not raw_audio or not raw_audio.exists():
        raise FileNotFoundError("音频下载后文件未找到")

    # 用 ffmpeg-python 转换为 mp3（复用项目现有的转换逻辑）
    audio_path = raw_audio.with_suffix('.mp3')
    if show_progress:
        print(f"正在转换音频格式: {raw_audio.suffix} -> mp3")

    # 导入 douyin_downloader 的 ffmpeg 路径探测
    from douyin_downloader import _ffmpeg_cmd

    import ffmpeg
    (
        ffmpeg
        .input(str(raw_audio))
        .output(str(audio_path), acodec='libmp3lame', q=0)
        .run(cmd=_ffmpeg_cmd(), capture_stdout=True, capture_stderr=True, overwrite_output=True)
    )

    # 删除原始音频文件
    if raw_audio != audio_path and raw_audio.exists():
        raw_audio.unlink()

    if not audio_path.exists():
        raise FileNotFoundError("音频转换失败")

    if show_progress:
        print(f"B站音频下载完成: {audio_path}")

    return audio_path


def extract_bilibili_text(url: str, api_key: Optional[str] = None,
                           output_dir: Optional[str] = None,
                           save_video: bool = False,
                           show_progress: bool = True) -> dict:
    """
    从B站视频中提取文案（完整流程）

    参数:
        url: B站视频链接
        api_key: 语音识别 API Key
        output_dir: 输出目录
        save_video: 是否保存视频文件
        show_progress: 是否显示进度

    返回:
        dict: 包含 video_info, text, output_path 的字典
    """
    from douyin_downloader import DouyinProcessor as AudioProcessor

    api_key = api_key or os.getenv('API_KEY') or os.getenv('DASHSCOPE_API_KEY')
    if not api_key:
        raise ValueError("未设置环境变量 API_KEY，请先获取硅基流动 API 密钥")

    if show_progress:
        print("正在解析B站视频链接...")
    video_info = get_bilibili_video_info(url)

    if show_progress:
        print(f"视频标题: {video_info['title']}")
        print("正在下载音频...")

    # 使用 yt-dlp 直接下载音频（比下载视频再提取快 300 倍）
    audio_path = download_bilibili_audio(url, show_progress=show_progress)

    # 如果需要保存视频
    video_path = None
    if save_video:
        if show_progress:
            print("正在下载视频...")
        try:
            import yt_dlp
            video_output_dir = tempfile.mkdtemp(prefix='bilibili_video_')
            video_template = str(Path(video_output_dir) / '%(id)s.%(ext)s')

            ydl_opts = {
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'outtmpl': video_template,
                'quiet': not show_progress,
                'no_warnings': True,
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Referer': 'https://www.bilibili.com/',
                },
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                video_path = Path(ydl.prepare_filename(info))
                if not video_path.exists():
                    for f in Path(video_output_dir).iterdir():
                        if f.suffix in ('.mp4', '.flv'):
                            video_path = f
                            break
        except Exception as e:
            if show_progress:
                print(f"视频下载失败（不影响文案提取）: {e}")

    if show_progress:
        print("正在从音频中提取文本...")

    asr = AudioProcessor(api_key)
    text_content = asr.extract_text_from_audio(audio_path, show_progress=show_progress)

    result = {
        "video_info": video_info,
        "text": text_content,
        "output_path": None,
    }

    # 保存到文件
    if output_dir:
        output_base = Path(output_dir)
        video_folder = output_base / video_info['video_id']
        video_folder.mkdir(parents=True, exist_ok=True)

        transcript_path = video_folder / "transcript.md"
        with open(transcript_path, 'w', encoding='utf-8') as f:
            f.write(f"# {video_info['title']}\n\n")
            f.write(f"| 属性 | 值 |\n")
            f.write(f"|------|------|\n")
            f.write(f"| 视频ID | {video_info['video_id']} |\n")
            f.write(f"| 来源 | B站 |\n")
            if video_info.get('duration'):
                duration_min = video_info['duration'] / 60
                f.write(f"| 时长 | {duration_min:.1f} 分钟 |\n")
            if video_info.get('uploader'):
                f.write(f"| UP主 | {video_info['uploader']} |\n")
            f.write(f"| 链接 | {video_info['url']} |\n")
            f.write(f"| 提取时间 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |\n\n")
            f.write("---\n\n")
            f.write(text_content)

        result['output_path'] = str(transcript_path)
        if show_progress:
            print(f"文案已保存到: {transcript_path}")

        if video_path and video_path.exists():
            import shutil as _shutil
            dest = video_folder / f"{video_info['video_id']}.mp4"
            _shutil.copy2(str(video_path), str(dest))
            if show_progress:
                print(f"视频已保存到: {dest}")

    return result
