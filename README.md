# Jade Assistant

**Your personal assistant for Jade's Black Market.**

一款用于《崩坏：星穹铁道》「货币战争」模式中「黑市主理人」羁绊商店的自动化助手。它可以刷新轮换商品、识别目标物品，并自动尝试购买。

## 使用须知

- 游戏客户区比例需为 **16:9**，分辨率至少 **1280×720**。
- 使用前请进入游戏内「货币战争」的黑市商店界面。
- 请以管理员身份运行终端并启动程序，以便在游戏窗口内接收快捷键和执行操作。

## 构建环境

需要安装 **Python 3**。在项目文件夹中打开终端，安装依赖：

```powershell
py -m pip install -r requirements.txt
```

## 运行

在项目文件夹中执行：

```powershell
py game_shop_helper.py
```

## 操作

1. 确认游戏停留在黑市轮换商品页面。
2. 按 **F8** 开始或暂停自动操作。
3. 程序会循环刷新商品；发现目标物品后尝试购买。购买未成功时，会点击右侧招牌商品 **9 次**后再次尝试。
4. 按 **F9** 退出程序。鼠标移至屏幕左上角也可触发紧急停止。

程序会在游戏窗口左下角显示当前状态。

## GitHub 描述

An automation tool for Honkai: Star Rail's Black Market Manager bond, helping players automatically refresh shops and purchase target items.
