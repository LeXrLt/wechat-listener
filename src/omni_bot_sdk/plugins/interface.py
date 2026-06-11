"""
插件开发统一入口

- 插件开发者只需从本模块导入 Plugin 及常用协议类型
- 便于类型提示、解耦和未来SDK升级
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, Tuple, Union
from pathlib import Path
from omni_bot_sdk.plugins.core.plugin_interface import (
    Plugin,
    PluginExcuteContext,
    PluginExcuteResponse,
)
from omni_bot_sdk.weixin.message_classes import Message, MessageType, DownloadStatus


# =====================
# Bot协议
# =====================
class Bot(Protocol):
    """
    OmniBot 主体协议。
    插件通过注入获得Bot实例，访问所有核心服务、组件和生命周期管理方法。
    支持状态回调机制。
    """

    config: "ConfigService"  # 配置服务
    user_service: "UserService"  # 用户服务
    user_info: "UserInfo"  # 当前用户信息
    db: "DatabaseService"  # 数据库服务
    plugin_manager: "PluginManager"  # 插件管理器
    dat_decrypt_service: "DatDecryptService"  # dat解密服务
    processor_service: "ProcessorService"  # 处理器服务
    # 其他服务...

    STATUS_STARTING: str
    STATUS_RUNNING: str
    STATUS_PAUSED: str
    STATUS_STOPPING: str
    STATUS_STOPPED: str
    STATUS_FAILED: str

    def add_status_callback(self, callback):
        """
        注册状态变更回调。
        Args:
            callback (Callable[[str, Bot], None]): 回调函数，参数为新状态和Bot实例。
        """

    def setup(self):
        """
        初始化Bot。
        """

    def teardown(self):
        """
        销毁Bot，释放资源。
        """

    def run_forever(self):
        """
        持续运行主循环。
        """

    def pause(self):
        """
        暂停Bot运行。
        """

    def resume(self):
        """
        恢复Bot运行。
        """

    def exit(self):
        """
        退出Bot。
        """


# =====================
# Service协议
# =====================
class ConfigService(Protocol):
    """
    配置服务协议。
    提供配置项的读取能力。
    """

    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置项。
        """


class UserService(Protocol):
    """
    用户服务协议。
    提供用户信息的获取、设置、密钥管理等功能。
    插件可通过 bot.user_service 访问。
    """

    user_info: "UserInfo"  # 当前用户信息

    def get_user_info(self) -> "UserInfo":
        """
        获取当前用户信息。
        Returns:
            UserInfo: 当前用户信息对象。
        """

    def set_user_info(self, user_info: "UserInfo"):
        """
        设置当前用户信息。
        Args:
            user_info (UserInfo): 要设置的用户信息对象。
        """

    def update_raw_key(self, key: str, value: str):
        """
        更新原始密钥。
        Args:
            key (str): 密钥名称。
            value (str): 密钥值。
        """

    def get_raw_key(self, key: str) -> Optional[str]:
        """
        获取原始密钥。
        Args:
            key (str): 密钥名称。
        Returns:
            Optional[str]: 密钥值，如不存在则为None。
        """

    def dump_to_file(self):
        """
        将用户信息持久化到文件。
        """


class UserInfo(Protocol):
    """
    微信用户信息协议。
    统一描述微信用户的所有关键信息。
    """

    pid: str  # 进程ID，唯一标识本地运行实例
    version: str  # 微信版本号
    alias: str  # 微信别名
    account: str  # 微信号
    nickname: str  # 微信昵称
    phone: str  # 绑定手机号
    data_dir: str  # 数据目录路径
    dbkey: str  # 数据库密钥
    raw_keys: Dict[str, str]  # 其他原始密钥信息


