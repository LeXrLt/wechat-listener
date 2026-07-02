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

import base64
import json
import logging
import os
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
    push_image: bool = True
    max_image_bytes: int = 5 * 1024 * 1024
    image_decrypt_timeout: float = 60.0


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
        if message.local_type == MessageType.Quote:
            content = message.to_text()
        elif message.local_type == MessageType.Image:
            content = "[图片]"
        else:
            content = message.parsed_content
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

        allowed_types = (MessageType.Text, MessageType.Quote, MessageType.Image)
        if message.local_type not in allowed_types:
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

        # 图片消息：读取图片并以 base64 附加到 payload（方案2）
        if message.local_type == MessageType.Image and getattr(
            self.plugin_config, "push_image", True
        ):
            await self._attach_image_base64(message, payload)

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

    async def _attach_image_base64(self, message, payload: dict) -> None:
        """读取图片文件并以 base64 附加到 payload。"""
        try:
            data, ext = await self._get_image_bytes(message)
        except Exception as e:
            self.logger.warning(f"读取图片失败: {e}")
            data, ext = None, None

        if not data:
            payload["image_available"] = False
            self.logger.debug("图片不可用（未解密或文件缺失），仅推送元数据")
            return

        max_bytes = getattr(self.plugin_config, "max_image_bytes", 5 * 1024 * 1024)
        if len(data) > max_bytes:
            payload["image_available"] = False
            payload["image_reason"] = "oversize"
            self.logger.warning(
                f"图片大小 {len(data)} 超过上限 {max_bytes}，跳过 base64 传输"
            )
            return

        payload["image_available"] = True
        payload["image_format"] = ext
        payload["image_size"] = len(data)
        payload["image_base64"] = base64.b64encode(data).decode("ascii")
        self.logger.debug(f"图片已编码，格式={ext}，大小={len(data)} 字节")

    async def _get_image_bytes(self, message):
        """获取图片字节与扩展名，必要时等待 dat 解密完成。"""
        image_candidates = self._image_candidates(message)
        target = self._first_existing_path(image_candidates)

        if not target:
            dat_service = getattr(self.bot, "dat_decrypt_service", None)
            if dat_service is not None:
                timeout = getattr(self.plugin_config, "image_decrypt_timeout", 60.0)
                dat_candidates = self._dat_candidates(message)
                existing_dat_paths = [
                    path for path in dat_candidates if os.path.exists(path)
                ]
                missing_thumb_dat_paths = [
                    path
                    for path in dat_candidates
                    if path not in existing_dat_paths and self._is_thumb_path(path)
                ]

                for dat_path in existing_dat_paths + missing_thumb_dat_paths:
                    target = self._first_existing_path(image_candidates)
                    if target:
                        break

                    try:
                        decrypted = await dat_service.await_decryption(
                            dat_path,
                            timeout=timeout,
                        )
                    except Exception as e:
                        self.logger.warning(f"等待图片解密失败: {e}")
                        target = self._first_existing_path(image_candidates)
                        if target:
                            break
                        continue

                    if decrypted and os.path.exists(decrypted):
                        target = decrypted
                        break

                    target = self._first_existing_path(
                        self._image_candidates_from_base(os.path.splitext(dat_path)[0])
                    )
                    if target:
                        break

        if not target:
            target = self._first_existing_path(image_candidates)

        if not target:
            return None, None

        ext = os.path.splitext(target)[1].lstrip(".").lower() or "jpg"
        with open(target, "rb") as f:
            return f.read(), ext

    def _image_candidates(self, message) -> list[str]:
        """按优先级生成可直接读取的图片候选路径。"""
        path = getattr(message, "path", "") or ""
        thumb = getattr(message, "thumb_path", "") or ""
        candidates = []

        for candidate in (path, thumb):
            candidates.extend(self._image_candidates_from_path(candidate))

        return self._dedupe_paths(candidates)

    def _dat_candidates(self, message) -> list[str]:
        """按优先级生成需要等待解密的 dat 候选路径。"""
        path = getattr(message, "path", "") or ""
        thumb = getattr(message, "thumb_path", "") or ""
        candidates = []

        for candidate in (path, thumb):
            candidates.extend(self._dat_candidates_from_path(candidate))

        return self._dedupe_paths(candidates)

    def _image_candidates_from_path(self, path: str) -> list[str]:
        path = self._normalize_media_path(path)
        if not path:
            return []

        base, ext = os.path.splitext(path)
        candidates = []
        if ext.lower() in (".jpg", ".jpeg", ".png", ".gif"):
            candidates.append(path)
        if base:
            for related_base in self._related_image_bases(base):
                candidates.extend(self._image_candidates_from_base(related_base))
        return candidates

    def _image_candidates_from_base(self, base: str) -> list[str]:
        if not base:
            return []
        return [f"{base}{ext}" for ext in (".jpg", ".jpeg", ".png", ".gif")]

    def _dat_candidates_from_path(self, path: str) -> list[str]:
        path = self._normalize_media_path(path)
        if not path:
            return []

        base, _ = os.path.splitext(path)
        return [f"{base}.dat" for base in self._related_image_bases(base)]

    def _dat_path_from_image_path(self, path: str) -> str:
        path = self._normalize_media_path(path)
        if not path:
            return ""
        base, _ = os.path.splitext(path)
        return f"{base}.dat" if base else ""

    def _related_image_bases(self, base: str) -> list[str]:
        if not base:
            return []

        root_base = self._root_image_base(base)
        candidates = [base]

        if root_base:
            candidates.extend([root_base, f"{root_base}_h", f"{root_base}_t"])

        return self._dedupe_paths(candidates)

    def _root_image_base(self, base: str) -> str:
        dirname = os.path.dirname(base)
        stem = os.path.basename(base)
        if stem.endswith("_h") or stem.endswith("_t"):
            stem = stem[:-2]
        return os.path.join(dirname, stem) if dirname else stem

    def _normalize_media_path(self, path: str) -> str:
        if not path:
            return ""

        path = os.path.normpath(path)
        drive, _ = os.path.splitdrive(path)
        if os.path.isabs(path) and drive:
            return path

        user_info = getattr(self.bot, "user_info", None)
        data_dir = getattr(user_info, "data_dir", "") if user_info else ""
        if not data_dir:
            return path

        path = path.lstrip("\\/")
        return os.path.normpath(os.path.join(data_dir, path))

    def _first_existing_path(self, paths: list[str]) -> str:
        for path in paths:
            if path and os.path.exists(path):
                return path
        return ""

    def _dedupe_paths(self, paths: list[str]) -> list[str]:
        seen = set()
        result = []
        for path in paths:
            if path and path not in seen:
                seen.add(path)
                result.append(path)
        return result

    def _is_thumb_path(self, path: str) -> bool:
        stem = os.path.splitext(os.path.basename(path))[0]
        return stem.endswith("_t")

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
