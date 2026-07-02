# MemGuardOS Interface Design System

## Theme

MemGuardOS 的桌面界面属于 openKylin/UKUI 系统界面，不建立独立视觉主题。Qt 原生组件从 `QApplication`、`QPalette`、系统字体、`QStyle` 和 UKUI/Kylin Widgets 继承外观。禁止在插件源码中硬编码背景、正文、强调、警示颜色、字体名称、圆角和阴影。

Web 控制台用于本地补充管理，应在信息架构和文案上与 UKUI 控制中心一致，但不反向影响原生桌面样式。

## Color

- 背景、表面、正文、次要文字、边框、选中态和禁用态全部使用系统 palette role。
- 成功、警告和错误同时使用图标、短标签与文字说明，颜色仅作冗余编码。
- 主操作使用当前 UKUI 主题的标准强调色；同一页面只保留一个主操作。
- 危险色只用于确认导入敏感例外、遗忘和特权操作的最终步骤。

## Typography

- 全部 UI 文字使用系统字体和系统字号，不指定微软雅黑、宋体或第三方字体。
- 页面标题使用系统标题语义，分组标题使用标准 `QGroupBox` 或 UKUI 分组组件。
- 技术路径、命令和 JSON 只在详情区使用系统等宽字体。
- 中文句子自然换行，按钮优先使用“动词 + 对象”，例如【重新检查状态】【导入所选文件】【取消操作】。

## Layout

- UKUI 控制中心采用纵向设置页结构：页面标题、状态摘要、分组列表、操作区和可折叠技术详情。
- Peony 扫描采用标准模态对话框：说明、文件表格、风险摘要、标准按钮区。
- 默认窗口在 1024×768 可完整操作；150% 缩放下允许滚动，不允许文字重叠或按钮裁切。
- 操作控件保持稳定尺寸，加载文本不能推动布局变化。

## Components

### Status Row

由主题图标、名称、短状态、说明和可选操作组成。状态码与文字固定对应：正常、需要注意、不可用、检查中。不得只显示红黄绿圆点。

### File Review Table

列为选择、文件名、位置、大小、风险、建议。敏感或不可导入项的选择框禁用；完整路径通过 tooltip 和可访问说明提供。长文件名中间省略，不能改变列布局。

### Confirmation

普通安全导入使用【导入所选文件】；遗忘和特权操作使用内容明确的确认对话框。取消按钮始终可见且为默认安全路径。

### Technical Details

默认折叠，显示清理过 ANSI 控制符的命令输出或格式化 JSON。提供复制操作，不影响普通状态摘要。

### Tray

使用系统主题图标和原生菜单，不自绘浮层。菜单包含【打开记忆安全中心】【打开本地控制台】【重新检查状态】【退出托盘】。退出托盘不停止服务。

## Interaction States

每个可交互控件必须覆盖默认、悬停、键盘焦点、按下、禁用和执行中状态。异步任务具备超时与取消；完成后焦点移动到结果摘要；错误信息包含现象、原因和下一步。通知只在状态变化或用户任务完成时出现，重复状态不反复弹出。

## Icons

使用 `QIcon::fromTheme` 和 freedesktop/UKUI 语义图标名称。推荐：`security-high`、`dialog-information`、`dialog-warning`、`dialog-error`、`view-refresh`、`system-run`、`document-save`、`preferences-system`。主题缺失时回退到标准 Qt style icon，不混用自绘彩色图标。

## Accessibility

所有图标按钮提供 tooltip 和 accessibleName。表格支持键盘选择，状态变化通过 accessibleDescription 表达。Tab 顺序遵循视觉顺序；Escape 关闭非破坏性对话框；Enter 只触发当前明确的主操作。所有布局在默认、深色主题以及 125%、150%、200% 缩放下检查。
