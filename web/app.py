#!/usr/bin/env python3
"""
抖音视频文案提取器 WebUI

启动方式:
    cd douyin-mcp-server
    export API_KEY="sk-xxx"
    python web/app.py
    # 访问 http://localhost:8080
"""

import os
import re
import sys
import asyncio
import shutil
from pathlib import Path
from urllib.parse import quote

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent / "douyin-video" / "scripts"))

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import uvicorn
import requests

# 导入视频处理模块（统一路由器，支持抖音+B站）
from video_router import get_video_info, extract_text, detect_platform
from douyin_downloader import format_text_with_llm, HEADERS

app = FastAPI(title="短视频文案提取器", version="2.0.0")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


class VideoRequest(BaseModel):
    """视频请求模型"""
    url: str
    api_key: str = ""  # 可选，从前端传入
    format_output: bool = False  # 是否调用大模型格式化
    llm_config: dict = {}  # 大模型配置（api_base_url, api_key, model_name, prompt）


class VideoInfoResponse(BaseModel):
    """视频信息响应"""
    success: bool
    video_id: str = ""
    title: str = ""
    download_url: str = ""
    error: str = ""


class ExtractResponse(BaseModel):
    """文案提取响应"""
    success: bool
    video_id: str = ""
    title: str = ""
    text: str = ""
    formatted_text: str = ""  # 格式化后的文本
    download_url: str = ""
    error: str = ""


class FormatRequest(BaseModel):
    """格式化请求模型"""
    text: str
    llm_config: dict = {}


class FormatResponse(BaseModel):
    """格式化响应"""
    success: bool
    formatted_text: str = ""
    error: str = ""


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """主页面"""
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/api/health")
async def health_check():
    """健康检查"""
    api_key = os.getenv("API_KEY", "")
    return {
        "status": "ok",
        "api_key_configured": bool(api_key)
    }


@app.post("/api/video/info", response_model=VideoInfoResponse)
async def get_info(req: VideoRequest):
    """获取视频信息（无需 API_KEY）"""
    try:
        info = await asyncio.to_thread(get_video_info, req.url)
        return VideoInfoResponse(
            success=True,
            video_id=info["video_id"],
            title=info["title"],
            download_url=info["url"]
        )
    except Exception as e:
        return VideoInfoResponse(success=False, error=str(e))


@app.post("/api/video/extract", response_model=ExtractResponse)
async def extract_transcript(req: VideoRequest):
    """提取视频文案（需要 API_KEY），可选调用大模型格式化"""
    # 优先使用请求中的 API Key，其次使用环境变量
    api_key = req.api_key or os.getenv("API_KEY", "")
    if not api_key:
        return ExtractResponse(
            success=False,
            error="请先配置 API Key"
        )

    try:
        result = await asyncio.to_thread(
            extract_text, req.url, api_key=api_key, show_progress=False
        )
        raw_text = result["text"]

        # 如果需要格式化
        formatted_text = ""
        if req.format_output and req.llm_config:
            try:
                formatted_text = await asyncio.to_thread(
                    format_text_with_llm, raw_text, req.llm_config, False
                )
            except Exception as e:
                # 格式化失败不影响提取结果，返回原始文本并附带错误信息
                return ExtractResponse(
                    success=True,
                    video_id=result["video_info"]["video_id"],
                    title=result["video_info"]["title"],
                    text=raw_text,
                    formatted_text="",
                    download_url=result["video_info"]["url"],
                    error=f"文案提取成功，但格式化失败: {str(e)}"
                )

        return ExtractResponse(
            success=True,
            video_id=result["video_info"]["video_id"],
            title=result["video_info"]["title"],
            text=raw_text,
            formatted_text=formatted_text,
            download_url=result["video_info"]["url"]
        )
    except Exception as e:
        return ExtractResponse(success=False, error=str(e))


@app.post("/api/video/format", response_model=FormatResponse)
async def format_transcript(req: FormatRequest):
    """使用自定义大模型格式化文案"""
    if not req.llm_config:
        return FormatResponse(success=False, error="请先配置大模型参数")

    try:
        formatted_text = await asyncio.to_thread(
            format_text_with_llm, req.text, req.llm_config, False
        )
        return FormatResponse(success=True, formatted_text=formatted_text)
    except Exception as e:
        return FormatResponse(success=False, error=str(e))


