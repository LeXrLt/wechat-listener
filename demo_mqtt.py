"""
demo_mqtt.py

一个简单的 MQTT 监听 Demo，用于查看本系统通过 MQTT 传输的消息。

本系统（MCP / MQTTService）会向以下主题发布/订阅消息：
  - msg/{account}/rpa_action        发送文本/文件/拍一拍等动作
  - msg/{account}/other_rpa_action  发朋友圈、群管理等其它 RPA 动作

本脚本默认订阅 `msg/#`，可以看到上述所有主题的消息。

用法:
  python demo_mqtt.py

MQTT 连接参数会按以下优先级读取:
  1. config.yaml 中的 mqtt 段
  2. config.example.yaml 中的 mqtt 段
  3. 内置默认值 (与 docker/mqtt 测试服务器一致)
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import paho.mqtt.client as mqtt

DEFAULT_MQTT = {
    "host": "127.0.0.1",
    "port": 1883,
    "username": "weixin",
    "password": "YOUR_MQTT_PASSWORD",
    "client_id": "demo_mqtt_listener",
}

# 订阅的主题，# 为通配符，表示所有层级
SUBSCRIBE_TOPIC = "wechat/messages"


def load_mqtt_config() -> dict:
    """从 config.yaml / config.example.yaml 读取 mqtt 配置，找不到则用默认值。"""
    root = Path(__file__).resolve().parent
    for name in ("config.yaml", "config.example.yaml"):
        path = root / name
        if not path.exists():
            continue
        try:
            from ruamel.yaml import YAML

            yaml = YAML(typ="safe")
            with path.open("r", encoding="utf-8") as f:
                data = yaml.load(f) or {}
            mqtt_cfg = data.get("mqtt") or {}
            if mqtt_cfg:
                print(f"[配置] 已从 {name} 读取 mqtt 配置")
                return {**DEFAULT_MQTT, **mqtt_cfg}
        except Exception as e:
            print(f"[配置] 读取 {name} 失败: {e}")
    print("[配置] 未找到配置文件，使用内置默认值")
    return dict(DEFAULT_MQTT)


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"[连接] 已连接到 MQTT 服务器，订阅主题: {SUBSCRIBE_TOPIC}")
        client.subscribe(SUBSCRIBE_TOPIC)
    else:
        print(f"[连接] 连接失败，错误码: {rc}")
        if rc == 7:
            print("[连接] 认证失败，请检查用户名/密码")


def on_disconnect(client, userdata, rc):
    print(f"[连接] 已断开连接 (rc={rc})")


def on_message(client, userdata, msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("\n" + "=" * 60)
    print(f"[{ts}] 主题: {msg.topic}")
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    except Exception:
        # 不是 JSON 就原样打印
        print(msg.payload.decode("utf-8", errors="replace"))
    print("=" * 60)


def main():
    cfg = load_mqtt_config()
    host = cfg.get("host", DEFAULT_MQTT["host"])
    port = int(cfg.get("port", DEFAULT_MQTT["port"]))
    username = cfg.get("username")
    password = cfg.get("password")
    client_id = f"{cfg.get('client_id', 'demo_mqtt_listener')}_{int(time.time())}"

    client = mqtt.Client(client_id=client_id)
    if username and password:
        client.username_pw_set(username, password)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    print(f"[启动] 正在连接 {host}:{port} (client_id={client_id}) ...")
    try:
        client.connect(host, port, keepalive=60)
    except Exception as e:
        print(f"[启动] 连接失败: {e}")
        sys.exit(1)

    print("[启动] 开始监听消息，按 Ctrl+C 退出\n")
    try:
        client.loop_forever()
    except KeyboardInterrupt:
        print("\n[退出] 收到中断信号，正在断开连接...")
        client.disconnect()


if __name__ == "__main__":
    main()
