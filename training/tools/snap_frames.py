# -*- coding: utf-8 -*-
"""
================================================================
 实拍采集 —— 给你的车攒低视角训练数据
================================================================

为什么必须用这个：
    低视角这件事，公开数据集解决不了。我试过从 COCO 类数据合成
    「只有下半身」的样本（见 tools/lowangle_aug.py），61 张里只有 1 张
    效果是对的 —— 因为 COCO 大量 person 是近景半身（坐着、被桌子挡），
    几何上无法和"站立全身"区分，裁出来是头发/手，纯噪声。

    所以结论很直白：**低视角数据只能自己拍**。

怎么拍（关键，比脚本本身重要）：
    1. 摄像头架到车上真实高度（15~25 cm），镜头基本水平
    2. 人从 3m 外走近到 0.5m，边走边拍（每条 5~8 秒）
    3. 蹲下、侧身、只露腿 各拍几条 —— 这三种是低视角最容易漏的
    4. 猫狗用玩具/真宠物都行，贴地放
    5. 把「地毯/拖鞋/电线」这些车能压过去的东西也拍几条
       （模型必须学会"这些不用绕"）

    不需要板子。USB 摄像头架在同高度拍的图，训练出来的模型一样能用，
    部署时 OV5640 直接跑。省掉整条 UDP 链路。

用法：
    # 用 USB 摄像头，每 0.4 秒存一张，跳过几乎没变化的帧
    python tools\\snap_frames.py --src cam --out datasets\\custom_raw

    # 用自己的视频
    python tools\\snap_frames.py --src video --path D:\\take1.mp4 --out datasets\\custom_raw

    # 板子 UDP 图传（这条链路代码没在实板上验证过，见 detect_realtime.py 注释）
    python tools\\snap_frames.py --src udp --trigger-ip 192.168.1.10 --out datasets\\custom_raw

    # 存的时候顺手用现有模型预标注（省标注工时，但 person 会漏，要人工补）
    python tools\\snap_frames.py --src cam --out datasets\\custom_raw --prelabel

拍完怎么标：
    pip install labelImg -i https://mirrors.aliyun.com/pypi/simple/
    labelImg datasets\\custom_raw\\images datasets\\custom_raw\\classes.txt
    （预设就是 YOLO 格式，类别名在 classes.txt 里已写好）
    标完把 images/ 和 labels/ 并进 living 数据集：
    python tools\\build_living_dataset.py --person-yolo datasets\\custom_raw --person-cls <person的id>

作者：欧阳博怀    日期：2026-10-28
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path

import cv2
import numpy as np

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from living_semantics import CLASS_NAMES  # noqa: E402

UDP_FRAME_BYTES = 640 * 480 * 2
PERSON_ID = CLASS_NAMES.index("person")


# ---------------------------------------------------------------- 数据源
class CamSource:
    def __init__(self, idx: int, w: int, h: int):
        self.cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)   # DSHOW 在 Windows 上开得快
        if not self.cap.isOpened():
            self.cap = cv2.VideoCapture(idx)
        if not self.cap.isOpened():
            raise RuntimeError(f"打不开摄像头 {idx}")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        self.desc = f"摄像头 #{idx}"

    def read(self):
        ok, fr = self.cap.read()
        return fr if ok else None

    def close(self):
        self.cap.release()


class VideoSource:
    def __init__(self, path: str):
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            raise RuntimeError(f"打不开视频 {path}")
        self.desc = f"视频 {path}"
        self.ended = False

    def read(self):
        ok, fr = self.cap.read()
        if not ok:
            self.ended = True
            return None
        return fr

    def close(self):
        self.cap.release()


class UdpSource:
    """和 detect_realtime.py 里的实现保持同一套协议：
       4B 帧头 + 4B 尺寸 + RGB565 640x480 = 614408B
       这里把每个数据报的前 8 字节当帧头跳过（--pkt-header 8）。
    """

    def __init__(self, port: int, trigger_ip: str | None, pkt_header: int):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
        self.sock.bind(("0.0.0.0", port))
        self.sock.settimeout(2.0)
        self.hdr = pkt_header
        self.buf = bytearray()
        self.desc = f"UDP :{port}"
        if trigger_ip:
            self.sock.sendto(b"1", (trigger_ip, port))
            print(f"[UDP] 已向 {trigger_ip}:{port} 发触发字节")

    def read(self):
        try:
            data, _ = self.sock.recvfrom(65535)
        except socket.timeout:
            self.buf.clear()
            return None
        payload = data[self.hdr:]
        self.buf += payload
        if len(self.buf) >= UDP_FRAME_BYTES:
            raw = bytes(self.buf[:UDP_FRAME_BYTES])
            del self.buf[:UDP_FRAME_BYTES]
            f16 = np.frombuffer(raw, np.uint8).reshape(480, 640, 2).copy()
            return cv2.cvtColor(f16, cv2.COLOR_BGR5652BGR)
        return None

    def close(self):
        self.sock.close()


# ---------------------------------------------------------------- 去重
def too_similar(a: np.ndarray, b: np.ndarray, thresh: float = 3.0) -> bool:
    """两张图是否几乎一样。

    没有这个的话，车停着不动时你会存下几百张一模一样的图 —— 训练集
    看着"有 800 张"，实际有效信息不到 20 张，还会让 val 指标虚高。
    用小尺寸灰度差算，够快也够准。
    """
    ga = cv2.resize(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), (64, 48))
    gb = cv2.resize(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), (64, 48))
    return float(cv2.absdiff(ga, gb).mean()) < thresh


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", choices=["cam", "udp", "video"], default="cam")
    ap.add_argument("--cam", type=int, default=0)
    ap.add_argument("--path", default=None, help="--src video 时的视频路径")
    ap.add_argument("--host", type=int, default=1234, help="UDP 监听端口")
    ap.add_argument("--trigger-ip", default=None, help="板卡 IP，会自动发触发字节")
    ap.add_argument("--pkt-header", type=int, default=8, help="每个数据报的报头字节数")
    ap.add_argument("--out", default=str(PROJECT / "datasets" / "custom_raw"))
    ap.add_argument("--interval", type=float, default=0.4, help="每几秒存一张")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--max", type=int, default=0, help="最多存多少张，0=不限")
    ap.add_argument("--prelabel", action="store_true",
                    help="用 COCO 预训练权重预标注（conf 调低多召回，人工再删）")
    ap.add_argument("--prelabel-model", default="yolo11n.pt",
                    help="预标注权重。默认 COCO 权重的 yolo11n.pt 认识 person/cat/dog/bird；"
                         "换成 yolo11l.pt 更准但慢 5 倍")
    ap.add_argument("--prelabel-conf", type=float, default=0.15)
    ap.add_argument("--no-show", action="store_true")
    args = ap.parse_args()

    out = Path(args.out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "labels").mkdir(parents=True, exist_ok=True)
    # labelImg 读这个摸类名
    (out / "classes.txt").write_text("\n".join(CLASS_NAMES) + "\n", encoding="utf-8")

    if args.src == "cam":
        src = CamSource(args.cam, args.width, args.height)
    elif args.src == "video":
        if not args.path:
            print("[!] --src video 需要 --path")
            return 2
        src = VideoSource(args.path)
    else:
        src = UdpSource(args.host, args.trigger_ip, args.pkt_header)

    model = None
    coco_map = None
    if args.prelabel:
        try:
            from ultralytics import YOLO
            from autolabel import COCO_TO_OURS

            w = Path(args.prelabel_model)
            if not w.is_absolute():
                w = PROJECT / "weights" / w
            if not w.exists():
                raise FileNotFoundError(f"权重不存在 {w}")
            model = YOLO(str(w))
            names = model.names
            # 自适应判断权重是哪一类：
            #   80 类 = COCO 官方权重，认识 person/cat/dog/bird，但 id 是 COCO
            #          那套，必须映射到本工程的 18~21，否则会写成垃圾标签
            #   其他 = 自己训的 22 类模型，id 已经对了，原样用
            if len(names) == 80 and 0 in names and names.get(0) == "person":
                coco_map = COCO_TO_OURS
                print(f"[预标注] {w.name} (COCO 80类) -> 映射到本工程 "
                      f"{sorted(COCO_TO_OURS.values())}，conf={args.prelabel_conf}")
            else:
                print(f"[预标注] {w.name} ({len(names)}类) 直接使用，"
                      f"conf={args.prelabel_conf}")
        except Exception as exc:                      # noqa: BLE001
            print(f"[预标注] 加载模型失败（{exc}），改为不预标注")
            model = None

    print("=" * 62)
    print("实拍采集")
    print("=" * 62)
    print(f"  源     : {src.desc}")
    print(f"  输出   : {out}")
    print(f"  间隔   : {args.interval}s")
    print(f"  操作   : 空格 = 立刻存一张    q / Esc = 结束")
    print()

    n_saved = 0
    n_dup = 0
    n_lab_empty = 0
    last_saved = None
    t_last = 0.0
    t0 = time.time()

    try:
        while True:
            fr = src.read()
            if fr is None:
                if getattr(src, "ended", False):
                    print("[结束] 视频读完")
                    break
                continue
            if fr.dtype == np.uint16:
                fr = cv2.cvtColor(fr, cv2.COLOR_BGR5652BGR)
            if fr.shape[1] != args.width or fr.shape[0] != args.height:
                fr = cv2.resize(fr, (args.width, args.height))

            now = time.time()
            key = cv2.waitKey(1) & 0xFF
            manual = key == 32                    # 空格
            if key in (27, ord("q")):
                break

            due = (now - t_last) >= args.interval
            if (due or manual) and (last_saved is None or manual
                                    or not too_similar(fr, last_saved)):
                if last_saved is not None and not manual and too_similar(fr, last_saved):
                    n_dup += 1
                else:
                    stem = f"c_{int(time.time() * 1000) % 10_000_000:07d}"
                    cv2.imwrite(str(out / "images" / f"{stem}.jpg"), fr,
                                [cv2.IMWRITE_JPEG_QUALITY, 94])
                    if model is not None:
                        r = model.predict(fr, conf=args.prelabel_conf, verbose=False)[0]
                        lines = []
                        h, w = fr.shape[:2]
                        for b, c in zip(r.boxes.xyxy.cpu().numpy(),
                                        r.boxes.cls.cpu().numpy().astype(int)):
                            c = int(c)
                            if coco_map is not None:
                                if c not in coco_map:
                                    continue          # COCO 的其余 76 类丢掉
                                c = coco_map[c]
                            elif not (0 <= c < len(CLASS_NAMES)):
                                continue
                            x1, y1, x2, y2 = b
                            x1, y1 = max(0.0, x1), max(0.0, y1)
                            x2, y2 = min(float(w), x2), min(float(h), y2)
                            if x2 - x1 < 3 or y2 - y1 < 3:
                                continue
                            lines.append(
                                f"{c} {((x1+x2)/2)/w:.6f} {((y1+y2)/2)/h:.6f} "
                                f"{(x2-x1)/w:.6f} {(y2-y1)/h:.6f}")
                        (out / "labels" / f"{stem}.txt").write_text(
                            "\n".join(lines) + ("\n" if lines else ""))
                        if not lines:
                            n_lab_empty += 1
                    n_saved += 1
                    last_saved = fr.copy()
                    t_last = now
            elif due:
                n_skip += 1

            if n_saved and n_saved % 20 == 0 and manual:
                print(f"  已存 {n_saved} 张 ...")

            if args.max and n_saved >= args.max:
                print(f"[结束] 达到上限 {args.max} 张")
                break

            if not args.no_show:
                vis = fr.copy()
                cv2.putText(vis, f"saved {n_saved}  skip {n_dup}", (10, 26),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                cv2.imshow("snap (space=save one, q=quit)", vis)
    finally:
        src.close()
        if not args.no_show:
            cv2.destroyAllWindows()

    dt = time.time() - t0
    print()
    print("=" * 62)
    print(f"  存下 {n_saved} 张，跳过重复 {n_dup} 张，用时 {dt / 60:.1f} 分钟")
    if model is not None and n_lab_empty:
        print()
        print(f"  [!] 其中 {n_lab_empty} 张预标注结果为**空**。")
        print("      空标签在训练里等于『这张图什么都没有』。如果这些图里其实有人，")
        print("      那就是漏检，空标签会把真实目标教成背景 —— 务必在审核时补上。")
        print("      另外画面里确实没有活体的图（比如只有家具）留着当负样本是好事。")
    print(f"  目录 {out}")
    print()
    print("  下一步：")
    print(f"    1) 审核预标注：python tools\\review_labels.py --src {out / 'images'}")
    print(f"    2) 漏检的用 labelImg 补：labelImg {out / 'images'} {out / 'classes.txt'}")
    print(f"    3) 并进主数据集（--person-cls -1 = 保留全部类，自建数据必须用这个）：")
    print(f"       python tools\\build_living_dataset.py --person-yolo {out} --person-cls -1")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
