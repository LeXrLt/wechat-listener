"""
DAT 数据解密服务模块。
提供微信数据库、图片等数据的解密与还原能力。
"""

import asyncio
import heapq
import logging
import os
import shutil
import threading
import time
from concurrent.futures import Future as ConcurrentFuture
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Set
from collections import deque
import inspect

import aiofiles
import aiofiles.os
from omni_bot_sdk.common.config import Config
from omni_bot_sdk.models import UserInfo
from omni_bot_sdk.utils.fuck_zxl import decrypt_dat, find_key
from watchfiles import Change, awatch

# 配置日志
logging.getLogger("watchfiles").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

WXGF_HEADER = b"wxgf"
WXGF_MIN_ANIME_MAX_RATIO = 0.6
WXGF_NALU_PATTERNS = (b"\x00\x00\x00\x01", b"\x00\x00\x01")
ENV_FFMPEG_PATH = "FFMPEG_PATH"


@dataclass
class _WxgfPartition:
    offset: int
    size: int
    ratio: float


def _find_ffmpeg_path() -> Optional[str]:
    configured_path = os.getenv(ENV_FFMPEG_PATH)
    if configured_path:
        candidate = Path(configured_path)
        if candidate.is_dir():
            executable = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
            return str(candidate / executable)
        return configured_path
    return shutil.which("ffmpeg")


def _find_wxgf_partitions(data: bytes) -> list[_WxgfPartition]:
    if len(data) < 15 or data[:4] != WXGF_HEADER:
        raise ValueError("invalid wxgf")

    header_len = data[4]
    if header_len >= len(data):
        raise ValueError("invalid wxgf header length")

    for pattern in WXGF_NALU_PATTERNS:
        partitions: list[_WxgfPartition] = []
        offset = 0

        while header_len + offset <= len(data):
            search_start = header_len + offset
            abs_index = data.find(pattern, search_start)
            if abs_index == -1:
                break

            rel_index = abs_index - search_start
            if abs_index < 4:
                offset += rel_index + 1
                continue

            size = int.from_bytes(data[abs_index - 4 : abs_index], "big")
            if size <= 0 or abs_index + size > len(data):
                offset += rel_index + 1
                continue

            partitions.append(
                _WxgfPartition(
                    offset=abs_index,
                    size=size,
                    ratio=size / len(data),
                )
            )
            offset += rel_index + size

        if partitions:
            return partitions

    raise ValueError("no wxgf data partition found")


def _is_wxgf_anime(partitions: list[_WxgfPartition]) -> bool:
    if len(partitions) <= 1:
        return False
    max_ratio = max(partition.ratio for partition in partitions)
    return max_ratio < WXGF_MIN_ANIME_MAX_RATIO


async def _write_bytes(path: Path, data: bytes) -> None:
    await aiofiles.os.makedirs(str(path.parent), exist_ok=True)
    async with aiofiles.open(str(path), "wb") as f:
        await f.write(data)


async def _run_ffmpeg(
    args: list[str], input_data: Optional[bytes] = None
) -> Optional[bytes]:
    ffmpeg_path = _find_ffmpeg_path()
    if not ffmpeg_path:
        return None

    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            *args,
            stdin=asyncio.subprocess.PIPE if input_data is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(input_data)
    except FileNotFoundError:
        logger.warning("ffmpeg 不可用，无法使用 ffmpeg 解码 wxgf。")
        return None
    except Exception as e:
        logger.warning(f"调用 ffmpeg 解码 wxgf 时出错: {e}", exc_info=True)
        return None

    if proc.returncode != 0:
        error_text = stderr.decode("utf-8", errors="ignore").strip()
        logger.warning(f"ffmpeg 解码 wxgf 失败: {error_text}")
        return None
    if not stdout:
        logger.warning("ffmpeg 解码 wxgf 成功返回，但输出为空。")
        return None
    return stdout


