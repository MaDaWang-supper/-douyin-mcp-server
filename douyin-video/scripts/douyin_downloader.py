#!/usr/bin/env python3
"""
抖音无水印视频下载和文案提取工具

功能:
1. 从抖音分享链接获取无水印视频下载链接
2. 下载视频并提取音频
3. 使用硅基流动 API 从音频中提取文本
4. 自动保存文案到文件 (一个视频一个文件夹)

环境变量:
- API_KEY: 硅基流动 API 密钥 (用于文案提取功能)

使用示例:
  # 获取下载链接 (无需 API 密钥)
  python douyin_downloader.py --link "抖音分享链接" --action info

  # 下载视频
  python douyin_downloader.py --link "抖音分享链接" --action download --output ./videos

  # 提取文案并保存到文件 (需要 API_KEY 环境变量)
  python douyin_downloader.py --link "抖音分享链接" --action extract --output ./output
"""

import os
import re
import sys
import json
import argparse
import tempfile
import shutil
import subprocess
from pathlib import Path
from typing import Optional
from datetime import datetime

# FFmpeg 完整版路径探测
# 优先使用完整版 FFmpeg（支持 libmp3lame 音频编码）
# 如果找到完整版，设置 PATH 确保所有子进程都能用到
_FFMPEG_FULL_DIR = None
_FFMPEG_BIN = None
_FFPROBE_BIN = None
for _candidate in [
    r'c:\Users\ASUS\.trae-cn\work\ffmpeg\ffmpeg-8.1.2-essentials_build\bin',
    r'c:\ffmpeg\bin',
    r'c:\tools\ffmpeg\bin',
]:
    if os.path.isdir(_candidate):
        _fb = os.path.join(_candidate, 'ffmpeg.exe')
        _fp = os.path.join(_candidate, 'ffprobe.exe')
        if os.path.isfile(_fb):
            _FFMPEG_FULL_DIR = _candidate
            _FFMPEG_BIN = _fb
            _FFPROBE_BIN = _fp if os.path.isfile(_fp) else None
            os.environ['PATH'] = _candidate + os.pathsep + os.environ.get('PATH', '')
            break


def _ffmpeg_cmd():
    """返回完整版 ffmpeg.exe 路径，用于 ffmpeg-python 的 cmd 参数"""
    return _FFMPEG_BIN


def check_dependencies():
    """检查必要的依赖是否已安装"""
    missing = []
    try:
        import requests
    except ImportError:
        missing.append("requests")
    try:
        import ffmpeg
    except ImportError:
        missing.append("ffmpeg-python")

    if missing:
        print(f"缺少依赖: {', '.join(missing)}")
        print(f"请运行: pip install {' '.join(missing)}")
        sys.exit(1)


check_dependencies()

import requests
import ffmpeg

# 请求头，模拟移动端访问
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) EdgiOS/121.0.2277.107 Version/17.0 Mobile/15E148 Safari/604.1'
}

# 硅基流动 API 配置
DEFAULT_API_BASE_URL = "https://api.siliconflow.cn/v1/audio/transcriptions"
DEFAULT_MODEL = "FunAudioLLM/SenseVoiceSmall"