class TestModelRequest(BaseModel):
    """测试模型连接请求"""
    llm_config: dict = {}


class TestModelResponse(BaseModel):
    """测试模型连接响应"""
    success: bool
    message: str = ""
    response_text: str = ""
    error: str = ""


@app.post("/api/model/test", response_model=TestModelResponse)
async def test_model(req: TestModelRequest):
    """测试大模型连接是否正常"""
    if not req.llm_config:
        return TestModelResponse(success=False, error="请先配置大模型参数")

    if not req.llm_config.get("api_base_url"):
        return TestModelResponse(success=False, error="API 地址未填写")
    if not req.llm_config.get("api_key"):
        return TestModelResponse(success=False, error="API Key 未填写")

    try:
        result = await asyncio.to_thread(
            format_text_with_llm, "这是一段测试文本，请回复'连接成功'四个字。", req.llm_config, False
        )
        return TestModelResponse(
            success=True,
            message="模型连接正常",
            response_text=result[:200]
        )
    except Exception as e:
        return TestModelResponse(success=False, error=str(e))


def _content_disposition(filename: str) -> str:
    """构造安全的 Content-Disposition 头（ASCII 回退 + RFC 5987 编码）"""
    ascii_name = re.sub(r'[^A-Za-z0-9._-]', '_', filename) or "video.mp4"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


@app.get("/api/video/download")
async def download_video(video_id: str, filename: str = "video.mp4"):
    """代理下载视频（解决跨域和请求头问题）

    支持抖音视频ID和B站视频ID。
    """
    try:
        platform = detect_platform(video_id)

        if platform == 'bilibili':
            # B站视频：使用 yt-dlp 获取下载链接
            from bilibili_downloader import download_bilibili_audio
            import tempfile
            import subprocess

            bili_url = f"https://www.bilibili.com/video/{video_id}"
            temp_dir = tempfile.mkdtemp(prefix='bilibili_dl_')

            try:
                import yt_dlp

                ydl_opts = {
                    'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                    'outtmpl': str(Path(temp_dir) / 'video.%(ext)s'),
                    'quiet': True,
                    'no_warnings': True,
                    'http_headers': {
                        'User-Agent': HEADERS['User-Agent'],
                        'Referer': 'https://www.bilibili.com/',
                    },
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(bili_url, download=True)
                    video_path = Path(ydl.prepare_filename(info))
                    if not video_path.exists():
                        for f in Path(temp_dir).iterdir():
                            if f.suffix in ('.mp4', '.flv', '.mkv'):
                                video_path = f
                                break

                def iter_file():
                    with open(video_path, 'rb') as f:
                        for chunk in iter(lambda: f.read(8192), b''):
                            yield chunk

                content_length = video_path.stat().st_size

                headers = {
                    "Content-Disposition": _content_disposition(filename),
                }
                if content_length:
                    headers["Content-Length"] = str(content_length)

                return StreamingResponse(
                    iter_file(),
                    media_type="video/mp4",
                    headers=headers
                )
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        else:
            # 抖音视频：原有逻辑
            if not re.fullmatch(r'\d+', video_id):
                raise HTTPException(status_code=400, detail="无效的视频 ID")

            share_url = f"https://www.iesdouyin.com/share/video/{video_id}"
            info = await asyncio.to_thread(get_video_info, share_url)

            download_headers = {
                'User-Agent': HEADERS['User-Agent'],
                'Referer': 'https://www.douyin.com/',
                'Accept': '*/*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Accept-Encoding': 'identity',
                'Connection': 'keep-alive',
            }

            response = await asyncio.to_thread(
                requests.get, info["url"], headers=download_headers, stream=True, allow_redirects=True
            )
            response.raise_for_status()

            content_length = response.headers.get("content-length", "")

            def iter_content():
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk

            headers = {
                "Content-Disposition": _content_disposition(filename),
            }
            if content_length:
                headers["Content-Length"] = content_length

            return StreamingResponse(
                iter_content(),
                media_type="video/mp4",
                headers=headers
            )
    except requests.exceptions.HTTPError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"下载失败: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def main():
    """启动服务"""
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8080"))
    print(f"🚀 启动文案提取器 WebUI: http://localhost:{port}")
    print(f"📝 API_KEY 配置状态: {'已配置' if os.getenv('API_KEY') else '未配置'}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