def _convert_hevc_to_jpg_with_cv2(
    hevc_data: bytes, output_path: str, temp_dir: str
) -> bool:
    try:
        import cv2
    except ImportError:
        logger.warning("未安装 OpenCV，无法回退解码静态 wxgf。")
        return False

    temp_root = Path(temp_dir)
    temp_root.mkdir(parents=True, exist_ok=True)
    temp_path = temp_root / f".tmp_wxgf_{time.time_ns()}.h265"
    try:
        temp_path.write_bytes(hevc_data)

        capture = cv2.VideoCapture(str(temp_path))
        try:
            ok, frame = capture.read()
        finally:
            capture.release()

        if not ok or frame is None:
            logger.warning("OpenCV 无法读取 wxgf 内的 HEVC 首帧。")
            return False

        final_path = Path(output_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        return bool(
            cv2.imwrite(str(final_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        )
    except Exception as e:
        logger.warning(f"OpenCV 回退解码 wxgf 时出错: {e}", exc_info=True)
        return False
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


async def _convert_hevc_to_jpg_async(
    hevc_data: bytes, output_path_base: str, temp_dir: str
) -> Optional[str]:
    final_output_path = Path(output_path_base).with_suffix(".jpg")

    jpg_data = await _run_ffmpeg(
        [
            "-f",
            "hevc",
            "-i",
            "-",
            "-vframes",
            "1",
            "-c:v",
            "mjpeg",
            "-q:v",
            "4",
            "-f",
            "image2",
            "-",
        ],
        input_data=hevc_data,
    )
    if jpg_data:
        await _write_bytes(final_output_path, jpg_data)
        return str(final_output_path)

    loop = asyncio.get_running_loop()
    ok = await loop.run_in_executor(
        None,
        _convert_hevc_to_jpg_with_cv2,
        hevc_data,
        str(final_output_path),
        temp_dir,
    )
    if ok:
        return str(final_output_path)
    return None


async def _convert_wxgf_anime_to_gif_async(
    anime_frames: list[bytes],
    mask_frames: list[bytes],
    output_path_base: str,
    temp_dir: str,
) -> Optional[str]:
    if len(anime_frames) != len(mask_frames):
        logger.warning(
            f"wxgf 动画帧数量异常: anime={len(anime_frames)}, mask={len(mask_frames)}"
        )
        if anime_frames:
            return await _convert_hevc_to_jpg_async(
                anime_frames[0], output_path_base, temp_dir
            )
        return None

    if not anime_frames:
        logger.warning("wxgf 动画没有可解码帧。")
        return None

    ffmpeg_path = _find_ffmpeg_path()
    if not ffmpeg_path:
        logger.warning("wxgf 动画转 GIF 需要 ffmpeg，回退为首帧 JPG。")
        return await _convert_hevc_to_jpg_async(anime_frames[0], output_path_base, temp_dir)

    temp_root = Path(temp_dir)
    await aiofiles.os.makedirs(str(temp_root), exist_ok=True)
    anime_path = temp_root / f".tmp_wxgf_anime_{time.time_ns()}.h265"
    mask_path = temp_root / f".tmp_wxgf_mask_{time.time_ns()}.h265"

    try:
        await _write_bytes(anime_path, b"".join(anime_frames))
        await _write_bytes(mask_path, b"".join(mask_frames))

        gif_data = await _run_ffmpeg(
            [
                "-f",
                "hevc",
                "-i",
                str(anime_path),
                "-f",
                "hevc",
                "-i",
                str(mask_path),
                "-filter_complex",
                "[0:v][1:v]alphamerge,split[s0][s1];"
                "[s0]palettegen[p];[s1][p]paletteuse",
                "-f",
                "gif",
                "-",
            ]
        )
        if gif_data:
            final_output_path = Path(output_path_base).with_suffix(".gif")
            await _write_bytes(final_output_path, gif_data)
            return str(final_output_path)

        logger.warning("wxgf 动画转 GIF 失败，回退为首帧 JPG。")
        return await _convert_hevc_to_jpg_async(anime_frames[0], output_path_base, temp_dir)
    finally:
        for path in (anime_path, mask_path):
            try:
                if await aiofiles.os.path.exists(str(path)):
                    await aiofiles.os.unlink(str(path))
            except OSError:
                pass


async def decrypt_wechat_image_async(
    image_path: str,
    server_url: Optional[str],
    output_path_base: str,
    temp_dir: Optional[str] = None,
) -> Optional[str]:
    """
    异步解密 wxgf 格式图片，并将结果直接写入最终目标路径。

    Args:
        image_path (str): wxgf 格式的临时文件路径。
        server_url (str): 保留参数，兼容旧的远程解密调用方式。
        output_path_base (str): 最终文件的基础路径（不含扩展名）。
        temp_dir (str): 临时文件目录。

    Returns:
        Optional[str]: 成功后的最终文件完整路径，否则为 None。
    """
    _ = server_url
    temp_dir = temp_dir or str(Path(image_path).parent)
    try:
        if not await aiofiles.os.path.exists(image_path):
            logger.warning(f"wxgf文件不存在，无法进行二次解密: {image_path}")
            return None

        async with aiofiles.open(image_path, "rb") as f:
            file_data = await f.read()

        partitions = _find_wxgf_partitions(file_data)
        if _is_wxgf_anime(partitions):
            mask_frames: list[bytes] = []
            anime_frames: list[bytes] = []
            for index, partition in enumerate(partitions):
                frame = file_data[partition.offset : partition.offset + partition.size]
                if index % 2 == 0:
                    mask_frames.append(frame)
                else:
                    anime_frames.append(frame)

            return await _convert_wxgf_anime_to_gif_async(
                anime_frames, mask_frames, output_path_base, temp_dir
            )

        partition = max(partitions, key=lambda item: item.ratio)
        hevc_data = file_data[partition.offset : partition.offset + partition.size]
        return await _convert_hevc_to_jpg_async(hevc_data, output_path_base, temp_dir)
    except Exception as e:
        logger.error(f"解密wxgf图片时出错: {str(e)}", exc_info=True)
        return None


async def decrypt_wechat_dat_async(
    xor_key: int, aes_key: bytes, dat_path: str, output_path: str, temp_dir: str
) -> Optional[str]:
    """
    异步解密微信的dat文件，正确使用temp_dir并修复跨磁盘复制问题。

    Args:
        xor_key: XOR密钥。
        aes_key: AES密钥。
        dat_path: 原始.dat文件路径。
        output_path: 最终输出文件的基础路径（不含扩展名）。
        temp_dir: 用于存放初次解密结果的临时目录。

    Returns:
        Optional[str]: 成功后的最终文件完整路径，否则为None。
    """
    temp_path_decrypted = None
    try:
        if not await aiofiles.os.path.exists(dat_path):
            logger.warning(f"dat文件不存在: {dat_path}")
            return None

        await aiofiles.os.makedirs(temp_dir, exist_ok=True)

        base_name = Path(dat_path).stem
        temp_path_decrypted = str(
            Path(temp_dir) / f".tmp_{base_name}_{int(time.time()*1000)}.dec"
        )

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, decrypt_dat, dat_path, temp_path_decrypted, xor_key, aes_key
        )

        async with aiofiles.open(temp_path_decrypted, "rb") as f:
            header = await f.read(4)

        if header == WXGF_HEADER:
            return await decrypt_wechat_image_async(
                temp_path_decrypted,
                server_url=None,
                output_path_base=output_path,
                temp_dir=temp_dir,
            )
        else:
            # 只在这里用Path特性
            if header.startswith(b"\xff\xd8\xff"):
                ext = ".jpg"
            elif header.startswith(b"\x89PNG"):
                ext = ".png"
            elif header.startswith(b"GIF8"):
                ext = ".gif"
            else:
                logger.warning(
                    f"未知的文件格式，文件头: {header.hex()}，文件: {dat_path}"
                )
                return None

            final_destination = str(Path(output_path).with_suffix(ext))
            output_dir = Path(final_destination).parent
            await aiofiles.os.makedirs(str(output_dir), exist_ok=True)

            async with (
                aiofiles.open(temp_path_decrypted, "rb") as src,
                aiofiles.open(final_destination, "wb") as dst,
            ):
                await dst.write(await src.read())

            return final_destination

    except Exception as e:
        logger.error(f"解密dat文件时出错: {dat_path} - {str(e)}", exc_info=True)
        return None
    finally:
        if temp_path_decrypted and await aiofiles.os.path.exists(temp_path_decrypted):
            try:
                await aiofiles.os.unlink(temp_path_decrypted)
            except OSError:
                pass


# --- 事件循环管理器 (已修正) ---
class _AsyncLoopManager:
    """一个管理后台asyncio事件循环的单例。"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._loop: Optional[asyncio.AbstractEventLoop] = None
                    cls._instance._thread: Optional[threading.Thread] = None
        return cls._instance

    def get_loop(self) -> asyncio.AbstractEventLoop:
        """获取或创建在后台线程中运行的事件循环。"""
        # [修正] 使用 self 访问类属性 _lock 和实例属性 _loop, _thread
        with self._lock:
            if self._loop is None or not self._loop.is_running():
                self._loop = asyncio.new_event_loop()
                self._thread = threading.Thread(
                    target=self._run_loop, daemon=True, name="AsyncDecryptLoop"
                )
                self._thread.start()
        return self._loop

    def _run_loop(self):
        """线程的目标函数，用于运行事件循环。"""
        logger.info(f"后台事件循环线程 '{threading.current_thread().name}' 已启动。")
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()
        logger.info("后台事件循环已停止。")

    def shutdown(self):
        """同步地停止事件循环和后台线程。"""
        # [修正] 使用 self 访问类属性 _lock 和实例属性
        with self._lock:
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread:
                self._thread.join()
            self._loop = None
            self._thread = None
        logger.info("后台事件循环已完全关闭。")


async_loop_manager = _AsyncLoopManager()


# --- 异步服务核心 (无修改) ---
class DatDecryptService:
    def __init__(self, user_info: UserInfo, config: Config):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.user_info = user_info
        self.watch_dir: Optional[str] = None
        self.temp_dir: Optional[str] = None
        self.xor_key: Optional[int] = None
        self.aes_key: Optional[bytes] = None
        self.config = config

        self._watcher_task_future: Optional[ConcurrentFuture] = None
        self.processing_files: Set[str] = set()
        self.debounce_tasks: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

        self.decryption_futures: Dict[str, List[asyncio.Future]] = {}

        self._loop = async_loop_manager.get_loop()
        self._stop_event = threading.Event()

        self._recent_decrypts = deque(maxlen=20)  # [(filename, path)]
        self._decrypt_callbacks = {}  # filename -> [(callback, timer), ...]
        self._callback_lock = threading.Lock()
        self._init_done = False
        if self.config.get("aes_xor_key", None):
            [aes, xor] = self.config.get("aes_xor_key", None).split(",")
            self.aes_key = aes.encode()
            self.xor_key = int(xor)

    def setup_lazy(self):
        """
        同步初始化服务
        """
        if self._init_done:
            return
        self.watch_dir = str(Path(self.user_info.data_dir) / "msg" / "attach")
        self.temp_dir = str(Path(self.user_info.data_dir) / "temp")

        if not self.aes_key or not self.xor_key:
            self.logger.info("正在查找AES和XOR密钥...")
            future = asyncio.run_coroutine_threadsafe(
                self._find_aes_xor_key(), self._loop
            )
            try:
                future.result(timeout=30)
            except Exception as e:
                self.logger.error(
                    f"在setup期间查找密钥时发生严重错误: {e}", exc_info=True
                )
                return
            if not self.xor_key or not self.aes_key:
                self.logger.error("未能找到解密密钥，服务无法启动。")
                raise RuntimeError("未能找到解密密钥，服务无法启动。")
            self.logger.info("密钥查找和设置完成。")
            self.config.set("aes_xor_key", f"{self.aes_key.decode()},{self.xor_key}")
        self._init_done = True
        self.start_lazy()

    def start_lazy(self):
        """同步启动服务的文件监控循环。"""
        if self._watcher_task_future and not self._watcher_task_future.done():
            self.logger.warning("服务已在运行中。")
            return

        self._stop_event.clear()

        self._watcher_task_future = asyncio.run_coroutine_threadsafe(
            self._watch_loop(), self._loop
        )
        self.logger.info(f"dat文件解密服务已提交到后台运行，监控目录: {self.watch_dir}")

    def stop(self):
        """同步停止服务。"""
        if not self._watcher_task_future or self._watcher_task_future.done():
            self.logger.info("服务未在运行或已停止。")
            return

        self.logger.info("正在同步停止dat解密服务...")
        self._stop_event.set()

        try:
            self._watcher_task_future.result(timeout=5)
            self.logger.info("文件监控循环已优雅退出。")
        except TimeoutError:
            self.logger.warning("等待监控循环退出超时，可能需要强制清理。")
            self._watcher_task_future.cancel()
        except Exception as e:
            self.logger.error(f"等待监控循环退出时发生错误: {e}", exc_info=True)

        cleanup_future = asyncio.run_coroutine_threadsafe(
            self._stop_async_cleanup(), self._loop
        )
        try:
            cleanup_future.result(timeout=5)
            self.logger.info("后台异步任务已成功清理。")
        except Exception as e:
            self.logger.error(f"停止服务时清理任务发生错误: {e}", exc_info=True)

    async def _stop_async_cleanup(self):
        """一个私有的协程，用于执行所有异步的清理工作。"""
        tasks_to_cancel = list(self.debounce_tasks.values())
        for task in tasks_to_cancel:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

        for future_list in self.decryption_futures.values():
            internal_future = future_list[0]
            if not internal_future.done():
                internal_future.cancel()

        self.logger.info("所有后台异步任务已取消。")

    async def await_decryption(
        self, file_path: str, timeout: Optional[float] = 15.0
    ) -> Optional[str]:
        """
        异步等待一个文件的解密结果。
        此方法对调用者透明，自动处理跨事件循环的等待。
        """
        try:
            caller_loop = asyncio.get_running_loop()
        except RuntimeError:
            self.logger.error("await_decryption 必须在一个正在运行的事件循环中被调用。")
            raise

        future_in_caller_loop = caller_loop.create_future()

        self._loop.call_soon_threadsafe(
            self._loop.create_task,
            self._register_and_process(file_path, future_in_caller_loop),
        )

        try:
            return await asyncio.wait_for(future_in_caller_loop, timeout=timeout)
        except asyncio.TimeoutError:
            self.logger.error(f"等待文件 '{file_path}' 解密超时 ({timeout}秒)。")
            return None
        except asyncio.CancelledError:
            self.logger.warning(f"等待文件 '{file_path}' 的任务被调用者取消。")
            return None

    async def _register_and_process(
        self, file_path: str, future_to_resolve: asyncio.Future
    ):
        """
        在后台 Decrypt Loop 中运行，注册 Future 并触发处理。
        """
        caller_loop = future_to_resolve.get_loop()

        def done_callback(result_future: asyncio.Future):
            try:
                result = result_future.result()
                caller_loop.call_soon_threadsafe(future_to_resolve.set_result, result)
            except Exception as e:
                caller_loop.call_soon_threadsafe(future_to_resolve.set_exception, e)

        async with self._lock:
            if file_path not in self.decryption_futures:
                internal_future = self._loop.create_future()
                self.decryption_futures[file_path] = [internal_future]

                self._loop.create_task(self._check_and_process_if_needed(file_path))

            self.decryption_futures[file_path][0].add_done_callback(done_callback)

    async def _check_and_process_if_needed(self, file_path: str):
        """辅助函数，在 Decrypt Loop 中检查并触发处理。"""
        output_path_base = file_path.replace(".dat", "")
        for ext in [".jpg", ".png", ".gif"]:
            path_to_check = output_path_base + ext
            if await aiofiles.os.path.exists(path_to_check):
                await self._resolve_futures(file_path, path_to_check, None)
                return

        async with self._lock:
            if file_path not in self.processing_files:
                self._loop.create_task(self._process_dat_file(file_path))

    async def _resolve_futures(
        self, file_path: str, result: Optional[str], error: Optional[Exception]
    ):
        """在 Decrypt Loop 中安全地解析所有等待该文件的 Futures。"""
        internal_future = None
        async with self._lock:
            if file_path in self.decryption_futures:
                internal_future = self.decryption_futures.pop(file_path)[0]

        if internal_future and not internal_future.done():
            if error:
                internal_future.set_exception(error)
            else:
                internal_future.set_result(result)

    async def _find_aes_xor_key(self):
        """异步查找密钥。"""
        key_file = "aes_xor_key.dat"
        if await aiofiles.os.path.exists(key_file):
            try:
                async with aiofiles.open(key_file, "r") as f:
                    content = await f.read()
                    aes_str, xor_str = content.strip().split(",")
                    self.xor_key = int(xor_str)
                    self.aes_key = aes_str.encode()
                    self.logger.info("从缓存文件成功加载密钥。")
                    return
            except Exception as e:
                self.logger.warning(f"读取密钥缓存文件失败: {e}，将重新查找。")

        dat_files = await self._loop.run_in_executor(
            None, self.find_latest_t_dat_files, self.watch_dir, 10
        )

        if not dat_files:
            self.logger.error(
                f"在目录 '{self.watch_dir}' 中未找到任何_t.dat文件用于密钥分析。"
            )
            return

        for file_path, _ in dat_files:
            try:
                aes, xor = await self._loop.run_in_executor(None, find_key, file_path)
                if aes != -1:
                    self.xor_key = xor
                    self.aes_key = aes
                    async with aiofiles.open(key_file, "w") as f:
                        await f.write(f"{aes.decode()},{xor}")
                    self.logger.info(f"成功找到密钥并已缓存，来源文件: {file_path}")
                    return
            except Exception as e:
                self.logger.warning(f"使用文件 {file_path} 解析密钥时出错: {str(e)}")

    def find_latest_t_dat_files(self, directory: str, n: int = 10) -> list:
        """同步查找最新的n个_t.dat文件。"""
        heap = []
        try:
            for entry in Path(directory).iterdir():
                if entry.is_dir():
                    sub_files = self.find_latest_t_dat_files(str(entry), n)
                    for path, mtime in sub_files:
                        if len(heap) < n:
                            heapq.heappush(heap, (mtime, path))
                        else:
                            heapq.heappushpop(heap, (mtime, path))
                elif entry.is_file() and entry.name.endswith("_t.dat"):
                    try:
                        mtime = entry.stat().st_mtime
                        if len(heap) < n:
                            heapq.heappush(heap, (mtime, str(entry)))
                        else:
                            heapq.heappushpop(heap, (mtime, str(entry)))
                    except FileNotFoundError:
                        continue
        except (FileNotFoundError, PermissionError) as e:
            logger.debug(f"扫描目录时出错: {directory}, 错误: {e}")
            return []

        sorted_files = sorted(heap, key=lambda x: x[0], reverse=True)
        return [(path, mtime) for mtime, path in sorted_files]

    async def _watch_loop(self):
        """使用watchfiles进行异步文件监控。"""
        try:
            async for changes in awatch(self.watch_dir, stop_event=self._stop_event):
                for change_type, path_str in changes:
                    if (
                        change_type in (Change.added, Change.modified)
                    ) and path_str.endswith(".dat"):
                        if "\\Ann\\" in path_str or "/Ann/" in path_str:
                            continue
                        if (
                            path_str in self.debounce_tasks
                            and not self.debounce_tasks[path_str].done()
                        ):
                            self.debounce_tasks[path_str].cancel()
                        self.debounce_tasks[path_str] = self._loop.create_task(
                            self._debounce_and_process(path_str)
                        )
        except Exception as e:
            logger.error(f"文件监控循环意外终止: {e}", exc_info=True)
        finally:
            logger.info("文件监控循环 (_watch_loop) 已结束。")

    async def _debounce_and_process(self, file_path: str):
        """延迟处理并调用解密任务。"""
        try:
            await asyncio.sleep(0.5)
            self._loop.create_task(self._check_and_process_if_needed(file_path))
        except asyncio.CancelledError:
            pass
        finally:
            if file_path in self.debounce_tasks:
                del self.debounce_tasks[file_path]

    async def _process_dat_file(self, file_path: str):
        """异步处理单个dat文件。"""
        async with self._lock:
            if file_path in self.processing_files:
                logger.debug(f"文件正在处理中，跳过: {file_path}")
                return
            self.processing_files.add(file_path)

        result, error = None, None
        try:
            output_path_base = file_path.replace(".dat", "")
            max_wait = 15.0  # 与 await_decryption 默认超时一致
            poll_interval = 0.2
            waited = 0.0
            while not await aiofiles.os.path.exists(file_path):
                await asyncio.sleep(poll_interval)
                waited += poll_interval
                if waited >= max_wait:
                    error = RuntimeError(f"等待文件 {file_path} 超时，文件未出现")
                    break
            if not error:
                result = await decrypt_wechat_dat_async(
                    xor_key=self.xor_key,
                    aes_key=self.aes_key,
                    dat_path=file_path,
                    output_path=output_path_base,
                    temp_dir=self.temp_dir,
                )
                if result:
                    filename = Path(file_path).name
                    self._on_decrypt_success(filename, result)
                else:
                    error = RuntimeError(
                        f"解密函数为文件 {file_path} 返回了 None，但没有抛出异常。"
                    )
        except Exception as e:
            logger.error(
                f"处理dat文件时发生未知异常: {file_path} - {str(e)}", exc_info=True
            )
            error = e
        finally:
            await self._resolve_futures(file_path, result, error)
            async with self._lock:
                if file_path in self.processing_files:
                    self.processing_files.remove(file_path)

    def register_decrypt_callback(
        self, filename: str, callback: Callable[[str, str], None]
    ):
        """
        注册图片解密回调。若已解密则立即回调，否则等待解密后回调。
        Args:
            filename (str): 图片文件名（不含路径）。
            callback (Callable[[str, str], None] or Callable[[str, str], Awaitable[None]]): 回调函数，参数为解密后图片路径。
        """
        with self._callback_lock:
            for fname, path in self._recent_decrypts:
                if fname == filename:
                    self._invoke_callback(callback, fname, path)
                    return

            def timeout_callback():
                with self._callback_lock:
                    if filename in self._decrypt_callbacks:
                        # 移除该回调
                        self._decrypt_callbacks[filename] = [
                            (cb, t)
                            for cb, t in self._decrypt_callbacks[filename]
                            if cb != callback
                        ]
                        # 超时通知
                        try:
                            self._invoke_callback(callback, filename, None)
                        except Exception as e:
                            self.logger.error(f"超时回调出错: {e}")
                        # 如果该文件下已无回调，移除key
                        if not self._decrypt_callbacks[filename]:
                            del self._decrypt_callbacks[filename]

            timer = threading.Timer(60, timeout_callback)
            timer.start()
            self._decrypt_callbacks.setdefault(filename, []).append((callback, timer))

    def _invoke_callback(self, callback, filename, path):
        """
        内部方法：根据回调类型（同步/异步）自动调度执行。
        """
        try:
            if inspect.iscoroutinefunction(callback):
                # 异步回调，调度到事件循环
                try:
                    loop = self._loop
                    asyncio.run_coroutine_threadsafe(callback(filename, path), loop)
                except Exception as e:
                    self.logger.error(f"异步回调调度失败: {e}", exc_info=True)
            else:
                callback(filename, path)
        except Exception as e:
            self.logger.error(f"回调执行出错: {e}", exc_info=True)

    def _on_decrypt_success(self, filename: str, path: str):
        """
        内部方法：解密成功后，加入队列并触发所有等待的回调。
        """
        with self._callback_lock:
            self.logger.info(f"解密成功: {filename}, {path}")
            self._recent_decrypts.append((filename, path))
            if filename in self._decrypt_callbacks:
                for cb, timer in self._decrypt_callbacks[filename]:
                    try:
                        timer.cancel()
                        self._invoke_callback(cb, filename, path)
                    except Exception as e:
                        self.logger.error(f"回调执行出错: {e}", exc_info=True)
                del self._decrypt_callbacks[filename]
