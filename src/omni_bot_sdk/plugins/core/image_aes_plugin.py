"""
辅助查找图片AES密钥
"""

from typing import TYPE_CHECKING
from omni_bot_sdk.plugins.interface import (
    Plugin,
    PluginExcuteContext,
    MessageType,
)
from pydantic import BaseModel

if TYPE_CHECKING:
    from omni_bot_sdk.bot import Bot


class ImageAesPluginConfig(BaseModel):
    """
    群聊重命名插件配置
    enabled: 是否启用该插件
    priority: 插件优先级，数值越大优先级越高
    """

    enabled: bool = True
    priority: int = 2000


class ImageAesPlugin(Plugin):
    """
    群聊重命名插件实现类
    """

    priority = 2000
    name = "image-aes-plugin"

    def __init__(self, bot: "Bot" = None):
        super().__init__(bot)
        # 动态优先级支持
        self.priority = getattr(self.plugin_config, "priority", self.__class__.priority)

    def get_priority(self) -> int:
        return self.priority

    async def handle_message(self, context: PluginExcuteContext) -> None:
        """
        处理接收到的消息

        参数：
            context (PluginExcuteContext): 消息处理上下文信息

        返回：
            None: 处理结果通过context.add_response()方法返回
        """
        message = context.get_message()

        if message.local_type == MessageType.Image and message.is_self:
            # 初始化，自己发给自己一张图片
            if not self.bot.dat_decrypt_service._init_done:
                self.logger.info("图片解密服务未初始化，启动延迟初始化操作")
                self.bot.dat_decrypt_service.setup_lazy()

    def get_plugin_name(self) -> str:
        return self.name

    def get_plugin_description(self) -> str:
        return "这是一个用于给新创建的群修改备注的插件"

    @classmethod
    def get_plugin_config_schema(cls):
        """
        返回插件配置的pydantic schema类。
        """
        return ImageAesPluginConfig
