# My-TODOs

一个轻量级个人待办事项桌面软件，基于 PyQt 开发，适合放在桌面角落长期使用。

## 功能特性

- 待办事项新增、编辑、删除、完成状态切换
- 日计划模式，任务可绑定到具体日期
- 独立日历窗口，支持居中展开，不影响主窗口在桌面右上角停靠
- 月历视图展示每日计划，任务标题直接显示在日期格中
- 支持从飞书导入计划
- 本地配置和数据保存，无需后端服务

## 使用方式

### 直接运行

下载 Release 中的 `MyTODOsPyQt-20260612.zip`，解压后运行：

```text
MyTODOsPyQt\MyTODOsPyQt.exe
```

注意：不要只复制单个 exe 文件运行，PyInstaller 当前使用 one-dir 打包方式，程序需要同目录依赖文件。

### 从源码运行

```bash
python start.py
```

如缺少依赖，请根据报错安装对应 Python 包。

## 飞书导入说明

软件支持手动从飞书导入计划，当前支持：

- 飞书多维表格
- 飞书独立汇报 API

使用前需要在设置页填写飞书应用配置：

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_BITABLE_APP_TOKEN`
- `FEISHU_BITABLE_TABLE_ID`
- 标题、日期、日汇报字段名

导入规则：

- 只导入今天到未来 30 天内的计划
- 跳过历史计划
- 同日期同标题自动去重
- 导入标题会自动添加 `[飞书]` 前缀

## 数据存储

当前版本为本地单机应用，数据保存在本地配置文件中：

- `todos.ini`：待办数据
- `options.ini`：软件配置

请不要把包含个人凭证的 `options.ini` 上传到公开仓库。

## 打包

项目使用 PyInstaller 打包。当前推荐使用项目内的 spec 文件：

```bash
python -m PyInstaller --noconfirm MyTODOsPyQt.spec
```

打包完成后，请发布整个输出目录或压缩包，而不是只发布单个 exe。

## License

本项目基于 GPL v3.0 license。
