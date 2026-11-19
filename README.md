# Jade Assistant

**Your personal assistant for Jade's Black Market.**

一款用于《崩坏：星穹铁道》「货币战争」模式中「黑市主理人」羁绊商店的自动化助手。程序通过识别商品名称「10小队生命值」来查找并购买目标商品。

## 使用须知

- 游戏客户区比例需为 **16:9**，分辨率至少 **1280×720**。
- 使用前请进入游戏内「货币战争」的黑市商店界面。
- 请以管理员身份运行终端并启动程序，以便在游戏窗口内接收快捷键和执行操作。
<<<<<<< Updated upstream
=======
- 程序通过中文 OCR 识别商品名称，不再依赖商品图标模板图片。
- 文字识别依赖当前尚未提供 Python 3.15 Windows 安装包的 ONNX Runtime，请使用 **Python 3.14** 运行本项目；这不会替换或影响你已安装的 Python 3.15。
>>>>>>> Stashed changes

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

1. 确认游戏停留在黑市轮换商品页面。
2. 按 **F8** 开始或暂停自动操作。
3. 程序会识别三格商品名称，找到「10小队生命值」后尝试购买。购买未成功时，会点击右侧招牌商品 **9 次**后再次尝试。
4. 按 **F9** 退出程序。鼠标移至屏幕左上角也可触发紧急停止。

<<<<<<< Updated upstream
程序会在游戏窗口左下角显示当前状态。
=======
程序会在游戏窗口左下角显示当前状态。若没有识别到目标，命令行会列出每格 OCR 读到的文字。

## GitHub 描述

An automation tool for Honkai: Star Rail's Black Market Manager bond, helping players automatically refresh shops and purchase target items.
>>>>>>> Stashed changes
