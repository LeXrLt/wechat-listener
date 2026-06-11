"""
推送消息到MQTT插件

该插件负责将收到的消息输出到DEBUG日志，用于调试和监控。
主要功能：
- 监听所有收到的消息
- 将消息来源、发送人和内容输出到DEBUG日志
- 忽略图片消息

注意事项：
- 仅用于调试，不处理消息也不拦截消息流
"""

from typing import TYPE_CHECKING
from pydantic import BaseModel

from omni_bot_sdk.plugins.interface import Plugin, PluginExcuteContext, MessageType

if TYPE_CHECKING:
    from omni_bot_sdk.bot import Bot


class PushMsgToMqttPluginConfig(BaseModel):
    """
    推送消息到MQTT插件配置
    enabled: 是否启用该插件
    priority: 插件优先级，数值越大优先级越高
    """

    enabled: bool = True
    priority: int = 500


class PushMsgToMqttPlugin(Plugin):
    """
    推送消息到MQTT插件实现类

    继承自Plugin基类，用于将收到的消息输出到DEBUG日志。

    属性：
        priority (int): 插件优先级
        name (str): 插件名称标识符
    """

    priority = 500
    name = "push-msg-to-mqtt"

    def __init__(self, bot: "Bot" = None):
        super().__init__(bot)
        # 动态优先级支持
        self.priority = getattr(self.plugin_config, "priority", self.__class__.priority)

    def get_priority(self) -> int:
        return self.priority

    async def handle_message(self, context: PluginExcuteContext) -> None:
        """
        处理接收到的消息，将消息信息输出到DEBUG日志

        参数：
            context (PluginExcuteContext): 消息处理上下文信息

        返回：
            None
        """
        message = context.get_message()

        # 忽略图片消息
        if message.local_type == MessageType.Image:
            return
        sender = message.real_sender_name if message.real_sender_name else "未知群"
        # 判断消息来源
        if message.is_chatroom:
            source = "群聊"
        else:
            source = "私聊"

        # 获取消息内容
        content = message.content or ""

        # 输出DEBUG日志
        self.logger.debug(f"【消息来源：{source}】【{sender}】【{content}】")

    def get_plugin_name(self) -> str:
        return self.name

    def get_plugin_description(self) -> str:
        return "将收到的消息输出到DEBUG日志的插件，用于调试和监控"

    @classmethod
    def get_plugin_config_schema(cls):
        """
        返回插件配置的pydantic schema类。
        """
        return PushMsgToMqttPluginConfig
