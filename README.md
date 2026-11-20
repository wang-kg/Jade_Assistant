# Jade Assistant

**Your personal assistant for Jade's Black Market.**

一款用于《崩坏：星穹铁道》「货币战争」模式中「黑市主理人」羁绊商店的自动化助手。程序通过识别商品名称「10小队生命值」来查找并购买目标商品。

## 使用须知

- 游戏客户区比例需为 **16:9**，分辨率至少 **1280×720**。
- 程序按固定阶段运行：检测/启动游戏；仅新启动时处理登录页；按 ESC → F4 并确认指南页；进入货币战争；最后开始刷新。每阶段完成后不会回头执行前一阶段。
- 请以管理员身份运行终端并启动程序，以便在游戏窗口内接收快捷键和执行操作。
- 程序通过中文 OCR 识别商品名称，不再依赖商品图标模板图片。
- 文字识别依赖当前尚未提供 Python 3.15 Windows 安装包的 ONNX Runtime，请使用 **Python 3.14** 运行本项目；这不会替换或影响你已安装的 Python 3.15。

## 构建环境

在项目文件夹中打开 PowerShell。若尚未安装 Python 3.14，先执行：

```powershell
py install 3.14
```

为项目创建独立虚拟环境并安装依赖：

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 运行

每次运行时使用项目虚拟环境中的 Python：

```powershell
.\.venv\Scripts\python.exe game_shop_helper.py
```

## 操作

程序先检查游戏窗口。游戏未运行时会启动游戏，并在登录阶段依次处理「开始游戏」和「点击进入」；游戏已运行时会跳过登录操作。接着按 ESC、再按 F4 打开「星际和平指南」，并识别左上角标题确认页面已打开。未识别到时会重复 ESC → F4，最多尝试 10 次；仍未打开就保存排查截图并停止。

确认指南页后，程序点击顶部第三个图标「旷宇纷争」，再点击右下角「前往参与」。进入货币战争后点击「开始货币战争」，识别到「继续进度」才会点击存档继续按钮，不会点击「结束并结算」；继续存档后会等待并点击「点击空白处继续」，再打开黑市主理人商店并开始刷新。进入货币战争导航后，不会重新运行登录或指南页检测。

进入商店后，程序会识别三格商品名称，找到「10小队生命值」后尝试购买。购买未成功时，会点击右侧招牌商品 **9 次**后再次尝试。

按 **F8** 暂停或继续，按 **F9** 退出。鼠标移至屏幕左上角也可触发紧急停止。

程序会在游戏窗口左下角显示当前状态。游戏路径会从 HoYoPlay 常见安装目录自动查找；如果安装在其他位置，可在项目目录创建 `settings.json`：

```json
{
  "game_executable": "D:\\Games\\Star Rail Games\\StarRail.exe"
}
```

## GitHub 描述

An automation tool for Honkai: Star Rail's Black Market Manager bond, helping players automatically refresh shops and purchase target items.