class DatabaseService(Protocol):
    """
    数据库服务协议。
    定义了与微信数据库交互的接口，包括用户信息、联系人、消息、文件等操作。
    """

    user_info: Optional["UserInfo"]  # 当前用户信息, 可能未初始化

    # --- 初始化与核心方法 ---

    def setup(self) -> None:
        """
        初始化数据库服务。
        加载所有必要的路径、密钥和初始数据。
        """
        ...

    def execute_query(
        self, db_path: Path, query: str, params: tuple = ()
    ) -> List[tuple]:
        """
        在指定的数据库上执行只读SQL查询。

        Args:
            db_path (Path): 目标数据库的 Path 对象。
            query (str): SQL查询语句。
            params (tuple, optional): 查询参数，默认为空元组。
        Returns:
            List[tuple]: 查询结果行列表。
        """
        ...

    # --- 路径与数据库信息获取 ---

    def get_db_path_by_username(self, username: str) -> List[Path]:
        """
        根据用户名获取其所有相关的消息数据库路径列表。
        一个用户的消息可能分布在多个数据库文件中。

        Args:
            username (str): 用户名。
        Returns:
            List[Path]: 数据库路径列表，如不存在则为空列表。
        """
        ...

    def get_all_db_files(self) -> List[Path]:
        """
        递归获取数据目录下所有.db数据库文件的路径。

        Returns:
            List[Path]: 所有数据库文件的路径列表。
        """
        ...

    def get_db_tables(self, db_path: Path) -> List[str]:
        """
        获取指定数据库中的所有表名。

        Args:
            db_path (Path): 目标数据库的 Path 对象。
        Returns:
            List[str]: 表名列表。
        """
        ...

    # --- 消息处理 ---

    def check_new_messages(self) -> List[Tuple[str, tuple]]:
        """
        遍历消息数据库，检查并返回新消息。
        通过比对 sqlite_sequence 表的序列号来检测。

        Returns:
            List[Tuple[str, tuple]]: 新消息列表，每个元素为 (表名, 消息数据元组)。
        """
        ...

    def get_message_by_server_id(
        self, server_id: str, message_db_path: Path, username: str
    ) -> Optional[tuple]:
        """
        通过消息的 server_id 获取特定消息的完整信息。

        Args:
            server_id (str): 消息的服务器ID。
            message_db_path (Path): 该消息所在数据库的路径。
            username (str): 消息所属对话的用户名。
        Returns:
            Optional[tuple]: 包含消息所有字段和数据库路径的元组，未找到则返回 None。
        """
        ...

    def get_messages_by_username(
        self, username: str, count: int = 10, order: str = "desc"
    ) -> List[tuple]:
        """
        获取指定用户的最近消息。
        会自动查找该用户对应的所有消息数据库。

        Args:
            username (str): 用户名 (wxid)。
            count (int, optional): 获取消息数量，默认为10。
            order (str, optional): 排序方式 ('desc' 或 'asc')，默认为 "desc"。
        Returns:
            List[tuple]: 消息原始数据元组的列表。
        """
        ...

    def query_text_messages(
        self,
        username: str,
        limit: int = 10,
        start_timestamp: Optional[int] = None,
        end_timestamp: Optional[int] = None,
        order: str = "desc",
        query: Optional[str] = None,
    ) -> List[tuple]:
        """
        高级查询指定用户的文本消息。

        Args:
            username (str): 用户名 (wxid)。
            limit (int, optional): 返回消息的最大数量。默认为 10。
            start_timestamp (Optional[int], optional): 起始时间戳 (秒或毫秒)。默认为 None。
            end_timestamp (Optional[int], optional): 结束时间戳 (秒或毫秒)。默认为 None。
            order (str, optional): 排序方式 ('desc' 或 'asc')。默认为 "desc"。
            query (Optional[str], optional): 要在消息内容中搜索的文本。默认为 None。

        Returns:
            List[tuple]: 包含 (message_content, sender_username, db_path) 的元组列表。
        """
        ...

    # --- 联系人与群聊 ---

    def get_contact_by_username(self, username: str) -> Optional["Contact"]:
        """
        根据用户名获取联系人。

        Args:
            username (str): 用户名 (wxid)。
        Returns:
            Optional[Contact]: 联系人对象，如果未找到则返回 None。
        """
        ...

    def get_contact_by_sender_id(
        self, sender_id: int, message_db_path: Optional[Path] = None
    ) -> Optional["Contact"]:
        """
        通过消息中的发送者ID获取联系人信息。

        Args:
            sender_id (int): 发送者在消息数据库中的内部ID。
            message_db_path (Optional[Path], optional): 发送者ID所在的消息数据库路径。如果为None，则遍历所有库。
        Returns:
            Optional[Contact]: 对应的联系人对象，未找到则返回 None。
        """
        ...

    def get_contact_by_display_name(self, display_name: str) -> List["Contact"]:
        """
        根据显示名（昵称、备注等）模糊搜索联系人。

        Args:
            display_name (str): 用于模糊搜索的显示名。
        Returns:
            List[Contact]: 匹配的联系人对象列表，可能为空列表。
        """
        ...

    def get_room_by_md5(self, username_md5: str) -> Optional["Contact"]:
        """
        通过群聊username的MD5值反向查询群聊信息。

        Args:
            username_md5 (str): 群聊username的MD5哈希值。
        Returns:
            Optional[Contact]: 对应的群聊联系人对象，未找到则返回 None。
        """
        ...

    def get_room_member_list(self, room_user_name: str) -> List["Contact"]:
        """
        获取指定群聊的成员列表。

        Args:
            room_user_name (str): 群聊的用户名 (wxid)。
        Returns:
            List[Contact]: 群成员的联系人对象列表。
        """
        ...

    def get_room_member_count_by_name(self, room_name: str) -> int:
        """
        通过群聊的显示名称（备注或昵称）获取其成员数量。

        Args:
            room_name (str): 群聊的显示名称。
        Returns:
            int: 群成员的数量。如果找不到对应的群聊，返回 -1。
        """
        ...

    def check_member_in_room(self, room_id: int, member_id: int) -> bool:
        """
        通过内部ID检查一个联系人是否在某个群聊中。

        Args:
            room_id (int): 群聊的内部ID (对应 chat_room 表的 id)。
            member_id (int): 成员的内部ID (对应 contact 表的 id)。
        Returns:
            bool: 如果成员在群聊中，返回 True，否则返回 False。
        """
        ...

    def get_fmessage_list(self) -> List["FMessage"]:
        """
        查询并返回好友请求列表。

        Returns:
            List[FMessage]: 好友请求消息对象列表。
        """
        ...

    # --- 缓存加载 ---

    def load_chat_rooms(self) -> None:
        """
        从数据库加载所有群聊信息并缓存到内存。
        """
        ...

    def load_message_username_map(self) -> None:
        """
        构建用户名到其所在消息数据库路径的映射缓存。
        """
        ...

    # --- 文件与媒体资源获取 ---

    def get_image_by_md5(self, md5: str) -> Optional[tuple]:
        """从硬链接数据库中通过MD5查找图片信息。"""
        ...

    def get_video_by_md5(self, md5: str) -> Optional[tuple]:
        """从硬链接数据库中通过MD5查找视频信息。"""
        ...

    def get_file_by_md5(self, md5: str) -> Optional[tuple]:
        """从硬链接数据库中通过MD5查找文件信息。"""
        ...

    def get_video(self, md5: str, thumb: bool = False) -> Optional[Path]:
        """获取视频文件的相对路径。"""
        ...

    def get_image(
        self,
        xml_content: str,
        message: "Message",
        up_dir: str = "",
        md5: Optional[str] = None,
        thumb: bool = False,
        sender_wxid: str = "",
    ) -> Path:
        """获取图片文件的相对路径。"""
        ...

    def get_image_thumb(self, message: "Message", sender_wxid: str) -> Path:
        """获取图片缩略图的相对路径。"""
        ...

    def get_image_by_time(self, message: "Message", sender_wxid: str) -> Path:
        """通过时间规则推算图片相对路径，并返回最可能存在的那个。"""
        ...

    def get_file(self, md5: str) -> Optional[Path]:
        """获取普通文件的相对路径。"""
        ...

    def get_emoji_url(self, md5: str, thumb: bool = False) -> str:
        """获取表情包的CDN URL。"""
        ...




