# Three.js 最小样例（无需 npm）

这个示例是一个单文件网页：`index.html`，用于演示你要的模式：

- WebSocket 只负责接收状态快照
- 前端渲染循环固定运行
- 每辆车维护 snapshot buffer，并做 interpolation / short extrapolation

## 1) 启动方式

在项目根目录运行：

```powershell
python -m http.server 8088
```

然后打开：

`http://localhost:8088/ui/threejs-demo/index.html`

## 2) 如何看效果

- 先点 `Mock On/Off`：用模拟数据看平滑移动效果
- 再点 `Connect`：连接你的 Ditto WS

默认 WS URL：

`ws://192.168.17.128:8080/ws/2`

## 3) 当前示例约束

- 未加入 Basic Auth（若你的 Ditto 需要认证，建议后续加一个后端代理）
- 只演示 car1/car2/car3，坐标范围固定为 `-25 ~ 25`
- 使用 CDN 引入 three.js（方便你先快速试）
