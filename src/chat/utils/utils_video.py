#!/usr/bin/env python3
"""纯 inkfox 视频关键帧分析工具

仅依赖 `inkfox.video` 提供的 Rust 扩展能力：
    - extract_keyframes_from_video
    - get_system_info

功能：
    - 关键帧提取 (base64, timestamp)
    - 批量 / 逐帧 LLM 描述
    - 自动模式 (<=3 帧批量，否则逐帧)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from PIL import Image

from src.common.database.core.models import Videos, get_db_session  # type: ignore
from src.common.logger import get_logger
from src.config.config import global_config, model_config
from src.llm_models.utils_model import LLMRequest

# 简易并发控制：同一 hash 只处理一次
_video_locks: dict[str, asyncio.Lock] = {}
_locks_guard = asyncio.Lock()

logger = get_logger("utils_video")

from inkfox import video


class VideoAnalyzer:
    """基于 inkfox 的视频关键帧 + LLM 描述分析器"""

    def __init__(self) -> None:
        cfg = getattr(global_config, "video_analysis", object())
        self.max_frames: int = getattr(cfg, "max_frames", 20)
        self.frame_quality: int = getattr(cfg, "frame_quality", 85)
        self.max_image_size: int = getattr(cfg, "max_image_size", 600)
        self.enable_frame_timing: bool = getattr(cfg, "enable_frame_timing", True)
        self.use_simd: bool = getattr(cfg, "rust_use_simd", True)
        self.threads: int = getattr(cfg, "rust_threads", 0)
        self.ffmpeg_path: str = getattr(cfg, "ffmpeg_path", "ffmpeg")
        self.analysis_mode: str = getattr(cfg, "analysis_mode", "auto")
        self.frame_analysis_delay: float = 0.3

        # 人格与提示模板
        try:
            persona = global_config.personality
            self.personality_core = getattr(persona, "personality_core", "是一个积极向上的女大学生")
            self.personality_side = getattr(persona, "personality_side", "用一句话或几句话描述人格的侧面特点")
        except Exception:  # pragma: no cover
            self.personality_core = "是一个积极向上的女大学生"
            self.personality_side = "用一句话或几句话描述人格的侧面特点"

        self.batch_analysis_prompt = getattr(
            cfg,
            "batch_analysis_prompt",
            """请以第一人称视角阅读这些按时间顺序提取的关键帧。\n核心：{personality_core}\n人格：{personality_side}\n请详细描述视频(主题/人物与场景/动作与时间线/视觉风格/情绪氛围/特殊元素)。""",
        )

        try:
            self.video_llm = LLMRequest(
                model_set=model_config.model_task_config.video_analysis, request_type="video_analysis"
            )
        except Exception:
            self.video_llm = LLMRequest(model_set=model_config.model_task_config.vlm, request_type="vlm")

        self._log_system()

    # ---- 系统信息 ----
    def _log_system(self) -> None:
        try:
            info = video.get_system_info()  # type: ignore[attr-defined]
            logger.info(
                f"inkfox: threads={info.get('threads')} version={info.get('version')} simd={info.get('simd_supported')}"
            )
        except Exception as e:  # pragma: no cover
            logger.debug(f"获取系统信息失败: {e}")

    # ---- 关键帧提取 ----
    async def extract_keyframes(self, video_path: str) -> list[tuple[str, float]]:
        """提取关键帧并返回 (base64, timestamp_seconds) 列表"""
        with tempfile.TemporaryDirectory() as tmp:
            result = video.extract_keyframes_from_video(  # type: ignore[attr-defined]
                video_path=video_path,
                output_dir=tmp,
                max_keyframes=self.max_frames * 2,  # 先多抓一点再截断
                max_save=self.max_frames,
                ffmpeg_path=self.ffmpeg_path,
                use_simd=self.use_simd,
                threads=self.threads,
                verbose=False,
            )
            files = sorted(Path(tmp).glob("keyframe_*.jpg"))[: self.max_frames]
            total_ms = getattr(result, "total_time_ms", 0)
            frames: list[tuple[str, float]] = []
            for i, f in enumerate(files):
                img = Image.open(f).convert("RGB")
                if max(img.size) > self.max_image_size:
                    scale = self.max_image_size / max(img.size)
                    img = img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=self.frame_quality)
                b64 = base64.b64encode(buf.getvalue()).decode()
                ts = (i / max(1, len(files) - 1)) * (total_ms / 1000.0) if total_ms else float(i)
                frames.append((b64, ts))
            return frames

    # ---- 批量分析 ----
    async def _analyze_batch(self, frames: list[tuple[str, float]], question: str | None) -> str:
        from src.llm_models.payload_content.message import MessageBuilder, RoleType
        from src.llm_models.utils_model import RequestType

        prompt = self.batch_analysis_prompt.format(
            personality_core=self.personality_core, personality_side=self.personality_side
        )
        if question:
            prompt += f"\n用户关注: {question}"
        desc = [
            (f"第{i + 1}帧 (时间: {ts:.2f}s)" if self.enable_frame_timing else f"第{i + 1}帧")
            for i, (_b, ts) in enumerate(frames)
        ]
        prompt += "\n帧列表: " + ", ".join(desc)
        mb = MessageBuilder().set_role(RoleType.User).add_text_content(prompt)
        for b64, _ in frames:
            mb.add_image_content("jpeg", b64)
        message = mb.build()
        model_info, api_provider, client = self.video_llm._select_model()
        resp = await self.video_llm._execute_request(
            api_provider=api_provider,
            client=client,
            request_type=RequestType.RESPONSE,
            model_info=model_info,
            message_list=[message],
            temperature=None,
            max_tokens=None,
        )
        return resp.content or "❌ 未获得响应"

    # ---- 逐帧分析 ----
    async def _analyze_sequential(self, frames: list[tuple[str, float]], question: str | None) -> str:
        results: list[str] = []
        for i, (b64, ts) in enumerate(frames):
            prompt = f"分析第{i + 1}帧" + (f" (时间: {ts:.2f}s)" if self.enable_frame_timing else "")
            if question:
                prompt += f"\n关注: {question}"
            try:
                text, _ = await self.video_llm.generate_response_for_image(
                    prompt=prompt, image_base64=b64, image_format="jpeg"
                )
                results.append(f"第{i + 1}帧: {text}")
            except Exception as e:  # pragma: no cover
                results.append(f"第{i + 1}帧: 失败 {e}")
            if i < len(frames) - 1:
                await asyncio.sleep(self.frame_analysis_delay)
        summary_prompt = "基于以下逐帧结果给出完整总结:\n\n" + "\n".join(results)
        try:
            final, _ = await self.video_llm.generate_response_for_image(
                prompt=summary_prompt, image_base64=frames[-1][0], image_format="jpeg"
            )
            return final
        except Exception:  # pragma: no cover
            return "\n".join(results)

    # ---- 主入口 ----
    async def analyze_video(self, video_path: str, question: str | None = None) -> tuple[bool, str]:
        if not os.path.exists(video_path):
            return False, "❌ 文件不存在"
        frames = await self.extract_keyframes(video_path)
        if not frames:
            return False, "❌ 未提取到关键帧"
        mode = self.analysis_mode
        if mode == "auto":
            mode = "batch" if len(frames) <= 20 else "sequential"
        text = await (
            self._analyze_batch(frames, question) if mode == "batch" else self._analyze_sequential(frames, question)
        )
        return True, text

    async def analyze_video_from_bytes(
        self,
        video_bytes: bytes,
        filename: str | None = None,
        prompt: str | None = None,
        question: str | None = None,
    ) -> dict[str, str]:
        """从内存字节分析视频，兼容旧调用 (prompt / question 二选一) 返回 {"summary": str}."""
        if not video_bytes:
            return {"summary": "❌ 空视频数据"}
        # 兼容参数：prompt 优先，其次 question
        q = prompt if prompt is not None else question
        video_hash = hashlib.sha256(video_bytes).hexdigest()

        # 查缓存
        try:
            async with get_db_session() as session:  # type: ignore
                row = await session.execute(
                    Videos.__table__.select().where(Videos.video_hash == video_hash)  # type: ignore
                )
                existing = row.first()
                if existing and existing[Videos.description] and existing[Videos.vlm_processed]:  # type: ignore
                    return {"summary": existing[Videos.description]}  # type: ignore
        except Exception:  # pragma: no cover
            pass

        # 获取锁避免重复处理
        async with _locks_guard:
            lock = _video_locks.get(video_hash)
            if lock is None:
                lock = asyncio.Lock()
                _video_locks[video_hash] = lock
        async with lock:
            # 双检：进入锁后再查一次，避免重复处理
            try:
                async with get_db_session() as session:  # type: ignore
                    row = await session.execute(
                        Videos.__table__.select().where(Videos.video_hash == video_hash)  # type: ignore
                    )
                    existing = row.first()
                    if existing and existing[Videos.description] and existing[Videos.vlm_processed]:  # type: ignore
                        return {"summary": existing[Videos.description]}  # type: ignore
            except Exception:  # pragma: no cover
                pass

            try:
                with tempfile.NamedTemporaryFile(delete=False) as fp:
                    fp.write(video_bytes)
                    temp_path = fp.name
                try:
                    ok, summary = await self.analyze_video(temp_path, q)
                    # 写入缓存（仅成功）
                    if ok:
                        try:
                            async with get_db_session() as session:  # type: ignore
                                await session.execute(
                                    Videos.__table__.insert().values(
                                        video_id="",
                                        video_hash=video_hash,
                                        description=summary,
                                        count=1,
                                        timestamp=time.time(),
                                        vlm_processed=True,
                                        duration=None,
                                        frame_count=None,
                                        fps=None,
                                        resolution=None,
                                        file_size=len(video_bytes),
                                    )
                                )
                                await session.commit()
                        except Exception:  # pragma: no cover
                            pass
                    return {"summary": summary}
                finally:
                    if os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except Exception:  # pragma: no cover
                            pass
            except Exception as e:  # pragma: no cover
                return {"summary": f"❌ 处理失败: {e}"}


# ---- 外部接口 ----
_INSTANCE: VideoAnalyzer | None = None


def get_video_analyzer() -> VideoAnalyzer:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = VideoAnalyzer()
    return _INSTANCE


def is_video_analysis_available() -> bool:
    return True


def get_video_analysis_status() -> dict[str, Any]:
    try:
        info = video.get_system_info()  # type: ignore[attr-defined]
    except Exception as e:  # pragma: no cover
        return {"available": False, "error": str(e)}
    inst = get_video_analyzer()
    return {
        "available": True,
        "system": info,
        "modes": ["auto", "batch", "sequential"],
        "max_frames_default": inst.max_frames,
        "implementation": "inkfox",
    }
