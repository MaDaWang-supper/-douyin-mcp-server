"""
视频源路由器 - 统一入口，根据链接自动识别平台

支持平台：
- 抖音 (douyin.com, iesdouyin.com)
- B站 (bilibili.com, b23.tv, BV号, AV号)

所有平台共用同一套音频提取、语音识别、格式化流程。
"""

import re
from typing import Optional


def extract_url_from_text(text: str) -> str:
    """
    从用户粘贴的任意文本中提取真正的 URL

    用户可能粘贴类似这样的文本：
    - "【标题】https://www.bilibili.com/video/BVxxx"
    - "`https://www.bilibili.com/video/BVxxx`"
    - "标题 https://v.douyin.com/xxx/ 复制此链接"
    - "BV1xx411c7mD"（纯BV号）

    参数:
        text: 用户粘贴的原始文本

    返回:
        str: 提取出的 URL 或视频 ID
    """
    text = text.strip()

    # 纯 BV 号或 AV 号，直接返回
    if re.match(r'^(BV[a-zA-Z0-9]{10,})$', text):
        return text
    if re.match(r'^av(\d+)$', text, re.IGNORECASE):
        return text

    # 提取所有 URL（支持 http/https）
    urls = re.findall(r'https?://[^\s`\'\"<>【】\]]+', text)
    if urls:
        return urls[0]

    # 如果没有找到 URL，尝试查找 BV 号
    bv_match = re.search(r'(BV[a-zA-Z0-9]{10,})', text)
    if bv_match:
        return bv_match.group(1)

    # 如果都没有，返回原文让后续逻辑报错
    return text


def detect_platform(url: str) -> str:
    """
    检测链接所属平台

    参数:
        url: 视频分享链接

    返回:
        str: 平台标识 ('douyin' 或 'bilibili')

    异常:
        ValueError: 不支持的链接
    """
    url_lower = url.lower()

    # B站检测（放在前面，因为BV号没有域名）
    if re.search(r'^(BV[a-zA-Z0-9]{10,})$', url.strip()):
        return 'bilibili'
    if re.search(r'^av(\d+)$', url.strip(), re.IGNORECASE):
        return 'bilibili'
    if 'bilibili.com' in url_lower or 'b23.tv' in url_lower:
        return 'bilibili'

    # 抖音检测
    if 'douyin.com' in url_lower or 'iesdouyin.com' in url_lower:
        return 'douyin'

    raise ValueError(
        f"不支持的链接，目前仅支持抖音和B站视频。\n"
        f"支持的格式：\n"
        f"  抖音: https://v.douyin.com/xxx/\n"
        f"  B站: https://www.bilibili.com/video/BVxxx\n"
        f"  B站: https://b23.tv/xxx\n"
        f"  B站: BVxxx (直接粘贴BV号)"
    )


def get_video_info(url: str) -> dict:
    """统一入口：获取视频信息（无需 API Key）"""
    url = extract_url_from_text(url)
    platform = detect_platform(url)

    if platform == 'bilibili':
        from bilibili_downloader import get_bilibili_video_info
        info = get_bilibili_video_info(url)
        info['platform'] = 'bilibili'
        return info
    else:
        from douyin_downloader import get_video_info
        info = get_video_info(url)
        info['platform'] = 'douyin'
        return info


def extract_text(url: str, api_key: Optional[str] = None,
                 output_dir: Optional[str] = None,
                 save_video: bool = False,
                 show_progress: bool = True) -> dict:
    """统一入口：从视频中提取文案"""
    url = extract_url_from_text(url)
    platform = detect_platform(url)

    if platform == 'bilibili':
        from bilibili_downloader import extract_bilibili_text
        return extract_bilibili_text(
            url, api_key=api_key, output_dir=output_dir,
            save_video=save_video, show_progress=show_progress
        )
    else:
        from douyin_downloader import extract_text
        return extract_text(
            url, api_key=api_key, output_dir=output_dir,
            save_video=save_video, show_progress=show_progress
        )
