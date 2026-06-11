"""
推送消息到MQTT插件

该插件负责将收到的消息推送到MQTT，同时输出到DEBUG日志。
主要功能：
- 监听所有收到的消息
- 将消息来源、发送人和内容推送到MQTT
- 忽略非文本消息

注意事项：
- 需要配置MQTT连接信息
"""

import json
import logging
from typing import TYPE_CHECKING
from pydantic import BaseModel
from paho.mqtt.client import Client as MqttClient

from omni_bot_sdk.plugins.interface import Plugin, PluginExcuteContext, MessageType

if TYPE_CHECKING:
    from omni_bot_sdk.bot import Bot


class PushMsgToMqttPluginConfig(BaseModel):
    """
    推送消息到MQTT插件配置
    enabled: 是否启用该插件
    priority: 插件优先级，数值越大优先级越高
    push_to_mqtt: 是否推送到MQTT（如果为false则只记录日志）
    mqtt_topic: MQTT主题，默认为 wechat/messages
    """

    enabled: bool = True
    priority: int = 500
    push_to_mqtt: bool = True
    mqtt_topic: str = "wechat/messages"


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
        self.mqtt_client: MqttClient | None = None
        self.mqtt_connected = False
        self._setup_mqtt()

    def get_priority(self) -> int:
        return self.priority

    def _setup_mqtt(self):
        """初始化MQTT连接"""
        if not getattr(self.plugin_config, "push_to_mqtt", True):
            return

        mqtt_config = self.config.get("mqtt", {})
        host = mqtt_config.get("host", "127.0.0.1")
        port = mqtt_config.get("port", 1883)
        username = mqtt_config.get("username")
        password = mqtt_config.get("password")
        client_id = mqtt_config.get("client_id", "weixin_omni")

        try:
            self.mqtt_client = MqttClient(client_id=f"{client_id}_push")
            if username and password:
                self.mqtt_client.username_pw_set(username, password)
            self.mqtt_client.on_connect = self._on_connect
            self.mqtt_client.on_disconnect = self._on_disconnect
            self.mqtt_client.connect(host, port, keepalive=60)
            self.mqtt_client.loop_start()
            self.logger.info(f"MQTT客户端已启动，连接到 {host}:{port}")
        except Exception as e:
            self.logger.error(f"MQTT连接失败: {e}")
            self.mqtt_client = None

    def _on_connect(self, client, userdata, flags, rc):
        """MQTT连接回调"""
        if rc == 0:
            self.mqtt_connected = True
            self.logger.info("MQTT连接成功")
        else:
            self.mqtt_connected = False
            self.logger.error(f"MQTT连接失败，返回码: {rc}")

    def _on_disconnect(self, client, userdata, rc):
        """MQTT断开连接回调"""
        self.mqtt_connected = False
        if rc != 0:
            self.logger.warning(f"MQTT意外断开，返回码: {rc}")

    def _format_message(self, message):
        """格式化消息为字典"""
        sender = self.user.nickname if message.is_self else message.contact.display_name
        content = (
            message.to_text()
            if message.local_type == MessageType.Quote
            else message.parsed_content
        )
        return {
            "speaker_name": sender,
            "content": str(content or ""),
            "is_bot": message.is_self,
            "msg_type": message.local_type.name if hasattr(message.local_type, 'name') else str(message.local_type),
            "timestamp": message.create_time,
            "time_str": message.str_time
        }

    def _push_to_mqtt(self, payload: dict):
        """推送消息到MQTT"""
        if not self.mqtt_client or not self.mqtt_connected:
            return False

        try:
            topic = getattr(self.plugin_config, "mqtt_topic", "wechat/messages")
            self.mqtt_client.publish(topic, json.dumps(payload, ensure_ascii=False), qos=1)
            return True
        except Exception as e:
            self.logger.error(f"MQTT推送失败: {e}")
            return False

    async def handle_message(self, context: PluginExcuteContext) -> None:
        """
        处理接收到的消息，将消息信息输出到DEBUG日志

        参数：
            context (PluginExcuteContext): 消息处理上下文信息

        返回：
            None
        """
        message = context.get_message()

        if (
            message.local_type != MessageType.Text
            and message.local_type != MessageType.Quote
        ):
            return
        # 判断消息来源
        if message.is_chatroom:
            room_name = message.room.display_name
        else:
            room_name = ""

        # 获取消息内容
        formatted_message = self._format_message(message)

        # 构建完整消息payload
        payload = {
            "room_name": room_name,
            **formatted_message,
        }

        # 输出DEBUG日志
        self.logger.debug(
            f"【{room_name}】【{formatted_message['speaker_name']}】【{formatted_message['content']}】"
        )

        # 推送到MQTT
        if getattr(self.plugin_config, "push_to_mqtt", True):
            if self._push_to_mqtt(payload):
                self.logger.debug(f"消息已推送到MQTT: {room_name}")
            else:
                self.logger.warning(f"消息推送到MQTT失败: {room_name}")

    def get_plugin_name(self) -> str:
        return self.name

    def get_plugin_description(self) -> str:
        return "将收到的消息推送到MQTT并输出到DEBUG日志的插件"

    def close(self):
        """清理资源，断开MQTT连接"""
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            self.logger.info("MQTT客户端已断开")

    @classmethod
    def get_plugin_config_schema(cls):
        """
        返回插件配置的pydantic schema类。
        """
        return PushMsgToMqttPluginConfig
