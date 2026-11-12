# Panda Ego State 可视化

Panda 与厨房场景共用 `SceneState → build_mjcf → EgoViewRenderer → ViewLoop → Ego API`。保留已知本体外观，编码器更新关节；不使用单独的 Panda 渲染器。

机器人标定用于构建 15 个连杆节点、9 个活动关节的完整自模型。渲染副本使用静态资源中的机器人视觉网格，保留贴图。汤罐、篮子的显示位姿来自 RGB-D Ego State；类别视觉先验只决定外观，不补入未感知物体。尚未测得的物体旋转沿用原估计/先验，因此不表示恢复了真实姿态。

静态资源以 SHA256SUMS 校验，不读取初态文件或 World State API。物体模板的根位姿在编译前归零，再按自身几何居中。模型加载位于观测锁之外，GL 资源仍归公共视图线程所有。

共享转换器新增 `geometry.visual` 的 mesh 表示：`data` 为 Base64 OBJ，`meshTexture` 为可选 Base64 PNG，`pose` 与 `rgba` 沿用几何字段。视觉网格不参与碰撞；碰撞继续使用原有 primitive。带视觉几何的物体走公共固定实体分支，按 SceneState 更新位置，其他包围盒保留原槽位缓存。MuJoCo 编译后的 UV 导出时恢复 V 轴约定，使用原始贴图和与 WSim 一致的线性色彩空间。

验证覆盖公共网格编译、Panda FK、链去重，以及原有视图、场景、相机、Panda Job、聊天和本体装配检查。最终关联回归 95 项通过，耗时 122.80 秒。测试原日志和画面位于 4090-2 的 `steward/var/pi05-live-20261111T072702Z/`。

![World State 与 Ego State 同视角对照](images/panda-ego-view.jpg)

[最终验证日志](panda-ego-view-tests.log)
