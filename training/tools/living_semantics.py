# -*- coding: utf-8 -*-
"""
================================================================
 活体/非活体 语义分组 —— 全工程唯一事实源
================================================================

为什么要有这个文件：
    模型训练时用的是「细类」（person / stool / table ...），
    但车上真正要做的决策只有两件事：
        ① 这是不是活物？（活物要停车鸣叫，不能撞）
        ② 这东西能不能直接压过去？（地毯能压，桌子得绕）

    把这两层语义写在代码里而不是塞进模型标签，好处是：
      * 8884 张已有的 18 类标注一个字都不用改
      * 想调整「什么算障碍」只改下面一张表，不用重训
      * 同一个模型可以同时喂给「停车逻辑」和「绕路逻辑」

被谁引用：
    tools/build_living_dataset.py    ← 建数据集时算 nc / names
    tools/detect_realtime.py         ← 推理时把 cls id 翻成语义
    （固件侧不引用，它只认串口命令）

作者：欧阳博怀    日期：2026-10-28
"""

from __future__ import annotations

#  ============================================================
#  一、类别表（顺序 = YOLO 的类 id，绝不能随意调整顺序）
# ============================================================
#  前 18 个沿用 datasets/18cls 的原顺序，保证已有标注零重映射；
#  person 追加在末尾，是这次新增的唯一活体类。
#  共 19 类。
# ============================================================
CLASS_NAMES: list[str] = [
    "trashcan",     # 0
    "slippers",     # 1
    "wire",         # 2
    "socks",        # 3
    "carpet",       # 4
    "book",         # 5
    "feces",        # 6
    "curtain",      # 7
    "stool",        # 8
    "bed",          # 9
    "sofa",         # 10
    "closestool",   # 11
    "table",        # 12
    "cabinet",      # 13
    "ajardoor",     # 14
    "opendoor",     # 15
    "closedoor",    # 16
    "stairway",     # 17
    "person",       # 18  ← 本次新增（活体）
]

# 为什么没有 cat / dog / bird：
#   一开始留了 19/20/21 三个动物类，想着"小猫小鸟也是活物，撞到不好"。
#   实测下来样本量是 cat 4 / dog 9 / bird 16 个框 —— 这个量级训出来的
#   不是检测器，是过拟合。硬留在类别表里的代价是：mAP 被三个必挂的类
#   拉到很低，看指标时反而分不清 person 到底学得好不好。
#   真正要处理的是"未知活物"这种情况，交给两层兜底：
#     ① 车体只有 15cm 高，鸟会飞走、猫会跳开，撞到也不伤人；
#     ② car_decide 遇到 unknown 类一律按障碍绕行，本来就不会压过去。
#   以后真要用，往后追加即可，前面 19 类的 id 一个不用动。

NC = len(CLASS_NAMES)


# ============================================================
# 二、三张语义表
# ============================================================

# ---- ① 活体判定 ----
#   命中即触发「停车 + 鸣叫」。
#
#   注意这里**不区分姿态**：站着的人的腿、蹲着的人、坐在地上的人、
#   趴着的猫，全都算活体。低视角下摄像头离地 20cm，人一蹲反而更容易
#   看到全身 —— 所以蹲/坐姿态不但不能排除，还是低视角下的主力样本。
#   把「站立全身」当成唯一正样本是早期的一个误判，已经纠正。
LIVING_CLASSES: set[str] = {
    "person",
}

# ---- ② 可通行性 ----
#   FLAT     : 贴着地面、车能直接碾过去的，不该触发绕行
#   OBSTACLE : 立体的、必须绕开的
#   PASSAGE  : 门 / 楼梯这类「通道」语义，既不是障碍也不能乱压
#   IGNORE   : 已知但决策无关（留口子，方便临时屏蔽某个类）
FLAT: set[str] = {
    "carpet", "wire", "socks", "slippers", "feces",
}

OBSTACLE: set[str] = {
    "trashcan", "book", "stool", "bed", "sofa", "closestool",
    "table", "cabinet", "curtain",
}

PASSAGE: set[str] = {
    "ajardoor", "opendoor", "closedoor", "stairway",
}


# ============================================================
# 三、查表函数
# ============================================================

def is_living(name: str) -> bool:
    """这个类是不是活物？"""
    return name in LIVING_CLASSES


def kind_of(name: str) -> str:
    """返回 'living' / 'flat' / 'obstacle' / 'passage' / 'unknown'。

    活体优先级最高：万一某个类同时被写进两张表，按活体处理（宁可停车）。
    """
    if name in LIVING_CLASSES:
        return "living"
    if name in FLAT:
        return "flat"
    if name in OBSTACLE:
        return "obstacle"
    if name in PASSAGE:
        return "passage"
    return "unknown"


#  给可视化用的固定色（BGR，OpenCV 顺序）
#  活体统一红、障碍橙、可压绿、通道蓝 —— 一眼能看懂画面里发生了什么
KIND_COLOR_BGR: dict[str, tuple[int, int, int]] = {
    "living":   (0, 0, 255),      # 红
    "obstacle": (0, 140, 255),    # 橙
    "flat":     (0, 200, 0),      # 绿
    "passage":  (255, 160, 0),    # 蓝
    "unknown":  (160, 160, 160),  # 灰
}


# ============================================================
# 四、自检
# ============================================================
def _self_check() -> None:
    """训练前跑一下，防止手改表时写错类名（拼错 = 永远匹配不上，且不报错）。"""
    problems: list[str] = []

    dup = {n for n in CLASS_NAMES if CLASS_NAMES.count(n) > 1}
    if dup:
        problems.append(f"CLASS_NAMES 有重复项：{dup}")

    all_grouped = LIVING_CLASSES | FLAT | OBSTACLE | PASSAGE
    unknown = all_grouped - set(CLASS_NAMES)
    if unknown:
        problems.append(f"下面这些名字不在 CLASS_NAMES 里（拼写错了？）：{sorted(unknown)}")

    not_grouped = set(CLASS_NAMES) - all_grouped
    if not_grouped:
        problems.append(
            f"这些类没归入任何语义组，推理时会被当成 unknown：{sorted(not_grouped)}"
            " —— 如果是有意的可以忽略"
        )

    for p in problems:
        print(f"  [!] {p}")
    if not problems:
        print(f"  [OK] 语义表自检通过：共 {NC} 类，"
              f"活体 {len(LIVING_CLASSES)} / 可压 {len(FLAT)} / "
              f"障碍 {len(OBSTACLE)} / 通道 {len(PASSAGE)}")


if __name__ == "__main__":
    print("=" * 60)
    print("活体/非活体 语义分组")
    print("=" * 60)
    for i, n in enumerate(CLASS_NAMES):
        print(f"  {i:2d}  {n:<12s} {kind_of(n)}")
    print()
    _self_check()
