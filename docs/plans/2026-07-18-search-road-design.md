# 镜牢纯 ONNX 寻路流程

## 模块职责

- `Mirror.search_road`：寻路总入口，统一把异常转换为 `False`。
- `MirrorMap`：保存当前楼层完整节点图和包含 bus 的最优节点路线。
- `search_road_from_road_map`：组织一次完整的 ONNX 寻路。
- `find_bus` / `move_bus` / `onnx`：定位并归一化地图，识别节点。
- `generate_map`：网格化节点、识别连线，普通镜牢补齐商店和 BOSS。
- `find_min_weight_route` / `route_to_directions`：计算最低权重路线并转换为 U/M/D。

## 调用流程

```mermaid
flowchart TD
    A[Mirror.run 检测到路线图] --> B[Mirror.search_road]
    B --> C[MirrorMap.get_next_node_direction]
    C --> D{floor_route 是否至少有两个节点}
    D -- 是 --> E[根据相邻节点计算 U/M/D]
    D -- 是 --> F[search_road_from_road_map]

    F --> G{bus 是否已位于可进入节点}
    G -- 是 --> H[点击进入]
    H --> I[返回 True]
    G -- 否 --> J[find_bus]
    J --> K[move_bus]
    K --> L[截取当前画面]
    L --> M[identify_nodes 执行 ONNX]
    M --> N[generate_map]
    N --> N1[节点坐标网格化]
    N1 --> N2[识别相邻节点连线]
    N2 --> N3{普通镜牢且需要补齐}
    N3 -- 是 --> N4[补齐商店和 BOSS]
    N3 -- 否 --> O[find_min_weight_route 执行 Dijkstra]
    N4 --> O
    O --> P[route_to_directions 转换 U/M/D]
    P --> Q{路线是否有效}
    Q -- 否 --> Q1[find_first_direction 直接识别]
    Q1 --> Q2{是否识别到 U/M/D}
    Q2 -- 否 --> Z[返回 False]
    Q2 -- 是 --> R
    Q -- 是 --> E

    E --> R[MirrorMap.enter_next_node]
    R --> S[键盘或鼠标选择节点]
    S --> T[等待并点击进入按钮]
    T --> U[等待人格或 EGO 页面]
    U --> V[从 floor_route 移除当前节点]
    V --> I

    J -. 失败 .-> Z
    K -. 失败 .-> Z
    M -. 失败 .-> Z
    N -. 失败 .-> Z
    O -. 失败 .-> Z
    R -. 失败 .-> Z
```

## 失败策略

当 `floor_route` 不足两个节点时重新执行 ONNX。ONNX 识别、bus 定位、建图或
最低权重路线计算未生成有效路线时，不保存路线缓存，而是使用当前 bus 坐标调用
`find_first_direction` 识别首个可用方向。只有 ONNX 和首方向识别均失败时才返回
`False`。不再使用 watchdog，也不回退到最近节点法或滚轮寻路。

缓存路线仅在成功进入节点后移除当前节点。直接识别的方向不写入 `floor_route`，
因此执行完毕后，下一次调用仍会优先重新执行 ONNX。

## 独立方向探测

`find_first_direction(bus_position)` 保持为独立函数，由 `MirrorMap` 在 ONNX
没有得到有效路线时调用。它根据 bus 坐标计算 bus 到上、中、下三个相邻节点的
连线中点，并依次匹配 `up_arr.png`、
`mid_arr.png`、`down_arr.png`。函数返回第一个命中的 `U`、`M` 或 `D`；三个
方向均未命中时返回 `False`。
