# MeshLab 2025.07 中文操作教程

更新时间：2026-11-16 18:25（北京时间 UTC+8）

适用版本：MeshLab 2025.07。示例模型为 `11.16.17.ply`；英文菜单与参数按本机插件说明核对，建议数值仍需在模型副本上验证。

## 1. 先认识当前界面

- 中间：模型视窗。左键拖动旋转，滚轮缩放；通常按住鼠标中键拖动平移，无中键时可尝试 Shift＋左键拖动。模型上双击可将点击位置设为观察中心。当前未逐项实测鼠标操作。
- 右上 `Project_1 → 11.16.17`：模型图层列表，点击图层名使其成为当前操作对象；眼睛图标切换可见性。
- 示例底部：`Vertices: 28,007`（顶点）、`Faces: 29,821`（三角面）；`Selection: v:0 f:0` 表示当时没有选中顶点或面。截图中的红色不能据此认定为选择高亮。
- 右侧 `Boundary Edges`、`Boundary Faces`、`No-Manif Verts`、`No-Manif Edges`、`Texture Border`：分别显示边界边、边界面、非流形顶点、非流形边、纹理边界。这些 On/Off 是检查显示开关，不会清理或修复几何。初学时可都设为 Off。
- 顶部 `Filters`：实际处理模型的滤镜；`Render`：显示效果。右上角放大镜可搜索操作名称。

## 2. 第一步：先另存工作副本

1. 在右上点击 `11.16.17` 图层。
2. 点击 `File → Export Mesh As…`。
3. 选桌面，把文件名改为 `11.16.17_work.ply`，保留原始文件。
4. 点 Save；如果出现导出选项，保留/勾选模型已有的顶点颜色 `Color` 和法线 `Normal`，再确认。没有纹理坐标的模型无需强行添加 Texture。
5. 以后每个满意阶段再另存，例如 `11.16.17_clean.ply`、`11.16.17_smooth.ply`。

`Save Project` 保存工程组织方式，不能代替处理后的网格导出。MeshLab 各种几何处理不要假定都能 Ctrl+Z 撤回；不满意时删除处理层，重新导入最近的备份。导出副本不等于新建了第二个图层。

## 3. 第二步：先清理小碎片

先处理明显孤立的碎片，不急着修改整张表面。

1. 打开 `Filters → Cleaning and Repairing → Remove Isolated pieces (wrt Face Num.)`。
2. 若菜单不好找，点击右上放大镜，搜索 `Remove Isolated`，选择带 `Face Num.` 的项目。
3. 控制连通块最小面数的参数先试 `10`；本机插件参数标识为 `MinComponentSize`，界面显示名可能不同。
4. 若有删除未引用顶点的选项，可勾选；点一次 Apply，然后 Close，旋转模型查看。
5. 它按整块包含的三角面数量删除小连通块，不是按空间位置删除背景。真实细小结构也可能是小连通块，因此必须与原模型对照。明显缺失正常物体时重新加载备份，降低阈值或跳过此步。

与主体相连的长条，如果与主体连接，不会被这种滤镜删除；删除前需要核对实际物体。

### 按位置手工删杂物（可选）

1. 先旋转视角，让确定要删的碎片与主体在屏幕上分开。
2. 在顶部工具栏悬停查看提示，选择 `Select Faces in a rectangular region`（矩形选择面）。
3. 在碎片上拖一个小框。该工具说明：Ctrl 追加选择、Shift 减去选择、Alt 仅选可见部分。普通框选可能穿透选到背面，尽量使用 Alt 并旋转核对。
4. 查看底部 `Selection … f:…` 是否大于 0，旋转检查有没有误选主体。进入选择工具后，若不能旋转，先退出选择工具再检查。
5. 确认后通过右上放大镜查找 `Delete Selected Faces and Vertices`，执行删除。
6. 不确定的部分先保留。选错但尚未删除时，搜索 `Select None` 清除选择后重选。

不要仅凭红色判断是否选中，要结合底部选择数量和实际选择操作。

## 4. 第三步：温和平滑，减轻表面毛刺

1. 打开 `Filters → Smoothing, Fairing and Deformation → Laplacian Smooth`。也可用右上放大镜搜索 `Laplacian`，选择普通的 `Laplacian Smooth`，不要选名称带 `Vertex Color` 的颜色平滑。
2. 将 `Smoothing steps` / 迭代次数设为 `1`（具体标签可能显示为 Smooth Iterations）。
3. 若要处理整个模型，取消 `Selected` / `Affect only selection`；否则没有选择时可能看不到变化。
4. 其余选项先保留默认值，点一次 Apply，然后 Close。
5. 旋转查看棱角、小物体和边缘。如果毛刺减轻且细节保留，可以另存；不满意就重新导入清理后的备份。

每点一次 Apply 都可能在上次结果上继续处理。当前模型薄片、开口较多，不建议一开始就做 10～20 次迭代。拉普拉斯平滑会移动顶点，并可能使模型收缩；它不能修好轨迹错位或恢复没拍到的结构。

## 5. 第四步：减面（当前可以跳过）

截图只有 29,821 个面，若旋转流畅，无需为了优化而减面。

1. `Filters → Remeshing, Simplification and Reconstruction → Simplification: Quadric Edge Collapse Decimation`；搜索 `Quadric` 也可。当前顶点颜色模型选择普通版本，不选择名称带 `(with texture)` 的版本。
2. `Target number of faces` 先试 `20000`。
3. `Percentage reduction (0..1)` 设为 `0`，表示使用目标面数；非零值在该滤镜中代表期望保留的比例，例如 `0.5` 为保留约一半，并非“减少0.5个面”。
4. 勾选 `Preserve Boundary of the mesh`、`Preserve Normal`，若有 `Preserve Topology` 也可勾选；取消 `Simplify only selected faces` 以处理全模型。
5. 点一次 Apply，比较轮廓、细节和颜色；约束可能导致实际面数不能达到目标。效果不好则恢复备份。

减面主要用于降低文件和显示负担，不用于提高采集精度。

## 6. 补洞暂时放到最后

入口：`Filters → Remeshing, Simplification and Reconstruction → Close Holes`，或搜索 `Close Holes`。

- 最大洞大小按边界边的数量计算，不是厘米、毫米或面积。可在副本上从 `10` 开始试小洞。
- 如果勾选仅处理选择区域，先选择目标洞边缘附近的面；未选择时不会达到预期。
- 示例中的大缺口可能是未扫描区域，不宜一次全部封闭。补洞会创建新三角面，可能把门口、物体间隔等真实开口封住。
- 表面重建（例如 Poisson）应另外试验，不能把它当作无条件修复按钮。

## 推荐练习顺序

先另存 `work.ply` → 尝试小连通块清理（10面起步）→ 普通 Laplacian Smooth 1次 → 另存 `smooth.ply`。减面和补洞先跳过。以上是建议步骤，尚未对示例模型实施这些滤镜。

显示更顺眼与几何更准确需要分别判断；物理测量依赖正确尺度，实际精度应与已知尺寸比较。