class PluginManager(Protocol):
    """
    插件管理器协议。
    负责插件的自动发现、加载、优先级排序、消息处理链路分发与热重载。
    插件开发者无需直接操作，通常通过 bot.plugin_manager 访问。
    """

    bot: "Bot"  # 主Bot对象
    plugins: list  # 已加载插件列表

    def setup(self):
        """
        初始化插件管理器。
        """

    def load_plugins(self):
        """
        加载所有插件。
        """

    async def process_message(self, message: "Message", context: dict) -> list:
        """
        异步处理消息，依次调用每个插件的 handle_message 方法。
        支持 should_stop 机制（插件可中断后续处理），并收集所有插件响应。

        Args:
            message (Message): 待处理的消息对象。
            context (dict): 附加上下文信息。
        Returns:
            list: 所有插件的 PluginExcuteResponse 响应列表。
        """

    def reload_all_plugins(self):
        """
        重新加载所有插件（热重载）。
        清空当前插件实例列表，重新发现并加载所有插件。
        """


class MessageService(Protocol):
    """
    消息服务协议。
    提供消息的启动、停止、回调设置等功能。
    """

    def start(self):
        """
        启动消息服务。
        """

    def stop(self):
        """
        停止消息服务。
        """

    def set_callback(self, callback: Any):
        """
        设置消息回调。

        Args:
            callback (Any): 回调函数。
        """

    def pause(self):
        """
        暂停消息服务。
        """

    def resume(self):
        """
        恢复消息服务。
        """

    def get_status(self) -> dict:
        """
        获取服务状态。

        Returns:
            dict: 服务状态信息。
        """