class DouyinProcessor:
    """抖音视频处理器"""

    def __init__(self, api_key: str = "", api_base_url: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key
        self.api_base_url = api_base_url or DEFAULT_API_BASE_URL
        self.model = model or DEFAULT_MODEL
        self.temp_dir = Path(tempfile.mkdtemp())

    def __del__(self):
        """清理临时目录"""
        if hasattr(self, 'temp_dir') and self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def parse_share_url(self, share_text: str) -> dict:
        """从分享文本中提取无水印视频链接"""
        # 提取分享链接
        urls = re.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', share_text)
        if not urls:
            raise ValueError("未找到有效的分享链接")

        share_url = urls[0]
        share_response = requests.get(share_url, headers=HEADERS)
        video_id = share_response.url.split("?")[0].strip("/").split("/")[-1]
        share_url = f'https://www.iesdouyin.com/share/video/{video_id}'

        # 获取视频页面内容
        response = requests.get(share_url, headers=HEADERS)
        response.raise_for_status()

        pattern = re.compile(
            pattern=r"window\._ROUTER_DATA\s*=\s*(.*?)</script>",
            flags=re.DOTALL,
        )
        find_res = pattern.search(response.text)

        if not find_res or not find_res.group(1):
            raise ValueError("从HTML中解析视频信息失败")

        # 解析JSON数据
        json_data = json.loads(find_res.group(1).strip())
        VIDEO_ID_PAGE_KEY = "video_(id)/page"
        NOTE_ID_PAGE_KEY = "note_(id)/page"

        if VIDEO_ID_PAGE_KEY in json_data["loaderData"]:
            original_video_info = json_data["loaderData"][VIDEO_ID_PAGE_KEY]["videoInfoRes"]
        elif NOTE_ID_PAGE_KEY in json_data["loaderData"]:
            original_video_info = json_data["loaderData"][NOTE_ID_PAGE_KEY]["videoInfoRes"]
        else:
            raise Exception("无法从JSON中解析视频或图集信息")

        data = original_video_info["item_list"][0]

        # 获取视频信息
        video_url = data["video"]["play_addr"]["url_list"][0].replace("playwm", "play")
        desc = data.get("desc", "").strip() or f"douyin_{video_id}"

        # 替换文件名中的非法字符
        desc = re.sub(r'[\\/:*?"<>|]', '_', desc)

        return {
            "url": video_url,
            "title": desc,
            "video_id": video_id
        }

    def download_video(self, video_info: dict, output_dir: Optional[Path] = None, show_progress: bool = True) -> Path:
        """下载视频"""
        if output_dir is None:
            output_dir = self.temp_dir
        else:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{video_info['video_id']}.mp4"
        filepath = output_dir / filename

        if show_progress:
            print(f"正在下载视频: {video_info['title']}")

        response = requests.get(video_info['url'], headers=HEADERS, stream=True)
        response.raise_for_status()

        # 获取文件大小
        total_size = int(response.headers.get('content-length', 0))

        # 下载文件
        downloaded = 0
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if show_progress and total_size > 0:
                        progress = downloaded / total_size * 100
                        print(f"\r下载进度: {progress:.1f}%", end="", flush=True)

        if show_progress:
            print(f"\n视频下载完成: {filepath}")
        return filepath

    def extract_audio(self, video_path: Path, show_progress: bool = True) -> Path:
        """从视频文件中提取音频"""
        audio_path = video_path.with_suffix('.mp3')

        if show_progress:
            print("正在提取音频...")
        try:
            (
                ffmpeg
                .input(str(video_path))
                .output(str(audio_path), acodec='libmp3lame', q=0)
                .run(cmd=_ffmpeg_cmd(), capture_stdout=True, capture_stderr=True, overwrite_output=True)
            )
            if show_progress:
                print(f"音频提取完成: {audio_path}")
            return audio_path
        except Exception as e:
            raise Exception(f"提取音频时出错: {str(e)}")

    def get_audio_info(self, audio_path: Path) -> dict:
        """获取音频文件信息（时长和大小）"""
        try:
            probe = ffmpeg.probe(str(audio_path))
            duration = float(probe['format'].get('duration', 0))
            size = audio_path.stat().st_size
            return {'duration': duration, 'size': size}
        except Exception:
            return {'duration': 0, 'size': audio_path.stat().st_size}

    def split_audio(self, audio_path: Path, segment_duration: int = 600, show_progress: bool = True) -> list:
        """
        将音频分割成多个片段

        参数:
            audio_path: 音频文件路径
            segment_duration: 每段时长（秒），默认 10 分钟
            show_progress: 是否显示进度

        返回:
            分割后的音频文件路径列表
        """
        audio_info = self.get_audio_info(audio_path)
        duration = audio_info['duration']

        if duration <= segment_duration:
            return [audio_path]

        segments = []
        segment_index = 0
        current_time = 0

        if show_progress:
            total_segments = int(duration / segment_duration) + 1
            print(f"音频时长 {duration:.0f} 秒，将分割为 {total_segments} 段...")

        while current_time < duration:
            segment_path = self.temp_dir / f"segment_{segment_index}.mp3"

            try:
                (
                    ffmpeg
                    .input(str(audio_path), ss=current_time, t=segment_duration)
                    .output(str(segment_path), acodec='libmp3lame', q=0)
                    .run(cmd=_ffmpeg_cmd(), capture_stdout=True, capture_stderr=True, overwrite_output=True)
                )
                segments.append(segment_path)

                if show_progress:
                    print(f"  分割片段 {segment_index + 1}: {current_time:.0f}s - {min(current_time + segment_duration, duration):.0f}s")

            except Exception as e:
                raise Exception(f"分割音频片段 {segment_index} 时出错: {str(e)}")

            current_time += segment_duration
            segment_index += 1

        return segments

    def transcribe_single_audio(self, audio_path: Path) -> str:
        """转录单个音频文件"""
        files = {
            'file': (audio_path.name, open(audio_path, 'rb'), 'audio/mpeg'),
            'model': (None, self.model)
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }

        try:
            response = requests.post(self.api_base_url, files=files, headers=headers)
            response.raise_for_status()

            result = response.json()
            if 'text' in result:
                return result['text']
            else:
                return response.text

        except Exception as e:
            raise Exception(f"提取文字时出错: {str(e)}")
        finally:
            files['file'][1].close()

    def extract_text_from_audio(self, audio_path: Path, show_progress: bool = True) -> str:
        """从音频文件中提取文字（自动分段 + 并行转录）"""
        if not self.api_key:
            raise ValueError("未设置 API 密钥，请设置环境变量 API_KEY")

        # 检查文件大小和时长
        audio_info = self.get_audio_info(audio_path)
        max_duration = 600   # 10 分钟（降低阈值，更积极地分段以启用并行）
        max_size = 20 * 1024 * 1024  # 20MB

        # 判断是否需要分段
        need_split = audio_info['duration'] > max_duration or audio_info['size'] > max_size

        if not need_split:
            # 短音频直接处理（使用带重试的单段转录）
            if show_progress:
                print("正在识别语音...")
            from transcriber import _transcribe_segment, TranscriptionProgress
            progress = TranscriptionProgress(total=1)
            result = _transcribe_segment(
                0, audio_path, self.api_key, self.api_base_url,
                self.model, max_retries=5, progress=progress,
            )
            if not result.success:
                raise Exception(f"提取文字时出错: {result.error}")
            return result.text

        # 分段 + 并行转录
        if show_progress:
            print(f"音频文件较大（时长: {audio_info['duration']:.0f}秒, 大小: {audio_info['size'] / 1024 / 1024:.1f}MB）")
            print("将自动分段并行处理...")

        # 分割音频（每段9分钟）
        segments = self.split_audio(audio_path, segment_duration=540, show_progress=show_progress)

        # 进度回调
        def on_progress(p):
            if show_progress:
                print(f"\r语音识别进度: {p['completed']}/{p['total']} 段完成, {p['failed']} 段失败", end="", flush=True)

        # 并行转录
        from transcriber import transcribe_parallel
        merged_text = transcribe_parallel(
            segments=segments,
            api_key=self.api_key,
            api_base_url=self.api_base_url,
            model=self.model,
            max_workers=3,
            max_retries=5,
            on_progress=on_progress,
        )

        # 清理分段文件
        for seg in segments:
            if seg != audio_path and seg.exists():
                self.cleanup_files(seg)

        if show_progress:
            print(f"\n语音识别完成，共处理 {len(segments)} 个片段（3并发）")

        return merged_text

    def cleanup_files(self, *file_paths: Path):
        """清理指定的文件"""
        for file_path in file_paths:
            if file_path.exists():
                file_path.unlink()


def get_video_info(share_link: str) -> dict:
    """获取视频信息和下载链接"""
    processor = DouyinProcessor()
    return processor.parse_share_url(share_link)


def download_video(share_link: str, output_dir: str = ".") -> Path:
    """下载视频到指定目录"""
    processor = DouyinProcessor()
    video_info = processor.parse_share_url(share_link)
    return processor.download_video(video_info, Path(output_dir))


def extract_text(share_link: str, api_key: Optional[str] = None, output_dir: Optional[str] = None,
                 save_video: bool = False, show_progress: bool = True) -> dict:
    """
    从视频中提取文案并保存到文件

    返回:
        dict: 包含 video_info, text, output_path 的字典
    """
    api_key = api_key or os.getenv('API_KEY') or os.getenv('DOUYIN_API_KEY')
    if not api_key:
        raise ValueError("未设置环境变量 API_KEY，请先获取硅基流动 API 密钥")

    processor = DouyinProcessor(api_key)

    if show_progress:
        print("正在解析抖音分享链接...")
    video_info = processor.parse_share_url(share_link)

    if show_progress:
        print("正在下载视频...")
    video_path = processor.download_video(video_info, show_progress=show_progress)

    if show_progress:
        print("正在提取音频...")
    audio_path = processor.extract_audio(video_path, show_progress=show_progress)

    if show_progress:
        print("正在从音频中提取文本...")
    text_content = processor.extract_text_from_audio(audio_path, show_progress=show_progress)

    result = {
        "video_info": video_info,
        "text": text_content,
        "output_path": None
    }

    # 保存到文件
    if output_dir:
        output_base = Path(output_dir)
        video_folder = output_base / video_info['video_id']
        video_folder.mkdir(parents=True, exist_ok=True)

        # 保存文案为 Markdown 格式
        transcript_path = video_folder / "transcript.md"
        with open(transcript_path, 'w', encoding='utf-8') as f:
            f.write(f"# {video_info['title']}\n\n")
            f.write(f"| 属性 | 值 |\n")
            f.write(f"|------|----|\n")
            f.write(f"| 视频ID | `{video_info['video_id']}` |\n")
            f.write(f"| 提取时间 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |\n")
            f.write(f"| 下载链接 | [点击下载]({video_info['url']}) |\n\n")
            f.write(f"---\n\n")
            f.write(f"## 文案内容\n\n")
            f.write(text_content)

        result["output_path"] = str(video_folder)

        if show_progress:
            print(f"文案已保存到: {transcript_path}")

        # 保存视频 (可选)
        if save_video:
            saved_video_path = video_folder / f"{video_info['video_id']}.mp4"
            shutil.copy2(video_path, saved_video_path)
            if show_progress:
                print(f"视频已保存到: {saved_video_path}")

    # 清理临时文件
    if show_progress:
        print("正在清理临时文件...")
    processor.cleanup_files(video_path, audio_path)

    return result


def format_text_with_llm(
    raw_text: str,
    model_config: dict,
    show_progress: bool = True
) -> str:
    """
    使用自定义大模型对识别出的文案进行格式化整理

    参数:
        raw_text: 语音识别出的原始文本
        model_config: 大模型配置，包含以下字段:
            - api_base_url: API 地址 (如 https://api.openai.com/v1/chat/completions)
            - api_key: API 密钥
            - model_name: 模型名称 (如 gpt-4o, deepseek-chat, qwen-turbo 等)
            - prompt: 自定义提示词 (可选，有默认值)
        show_progress: 是否显示进度

    返回:
        str: 格式化后的文本
    """
    api_base_url = model_config.get("api_base_url", "")
    api_key = model_config.get("api_key", "")
    model_name = model_config.get("model_name", "gpt-4o")

    if not api_base_url:
        raise ValueError("未配置大模型 API 地址")
    if not api_key:
        raise ValueError("未配置大模型 API 密钥")

    # 默认提示词（专为短视频口语化文案格式化设计）
    default_prompt = (
        "你是一位资深的内容编辑和文字润色专家。你的任务是将从短视频中提取的口语化语音识别文案，"
        "转化为一篇结构清晰、语言规范、适合阅读传播的专业文章。\n\n"
        "## 处理规则\n\n"
        "### 1. 语言净化（必须执行）\n"
        "- 删除所有语气词、填充词和冗余重复：'嗯''啊''哎''哎呀''那''呢''其实''相对来说''也是可以的'等\n"
        "- 删除重复的词汇和句子：如'今天大家今天'等口误或重复表达\n"
        "- 修正所有明显的错别字和形近字错误\n"
        "- 修正专业术语错误，确保用词准确严谨\n"
        "- 将口语化、随意的表达转化为书面语，保持专业度\n\n"
        "### 2. 语句重构（必须执行）\n"
        "- 修正病句和表意不通的句子，确保每句话逻辑清晰、语义完整\n"
        "- 合并碎句和半截话，形成完整、连贯的长句\n"
        "- 调整语序，使表达更加流畅自然\n"
        "- 补充隐含的主语、宾语，让句子成分完整\n"
        "- 删除前后矛盾的表述，修正不符合常识或常规的内容\n\n"
        "### 3. 结构重组（必须执行）\n"
        "- 根据内容主题进行合理分段，每个段落聚焦一个核心观点\n"
        "- 为每个主要部分添加清晰的小标题（使用 Markdown 格式）\n"
        "- 建立清晰的层级结构：大主题 → 子主题 → 具体要点\n"
        "- 同类内容归并到一起，不要分散穿插\n"
        "- 按逻辑顺序排列内容（如：总→分→总、时间线、重要性排序等）\n\n"
        "### 4. 信息提炼（必须执行）\n"
        "- 提炼每段的核心观点，删除冗余的解释和重复的内容\n"
        "- 将零散的信息点整理为有条理的列表（使用序号或项目符号）\n"
        "- 突出核心方法和关键结论，让读者一眼抓住重点\n"
        "- 补充适当的过渡句，使段落之间衔接自然\n\n"
        "### 5. 格式规范（必须执行）\n"
        "- 正确使用标点符号，避免一逗到底或缺少标点\n"
        "- 对关键概念、重点内容进行加粗（使用 Markdown **加粗**）\n"
        "- 适当使用序号（1. 2. 3.）和层级标题（## ###）\n"
        "- 在文章末尾添加简洁的总结和行动指引\n\n"
        "## 输出要求\n"
        "- 直接输出整理后的完整文本，不要添加任何解释、点评或元数据\n"
        "- 不要输出'以下是整理后的内容'之类的过渡语\n"
        "- 保持原文的核心信息和观点，不要删减重要内容\n"
        "- 保持原文的语言风格倾向（如：干货科普、经验分享、教程指导等），但去除口语化痕迹\n\n"
        "## 原始文案\n\n"
        "{text}"
    )

    prompt_template = model_config.get("prompt", "") or default_prompt
    prompt = prompt_template.format(text=raw_text)

    if show_progress:
        print("正在调用大模型格式化文案...")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "你是一个专业的文字编辑，擅长整理和格式化文本内容。"},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": 8192
    }

    try:
        response = requests.post(
            api_base_url,
            headers=headers,
            json=payload,
            timeout=120
        )
        response.raise_for_status()

        result = response.json()
        formatted_text = result["choices"][0]["message"]["content"].strip()

        if show_progress:
            print("文案格式化完成")

        return formatted_text

    except requests.exceptions.Timeout:
        raise Exception("调用大模型超时，请检查网络或模型配置")
    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code if hasattr(e, 'response') else 'unknown'
        error_detail = ""
        try:
            error_detail = e.response.json()
        except Exception:
            error_detail = e.response.text[:500] if hasattr(e, 'response') else str(e)
        raise Exception(f"调用大模型失败 (HTTP {status_code}): {error_detail}")
    except Exception as e:
        raise Exception(f"格式化文案时出错: {str(e)}")


