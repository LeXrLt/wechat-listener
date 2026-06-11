# 测试用 MQTT 服务器 (Mosquitto)

参照 `config.example.yaml` 中的 `mqtt` 配置，提供一个本地测试用的 MQTT broker。

## 启动

```bash
docker compose -f docker/mqtt/docker-compose.yml up -d
```

## 对应的 config.yaml 配置

```yaml
mqtt:
  client_id: weixin_omni
  host: 127.0.0.1
  port: 1883
  username: weixin
  password: 'YOUR_MQTT_PASSWORD'
```

> 用户名/密码在 `docker-compose.yml` 的 `MQTT_USERNAME` / `MQTT_PASSWORD` 环境变量中设置，
> 修改后需与 `config.yaml` 保持一致，并重新 `up -d` 使其生效。

## 验证

```bash
# 订阅
docker exec -it omni-bot-mqtt mosquitto_sub -h localhost -u weixin -P YOUR_MQTT_PASSWORD -t test

# 另开一个终端发布
docker exec -it omni-bot-mqtt mosquitto_pub -h localhost -u weixin -P YOUR_MQTT_PASSWORD -t test -m hello
```

## 停止

```bash
docker compose -f docker/mqtt/docker-compose.yml down
```