class DatDecryptService(Protocol):
    """
    dat解密服务协议。
    提供dat图片异步解密、回调注册、最近解密图片队列等能力。
    """

    def register_decrypt_callback(self, filename: str, callback: callable):
        """
        注册图片解密回调。用户可传入回调方法和图片文件名，
        如果该文件名已解密则立即触发，否则等待解密后触发。

        Args:
            filename (str): 图片文件名（不含路径）。
            callback (Callable[[str], None]): 回调函数，参数为解密后图片路径。
        """
        ...

    @property
    def recent_decrypts(self) -> list:
        """
        最近解密成功的图片队列，最多保留20条，元素为(filename, path)。

        Returns:
            list: [(str, str)] 最近解密的图片文件名和路径。
        """
        ...



class ProcessorService(Protocol):
    """
    处理器服务协议。
    负责处理消息、插件管理等核心业务逻辑。
    """

    user_info: "UserInfo"  # 当前用户信息
    db: "DatabaseService"  # 数据库服务
    message_factory_service: Any  # 消息工厂服务
    message_queue: Any  # 消息队列
    is_running: bool  # 是否正在运行
    plugin_manager: Any  # 插件管理器

    def setup(self):
        """
        初始化处理器服务。
        """

    def start(self):
        """
        启动处理器服务。
        """

    def stop(self):
        """
        停止处理器服务。
        """

    def get_status(self) -> dict:
        """
        获取处理器服务状态。

        Returns:
            dict: 服务状态信息。
        """

__all__ = [
    # 插件基类
    "Plugin",
    # 常量类
    "MessageType",
    "DownloadStatus",
    # 主要服务协议
    "Bot",
    "ConfigService",
    "UserService",
    "UserInfo",
    "DatabaseService",
    "PluginManager",
    "MessageService",
    "DatDecryptService",
    "ProcessorService",
    # 插件上下文与响应
    "PluginExcuteContext",
    "PluginExcuteResponse",
]