def main():
    parser = argparse.ArgumentParser(
        description="抖音无水印视频下载和文案提取工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 获取视频信息和下载链接
  python douyin_downloader.py --link "抖音分享链接" --action info

  # 下载视频
  python douyin_downloader.py --link "抖音分享链接" --action download --output ./videos

  # 提取文案并保存到文件 (需要设置 API_KEY 环境变量)
  python douyin_downloader.py --link "抖音分享链接" --action extract --output ./output

  # 提取文案并同时保存视频
  python douyin_downloader.py --link "抖音分享链接" --action extract --output ./output --save-video
        """
    )

    parser.add_argument("--link", "-l", required=True, help="抖音分享链接或包含链接的文本")
    parser.add_argument("--action", "-a", choices=["info", "download", "extract"],
                        default="info", help="操作类型: info(获取信息), download(下载视频), extract(提取文案)")
    parser.add_argument("--output", "-o", default="./output", help="输出目录 (默认 ./output)")
    parser.add_argument("--api-key", "-k", help="硅基流动 API 密钥 (也可通过 API_KEY 环境变量设置)")
    parser.add_argument("--save-video", "-v", action="store_true", help="提取文案时同时保存视频")
    parser.add_argument("--quiet", "-q", action="store_true", help="安静模式，减少输出")

    args = parser.parse_args()

    try:
        if args.action == "info":
            info = get_video_info(args.link)
            print("\n" + "=" * 50)
            print("视频信息:")
            print("=" * 50)
            print(f"视频ID: {info['video_id']}")
            print(f"标题: {info['title']}")
            print(f"下载链接: {info['url']}")
            print("=" * 50)

        elif args.action == "download":
            video_path = download_video(args.link, args.output)
            print(f"\n视频已保存到: {video_path}")

        elif args.action == "extract":
            result = extract_text(
                args.link,
                args.api_key,
                output_dir=args.output,
                save_video=args.save_video,
                show_progress=not args.quiet
            )

            if not args.quiet:
                print("\n" + "=" * 50)
                print("提取完成!")
                print("=" * 50)
                print(f"视频ID: {result['video_info']['video_id']}")
                print(f"标题: {result['video_info']['title']}")
                if result['output_path']:
                    print(f"保存位置: {result['output_path']}")
                print("=" * 50)
                print("\n文案内容:\n")
                print(result['text'][:500] + "..." if len(result['text']) > 500 else result['text'])
                print("\n" + "=" * 50)

    except Exception as e:
        print(f"\n错误: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
