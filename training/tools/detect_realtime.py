# -*- coding: utf-8 -*-
"""
实时视频检测 —— AMD Radeon 780M (ROCm) + FP16 + 优化的前/后处理

设计目标：把总感知延迟压进 25 ms（40 FPS 以上），并满足赛题 3.2.4.3 的口径：
  单样本 batch=1、分别统计 预处理 / 模型推理 / 后处理 / 总感知、报 P50/P95/P99/max。

相比直接调 YOLO.predict() 改了三处：
  1) 预处理：letterbox 在 CPU 上用 uint8 做（数据量只有 fp32 的 1/4），
     BGR→RGB 后再上显存，归一化和 HWC→CHW 全部丢给 GPU。
  2) 后处理：在 GPU 上先按 conf 过滤再做 NMS，只把少量候选框拷回 CPU。
     原版会把整张 [N, 4+nc] 张量搬回主机，在 780M 上是主要开销。
  3) 推理：FP16。RDNA3 的 fp16 通路是 2 倍速率，实测推理 20.09 ms vs FP32 29.63 ms。

用法：
    # 摄像头实时（默认 torch+ROCm 后端）
    python detect_realtime.py --source webcam
    # 本地视频
    python detect_realtime.py --source D:\\test.mp4
    # ZYNQ 板卡 UDP 图传（RGB565 640x480）
    python detect_realtime.py --source udp
    # 只测速不出图（无需硬件）
    python detect_realtime.py --source synthetic --bench 200
    # 用验证集图片校验结果与 ultralytics 官方实现一致
    python detect_realtime.py --verify 20

两个推理后端（--backend）：
    torch : torch + ROCm（HIP）。赛题 3.2.3.1 指定的软件栈，默认走这个。
    dml   : onnxruntime + DmlExecutionProvider（DirectML）。480/FP16 推理约 2.4 ms，
            比 ROCm 快近 10 倍，但它不是 ROCm 栈，合规性需主办方确认。
            DML 只在 ryzen-ai-1.6.0 环境里，用那个环境的 python 跑：
    D:\\miniconda\\envs\\ryzen-ai-1.6.0\\python.exe tools\\detect_realtime.py \\
        --backend dml --source synthetic --bench 200
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
os.environ.setdefault("YOLO_CONFIG_DIR", str(PROJECT / "config"))
os.environ["PYTHONNOUSERSITE"] = "1"
os.environ.pop("PYTHONPATH", None)

# ryzen-ai-1.6.0 是唯一带 DmlExecutionProvider 的环境，但它没装 opencv，
# 且其 site-packages 只读（需要管理员）。所以 cv2 装在工程内的 vendor 目录，
# 这里在 import 失败时按需挂载——不污染官方 Ryzen AI 环境。
_VENDOR = PROJECT / "vendor" / "dmlpy"
try:
    import cv2
except ImportError:                                  # pragma: no cover
    if not _VENDOR.exists():
        raise
    sys.path.insert(0, str(_VENDOR))
    import cv2

import numpy as np

# ---- 决策层（活体判定 / 停车鸣叫）----
# 这三个模块是这个目录下的独立文件，缺失时不影响纯检测模式，
# 只是 --decide 不可用。用 try 包起来是为了让脚本在没有硬件的
# 机器上也能跑纯测速。
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from living_semantics import kind_of, KIND_COLOR_BGR
    from motion_gate import MotionGate
    from car_decide import CarDecider, MonoGroundDistance, FpgaLink
    _DECIDE_OK = True
except ImportError as _exc:                          # pragma: no cover
    _DECIDE_OK = False
    _DECIDE_ERR = str(_exc)

WEIGHTS = PROJECT / "runs" / "18cls_yolo11n" / "weights" / "best.pt"
VAL_DIR = PROJECT / "datasets" / "18cls" / "images" / "val"
DATA_YAML = PROJECT / "datasets" / "18cls" / "data.yaml"

# ---- ZYNQ UDP 图传参数（和 FPGA 工程里的一致）----
UDP_LISTEN_IP = "0.0.0.0"
UDP_PORT = 1234
UDP_TRIGGER = b"1"          # 上位机必须先发这个字节，板卡才开始发图
UDP_FRAME_BYTES = 640 * 480 * 2   # RGB565 = 614400
UDP_PKT_BYTES = UDP_FRAME_BYTES + 8   # 4B 帧头 + 4B 尺寸


# ---------------------------------------------------------------- 预处理
def letterbox_u8(im: np.ndarray, size: int):
    """
    CPU 侧 letterbox，全程保持 uint8。
    im: BGR HxWx3 uint8
    返回: (RGB size x size uint8, scale, pad_w, pad_h)
    """
    h, w = im.shape[:2]
    scale = min(size / w, size / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(im, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    pad_w = (size - nw) // 2
    pad_h = (size - nh) // 2
    canvas[pad_h:pad_h + nh, pad_w:pad_w + nw] = resized
    return cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB), scale, pad_w, pad_h


def to_tensor(rgb_u8: np.ndarray, dev: str, half: bool):
    """uint8 HWC -> GPU 上的 NCHW 归一化张量（转换全部在显存里做）"""
    import torch

    t = torch.from_numpy(rgb_u8).to(dev, non_blocking=True)
    t = t.permute(2, 0, 1).unsqueeze(0)          # HWC -> NCHW
    t = t.to(torch.float16) if half else t.to(torch.float32)
    return t.mul_(1.0 / 255.0)


# ---------------------------------------------------------------- 后处理
def postprocess(pred, conf_thres: float, iou_thres: float, max_det: int):
    """
    pred: (1, 4+nc, N) 的 GPU 张量
    返回: (boxes_xyxy Nx4, scores N, cls N) 都在 CPU 上
    思路：全部在 GPU 上过滤 + NMS，只把幸存者拷回来。
    """
    import torch
    from ultralytics.utils.nms import TorchNMS, xywh2xyxy

    p = pred[0].t().contiguous()          # (N, 4+nc)
    boxes = xywh2xyxy(p[:, :4])           # xywh -> xyxy，像素坐标
    cls_scores = p[:, 4:]
    conf, cls = cls_scores.max(1)         # 单标签：取最大类
    keep = conf > conf_thres
    if not keep.any():
        return (torch.zeros((0, 4)), torch.zeros(0), torch.zeros(0, dtype=torch.long))

    boxes, conf, cls = boxes[keep], conf[keep], cls[keep]
    # 候选过多时先截断，防止 NMS 被拖慢（官方实现有同样的 max_nms 逻辑）
    if boxes.shape[0] > 300:
        order = conf.argsort(descending=True)[:300]
        boxes, conf, cls = boxes[order], conf[order], cls[order]

    # 按类 NMS：不同类别用 7680px 的坐标偏移隔开，避免跨类误抑制
    # （和官方 non_max_suppression 里 max_wh 的做法一致）
    offset = cls.float().view(-1, 1) * 7680.0
    boxes_f = boxes.float() + offset
    conf_f = conf.float()
    if _NMS_FN is not None:
        idx = _NMS_FN(boxes_f, conf_f, iou_thres)[:max_det]
    else:
        idx = TorchNMS.nms(boxes_f, conf_f, iou_thres)[:max_det]

    # 只做一次主机拷回：三个张量拼一起，减少 GPU->CPU 的同步次数
    # （每次 .cpu() 都是一次同步，780M 上同步开销可达毫秒级）
    out = torch.cat((boxes[idx].float(), conf[idx].float().view(-1, 1),
                     cls[idx].float().view(-1, 1)), 1).cpu()
    return out[:, :4].contiguous(), out[:, 4].contiguous(), out[:, 5].long().contiguous()


def unscale(boxes, scale, pad_w, pad_h):
    """把 imgsz 坐标系下的框映射回原始帧坐标（torch 张量 / numpy 数组都支持）"""
    if len(boxes) == 0:
        return boxes
    b = boxes.clone() if hasattr(boxes, "clone") else np.array(boxes, copy=True)
    b[:, [0, 2]] = (b[:, [0, 2]] - pad_w) / scale
    b[:, [1, 3]] = (b[:, [1, 3]] - pad_h) / scale
    return b


# ---------------------------------------------------------------- 后处理（numpy 版）
# DML 走 onnxruntime，输出是 numpy，而且那个环境里没有 torch，
# 所以这里必须有一份纯 numpy 的后处理。逻辑与上面的 torch 版逐行对应。
def _nms_np(boxes_xyxy: np.ndarray, scores: np.ndarray, iou_thres: float) -> np.ndarray:
    """贪心 NMS。优先用 cv2 的 C++ 实现，没有就退回 numpy 实现。"""
    if boxes_xyxy.shape[0] == 0:
        return np.zeros(0, dtype=np.int64)
    if hasattr(cv2, "dnn") and hasattr(cv2.dnn, "NMSBoxes"):
        rects = np.stack([boxes_xyxy[:, 0], boxes_xyxy[:, 1],
                          boxes_xyxy[:, 2] - boxes_xyxy[:, 0],
                          boxes_xyxy[:, 3] - boxes_xyxy[:, 1]], 1)
        res = cv2.dnn.NMSBoxes(rects.tolist(), scores.tolist(), 0.0, float(iou_thres))
        # 不同 OpenCV 版本返回值形态不同：() / (N,1) / (N,)
        arr = np.asarray(res)
        return arr.reshape(-1).astype(np.int64) if arr.size else np.zeros(0, np.int64)

    order = np.argsort(-scores)
    x1, y1, x2, y2 = (boxes_xyxy[:, i] for i in range(4))
    area = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    keep = []
    alive = np.ones(len(order), dtype=bool)
    for k in range(len(order)):
        if not alive[k]:
            continue
        i = order[k]
        keep.append(i)
        rest = order[k + 1:]
        rest = rest[alive[k + 1:]]
        if rest.size == 0:
            break
        iw = np.minimum(x2[i], x2[rest]) - np.maximum(x1[i], x1[rest])
        ih = np.minimum(y2[i], y2[rest]) - np.maximum(y1[i], y1[rest])
        inter = np.maximum(0.0, iw) * np.maximum(0.0, ih)
        iou = inter / (area[i] + area[rest] - inter + 1e-9)
        alive[k + 1:] &= ~(iou > iou_thres)
    return np.asarray(keep, dtype=np.int64)


def postprocess_np(pred: np.ndarray, conf_thres: float, iou_thres: float, max_det: int):
    """pred: (1, 4+nc, N) -> (boxes_xyxy Nx4, scores N, cls N)，全 numpy"""
    p = np.ascontiguousarray(pred[0].T)          # (N, 4+nc)
    xywh, cls_scores = p[:, :4], p[:, 4:]
    cls = cls_scores.argmax(1)
    conf = cls_scores[np.arange(cls_scores.shape[0]), cls]
    keep = conf > conf_thres
    if not keep.any():
        return (np.zeros((0, 4), np.float32), np.zeros(0, np.float32),
                np.zeros(0, np.int64))
    xywh, conf, cls = xywh[keep], conf[keep], cls[keep]
    if xywh.shape[0] > 300:
        order = np.argsort(-conf)[:300]
        xywh, conf, cls = xywh[order], conf[order], cls[order]

    b = np.empty_like(xywh)
    b[:, 0] = xywh[:, 0] - xywh[:, 2] / 2
    b[:, 1] = xywh[:, 1] - xywh[:, 3] / 2
    b[:, 2] = xywh[:, 0] + xywh[:, 2] / 2
    b[:, 3] = xywh[:, 1] + xywh[:, 3] / 2
    # 同 torch 版：按类偏移再 NMS，避免跨类误抑制。
    # 注意偏移只用于 NMS 排序，返回的必须是未偏移的框（否则坐标会多出 cls*7680）。
    b_off = b + (cls.astype(np.float32) * 7680.0)[:, None]
    idx = _nms_np(b_off, conf, iou_thres)[:max_det]
    return (b[idx].astype(np.float32), conf[idx].astype(np.float32),
            cls[idx].astype(np.int64))


# ---------------------------------------------------------------- 数据源
class WebcamSource:
    ended = False

    def __init__(self, index: int = 0):
        self.cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if not self.cap.isOpened():
            raise RuntimeError(f"打不开摄像头 {index}")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.desc = f"摄像头 {index}"

    def read(self):
        ok, frame = self.cap.read()
        return frame if ok else None

    def close(self):
        self.cap.release()


class VideoSource:
    def __init__(self, path: str):
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            raise RuntimeError(f"打不开视频 {path}")
        self.desc = f"视频 {Path(path).name}"
        self.ended = False

    def read(self):
        ok, frame = self.cap.read()
        if not ok:
            self.ended = True              # 播完必须能退出，否则主循环会空转
            return None
        return frame

    def close(self):
        self.cap.release()


class UdpSource:
    """
    ZYNQ 图传接收（RGB565 640x480 = 614400 字节/帧）

    重要：UDP 单个数据报最大只有 65507 字节，614400 字节的图像**必然分多包到达**，
    不可能一次 recvfrom 拿全。所以这里做的是"累积重组"：
    把陆续收到的数据报有效载荷依次拼接，攒够 614400 字节就拼成一帧。

    两个可调项，需要和 FPGA 侧的实际分包格式对齐：
      --pkt-header N : 每个数据报开头跳过 N 字节报头（默认 0，即纯像素数据）
      帧内字节顺序按 RGB565 小端解释（cv2.COLOR_BGR5652BGR）

    这一路没有在真实板卡上验证过，接入前请先确认板卡的分包方式。
    """

    ended = False

    def __init__(self, port: int = UDP_PORT, trigger_ip: str | None = None,
                 pkt_header: int = 0):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
        self.sock.bind((UDP_LISTEN_IP, port))
        self.sock.settimeout(2.0)
        self.pkt_header = max(0, pkt_header)
        self.buf = bytearray()
        self.desc = (f"UDP :{port} (RGB565 640x480, 每包跳过 {self.pkt_header}B 报头)")
        self.peer = None
        if trigger_ip:
            # 板卡要先收到一个 "1" 才开始发图
            self.sock.sendto(UDP_TRIGGER, (trigger_ip, port))
            print(f"[UDP] 已向 {trigger_ip}:{port} 发送触发字节 {UDP_TRIGGER!r}")

    def read(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                if self.buf:
                    print(f"[UDP] 超时，丢弃未拼完的 {len(self.buf)} 字节")
                    self.buf.clear()
                return None
            if self.peer is None:
                self.peer = addr
                print(f"[UDP] 首包来自 {addr[0]}:{addr[1]}，{len(data)} 字节")
            payload = data[self.pkt_header:]
            if not payload:
                continue
            self.buf += payload
            if len(self.buf) >= UDP_FRAME_BYTES:
                frame = bytes(self.buf[:UDP_FRAME_BYTES])
                del self.buf[:UDP_FRAME_BYTES]
                return np.frombuffer(frame, dtype=np.uint8).reshape(480, 640, 2).copy()
            # 防止一直拼不满导致缓冲区无限增长
            if len(self.buf) > 3 * UDP_FRAME_BYTES:
                self.buf.clear()

    def close(self):
        self.sock.close()


class SyntheticSource:
    """不出图的测速源：直接给真实验证集图片，避免噪声图把 NMS 打爆"""

    ended = False

    def __init__(self, n: int = 16):
        files = sorted(VAL_DIR.glob("*.jpg"))[:n] if VAL_DIR.exists() else []
        self.frames = []
        for f in files:
            im = cv2.imread(str(f))
            if im is not None:
                self.frames.append(cv2.resize(im, (640, 480)))
        if not self.frames:
            self.frames = [np.full((480, 640, 3), 114, np.uint8)]
        self.i = 0
        self.desc = f"验证集图片 {len(self.frames)} 张"

    def read(self):
        f = self.frames[self.i % len(self.frames)]
        self.i += 1
        return f

    def close(self):
        pass


def udp_to_bgr(frame16: np.ndarray):
    """RGB565 单通道 16 位 -> BGR 8 位。

    ⚠ 这里原来写的是 cv2.cvtColor(frame16, cv2.COLOR_BGR5652BGR)，是错的。
      OpenCV 那个转换要求输入是 **3 通道 8 位**，不是单通道 16 位，
      传 uint16 单通道会抛：
          error: (-15:Bad number of channels)
      因为 UDP 这条路一直没在真实板卡上跑过，这个 bug 从没被触发。

    位布局（RGB565 小端）：R[15:11] G[10:5] B[4:0]
    扩展回 8 位用高位复制，不是简单左移 —— 左移会让 255 变成 248，
    整幅图暗一档。
    """
    f = frame16.astype(np.uint32)
    b = f & 0x1F
    g = (f >> 5) & 0x3F
    r = (f >> 11) & 0x1F
    b = (b << 3) | (b >> 2)
    g = (g << 2) | (g >> 4)
    r = (r << 3) | (r >> 2)
    return np.dstack([b, g, r]).astype(np.uint8)


# ---------------------------------------------------------------- 主流程
def load_model(dev: str, half: bool):
    from ultralytics import YOLO

    if not WEIGHTS.exists():
        raise FileNotFoundError(f"权重不存在: {WEIGHTS}")
    y = YOLO(str(WEIGHTS))
    # 必须 fuse()：把 Conv+BN 折叠成 Conv。ultralytics 的 predict 内部会做这一步，
    # 不做的话推理结果会有细微差异（FP16 下可放大到几个像素），而且更慢。
    net = y.model.fuse().to(dev).eval()
    if half:
        net = net.half()
    return net, y.names


def read_names(path: Path) -> dict:
    """从 data.yaml 读 {id: 类名}。DML 环境里没有 ultralytics，只能读文件。"""
    names = {}
    if not path.exists():
        return names
    in_names = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not in_names:
            in_names = line.strip().startswith("names:")
            continue
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if ":" not in s:
            break
        k, v = s.split(":", 1)
        try:
            names[int(k.strip())] = v.strip().strip("'\"")
        except ValueError:
            break
    return names


# ---------------------------------------------------------------- 推理后端
class TorchBackend:
    """torch + ROCm（HIP）。这是当前的默认路线，完全在赛题要求的 ROCm 栈内。"""

    tag = "torch + ROCm"

    def __init__(self, dev: str, half: bool):
        import torch
        self.torch = torch
        self.dev = dev
        self.half = half
        self.net, self.names = load_model(dev, half)

    def sync(self):
        if self.dev != "cpu":
            self.torch.cuda.synchronize()

    def infer(self, rgb_u8: np.ndarray):
        torch = self.torch
        t = torch.from_numpy(rgb_u8).to(self.dev, non_blocking=True)
        t = t.permute(2, 0, 1).unsqueeze(0)
        t = t.to(torch.float16) if self.half else t.to(torch.float32)
        x = t.mul_(1.0 / 255.0)
        with torch.no_grad():
            pred = self.net(x)
        pred = pred[0] if isinstance(pred, (list, tuple)) else pred
        self.sync()                       # 关键：不同步耗时会漏到后处理里
        return pred

    def post(self, pred, conf, iou, max_det):
        return postprocess(pred, conf, iou, max_det)

    def desc(self) -> str:
        import torch
        name = torch.cuda.get_device_name(0) if self.dev != "cpu" else "CPU"
        return f"{self.tag}  {name}  {'FP16' if self.half else 'FP32'}"


class DmlBackend:
    """
    DirectML（onnxruntime + DmlExecutionProvider）。

    注意：这一路不是 ROCm 栈，合规解释权在主办方，见交底文档。

    **必须显式指定 device_id**：这台机器有三块 DXGI 适配器，
    DML 的默认设备是**独显 RTX 4060**（device_id=1），不是 Ryzen AI 的 780M。
    不指定就会静默跑在 NVIDIA 卡上——既不符合赛题，也让性能数据失真。
    实测（480，fp16）：780M = 5.5 ms，RTX 4060 = 2.4 ms。
    所以这里默认按厂商 ID(0x1002) 自动找 AMD 适配器，见 `dxgi_adapters.py`。

    与 torch 路线的两点差异：
      * 输入是 float32 NCHW（ONNX 图内没有归一化），所以归一化在 CPU 上做；
        H2D 拷贝发生在 session.run 内部，因此这一块时间被计入"推理"段。
      * enable_fp16=1 只影响 DML 内部计算精度，模型输入输出仍是 fp32。
    """

    tag = "DirectML"

    def __init__(self, onnx_path: Path, fp16: bool, device_id=None):
        import onnxruntime as ort

        # 先枚举适配器，把 device_id 和真实显卡对上
        self.adapter = None
        try:
            from dxgi_adapters import enum_adapters, find_device_id
            adapters = enum_adapters()
        except Exception:
            adapters, find_device_id = [], None

        if device_id is None and find_device_id is not None:
            device_id = find_device_id(adapters, 0x1002)     # 0x1002 = AMD
        if device_id is not None:
            for a in adapters:
                if a["device_id"] == device_id:
                    self.adapter = a
                    break
            if self.adapter is None and adapters:
                raise SystemExit(
                    f"device_id={device_id} 不存在。可用适配器：\n" +
                    "\n".join(f"  [{a['device_id']}] {a['name']}" for a in adapters))

        if not onnx_path.exists():
            raise FileNotFoundError(
                f"ONNX 不存在: {onnx_path}\n"
                f"先用 export_onnx.py 导出：python export_onnx.py <权重> {onnx_path.name} 12 480")
        so = ort.SessionOptions()
        so.log_severity_level = 3
        provs = ["DmlExecutionProvider", "CPUExecutionProvider"]
        opt0 = {"enable_fp16": "1"} if fp16 else {}
        if device_id is not None:
            opt0["device_id"] = str(device_id)
        # ort 要求 provider_options 与 providers 等长；不给的那一项填 {}
        opts = [opt0, {}]

        self.sess = ort.InferenceSession(str(onnx_path), so,
                                         providers=provs, provider_options=opts)
        self.inp = self.sess.get_inputs()[0].name
        self.fp16 = fp16
        self.device_id = device_id
        self.onnx = onnx_path
        self._buf = None
        self.names = read_names(DATA_YAML)
        used = self.sess.get_providers()
        if "DmlExecutionProvider" not in used:
            raise RuntimeError(f"DML 没启用，实际 provider={used}")

        # 选到非 AMD 卡时给出明确告警：赛题要求是 Ryzen AI 平台
        if self.adapter and self.adapter["vendor_id"] != 0x1002:
            print(f"[!] 当前 DML 设备是 {self.adapter['name']}"
                  f"（vendor 0x{self.adapter['vendor_id']:04X}），"
                  f"不是 AMD 适配器。赛题要求 Ryzen AI 平台，请用 --dml-device 指定。")
        elif self.adapter is None and device_id is None:
            print("[!] 没能自动识别 AMD 适配器，用了 ORT 默认设备——"
                  "很可能就是独显。建议显式加 --dml-device。")

    def sync(self):
        pass                              # session.run 是同步调用，不需要额外同步

    def infer(self, rgb_u8: np.ndarray) -> np.ndarray:
        """
        uint8 HWC -> fp32 NCHW。

        这里必须写进一块**预分配的连续缓冲区**，不能直接返回
        rgb.transpose(2,0,1)[None]：那是非连续 view，ORT 会额外做一次
        连续化拷贝，实测 sess.run 从 2.39 ms 劣化到 4.33 ms。
        预分配后 buffer 复用，也省掉每帧 2.7 MB 的内存分配。
        """
        if self._buf is None or self._buf.shape[2:] != rgb_u8.shape[:2]:
            h, w = rgb_u8.shape[:2]
            self._buf = np.empty((1, 3, h, w), np.float32)
        self._buf[0] = rgb_u8.transpose(2, 0, 1)   # HWC -> CHW
        np.multiply(self._buf, 1.0 / 255.0, out=self._buf)
        return self.sess.run(None, {self.inp: self._buf})[0]

    def post(self, pred, conf, iou, max_det):
        return postprocess_np(pred, conf, iou, max_det)

    def desc(self) -> str:
        dev = ""
        if self.adapter:
            dev = (f"  device_id={self.device_id} "
                   f"[{self.adapter['name']}, luid {self.adapter['luid_str']}]")
        elif self.device_id is not None:
            dev = f"  device_id={self.device_id}"
        return (f"{self.tag}  {self.onnx.name}  "
                f"{'fp16' if self.fp16 else 'fp32'}{dev}")


def build_backend(args):
    if args.backend == "dml":
        onnx = Path(args.onnx) if args.onnx else \
            PROJECT / "export" / f"yolo11n_18cls_{args.imgsz}.onnx"
        return DmlBackend(onnx, args.half, args.dml_device)

    import torch
    dev = "cuda:0" if torch.cuda.is_available() else "cpu"
    half = args.half and dev != "cpu"
    if dev == "cpu" and args.half:
        print("[!] 没有 ROCm 设备，FP16 关闭")
    return TorchBackend(dev, half)


def draw(frame, boxes, scores, cls, names, semantic: bool = False, dists=None):
    """画检测框。

    semantic=True 时用语义色（活体红/障碍橙/可压绿/通道蓝），
    并额外标出单目估算距离 —— 调车时一眼就能看出"它判成了什么"。
    """
    out = frame
    for i, (b, s, c) in enumerate(zip(boxes.tolist(), scores.tolist(), cls.tolist())):
        x1, y1, x2, y2 = [int(round(v)) for v in b]
        name = names.get(int(c), c)
        if semantic:
            color = KIND_COLOR_BGR.get(kind_of(str(name)), (160, 160, 160))
        else:
            color = color_for(int(c))
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        label = f"{name} {s:.2f}"
        if dists is not None and i < len(dists) and dists[i] is not None:
            label += f" {dists[i]:.2f}m"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(out, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
        cv2.putText(out, label, (x1 + 2, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (0, 0, 0), 1, cv2.LINE_AA)
    return out


# 每种动作条的颜色（BGR）
_MODE_COLOR = {
    "GO": (0, 200, 0),
    "SLOW": (0, 200, 255),
    "STOP": (0, 0, 255),
    "AVOID_L": (255, 160, 0),
    "AVOID_R": (255, 160, 0),
}


def draw_hud(frame, act, info: dict):
    """画面左上角状态条。

    只能用 ASCII：OpenCV 的 putText 不支持中文，写中文会变成一串 ?????
    （踩过）。中文说明留在终端日志里，画面上一律英文。
    """
    h, w = frame.shape[:2]
    color = _MODE_COLOR.get(act.mode, (200, 200, 200))
    text = act.mode + ("  << BEEP >>" if act.mode == "STOP" and act.beep else "")
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.85, 2)
    cv2.rectangle(frame, (8, 8), (20 + tw + 8, 20 + th + 8), color, -1)
    cv2.putText(frame, text, (14, 14 + th), cv2.FONT_HERSHEY_SIMPLEX, 0.85,
                (255, 255, 255), 2, cv2.LINE_AA)

    lines = []
    if info.get("living_m") is not None:
        lines.append(f"LIVING  {info['living_m']:.2f} m")
    if info.get("obstacle_m") is not None:
        lines.append(f"OBST    {info['obstacle_m']:.2f} m")
    lines.append(f"DET {info.get('n_det', 0)}  FLAT {info.get('n_flat', 0)}  "
                 f"duty {act.duty_l}/{act.duty_r}")
    y = 20 + th + 26
    for ln in lines:
        cv2.putText(frame, ln, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.48,
                    (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(frame, ln, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.48,
                    (255, 255, 255), 1, cv2.LINE_AA)
        y += 17
    return frame


_COLORS = {}

# NMS 实现选择：
#   TorchNMS.nms 是 Python while 循环，每保留一个框都要重算一次 IoU 并同步读回判断，
#   在 780M(ROCm) 上每次同步约 1ms，十几个框就吃掉近 10ms。
#   torchvision.ops.nms 是单个融合 kernel，只需一次同步，快一个数量级。
try:
    import torchvision as _tv
    _NMS_FN = _tv.ops.nms
except Exception:                                    # torchvision 不可用时退回
    _NMS_FN = None


def color_for(i: int):
    if i not in _COLORS:
        rng = np.random.default_rng(i * 7 + 13)
        _COLORS[i] = tuple(int(v) for v in rng.integers(60, 255, 3))
    return _COLORS[i]


def pctl(sorted_arr, p):
    if not sorted_arr:
        return float("nan")
    k = min(len(sorted_arr) - 1, int(round((len(sorted_arr) - 1) * p)))
    return sorted_arr[k]


def run(args) -> int:
    be = build_backend(args)
    names = be.names

    print("=" * 66)
    print("实时检测")
    print("=" * 66)
    print(f"  后端   : {be.desc()}")
    print(f"  精度   : {'FP16' if args.half else 'FP32'}")
    print(f"  输入   : {args.imgsz}   conf={args.conf}  iou={args.iou}")
    print()

    # 数据源
    if args.source == "webcam":
        src = WebcamSource(args.cam)
    elif args.source == "udp":
        src = UdpSource(args.port, args.trigger_ip, args.pkt_header)
    elif args.source == "synthetic":
        src = SyntheticSource()
    else:
        src = VideoSource(args.source)
    print(f"  数据源 : {src.desc}")

    # ---------------- 决策层初始化 ----------------
    decider = link = mg = None
    if args.decide:
        if not _DECIDE_OK:
            print(f"  [!] 决策模块导入失败（{_DECIDE_ERR}），本次退回纯检测")
        else:
            dist = MonoGroundDistance(K=args.dist_k, y_horizon=args.dist_horizon,
                                     img_h=480)
            decider = CarDecider(dist=dist, img_w=args.imgsz)
            decider.LIVING_STOP = args.stop_dist
            decider.LIVING_RESUME = args.stop_dist * 1.3      # 滞回 30%
            decider.LIVING_SLOW = args.stop_dist * 1.8
            mg = MotionGate(mode=args.motion)
            link = FpgaLink(args.serial, dry=args.dry)
            print(f"  决策   : 开  停距 {args.stop_dist}m  "
                  f"K={args.dist_k} 地平线={args.dist_horizon}")
            print(f"  运动判据: {args.motion}")
            print(f"  串口   : {args.serial}{' (dry-run)' if args.dry else ''}")

    # 预热：多跑几轮，把 MIOpen 卷积核自动调优、DML 首次算子编译、NMS 首次 JIT
    # 这类一次性开销吃掉。
    # 注意必须连**后处理一起**预热：torchvision.ops.nms 首次调用要 JIT 编译，
    # 实测一次性吃掉 2.8 秒。只暖 infer 的话这笔开销会落到正式统计的第 1 帧上，
    # 把 P99 直接拉爆（之前就踩了这个坑）。
    warm = 0
    for _ in range(20):
        f = src.read()
        if f is None:
            break
        if f.dtype == np.uint16:
            f = udp_to_bgr(f)
        rgb, _, _, _ = letterbox_u8(f, args.imgsz)
        _p = be.infer(rgb)
        be.post(_p, args.conf, args.iou, args.max_det)   # ← 关键：把 NMS 也跑一遍
        warm += 1
    be.sync()
    print(f"  预热   : {warm} 帧（含后处理）")

    pre_l, inf_l, post_l, tot_l = [], [], [], []
    writer = None
    n_done = 0
    t_start = time.perf_counter()
    try:
        limit = args.bench or args.frames or 10 ** 9
        while n_done < limit:
            raw = src.read()
            if raw is None:
                if args.bench or getattr(src, "ended", False):
                    break
                continue
            frame = udp_to_bgr(raw) if raw.dtype == np.uint16 else raw

            t0 = time.perf_counter()
            rgb, sc, pw, ph = letterbox_u8(frame, args.imgsz)
            be.sync()                        # torch 后端把 H2D/格式转换算进预处理
            t1 = time.perf_counter()

            pred = be.infer(rgb)
            t2 = time.perf_counter()

            boxes, scores, cls = be.post(pred, args.conf, args.iou, args.max_det)
            t3 = time.perf_counter()

            pre_l.append((t1 - t0) * 1000)
            inf_l.append((t2 - t1) * 1000)
            post_l.append((t3 - t2) * 1000)
            tot_l.append((t3 - t0) * 1000)
            n_done += 1

            if args.bench:
                continue

            boxes0 = unscale(boxes, sc, pw, ph)

            # ---------------- 决策 ----------------
            # 注意顺序：先 score（用上一帧做参考算光流），再 push（把当前帧存成参考）。
            # 反过来的话 score 永远拿到零位移，光流恒为空。
            act = None
            dets = None
            if decider is not None:
                dets = [
                    {"name": str(names.get(int(c), c)), "conf": float(s), "box": tuple(b)}
                    for b, s, c in zip(boxes0.tolist(), scores.tolist(), cls.tolist())
                ]
                msc = mg.score([d["box"] for d in dets], frame)
                act = decider.update(dets, msc, moving=True)
                mg.push(frame)
                mg.set_stopped(act.mode == "STOP")
                link.send_action(act)

            dists = None
            if decider is not None:
                dists = [decider._dist_of(d["box"]) for d in dets]

            vis = draw(frame.copy(), boxes0, scores, cls, names,
                       semantic=decider is not None, dists=dists)
            if act is not None:
                vis = draw_hud(vis, act, decider.last_seen)
            fps = 1000.0 / (sum(tot_l[-10:]) / len(tot_l[-10:]))
            ftxt = f"{fps:5.1f} FPS  {tot_l[-1]:5.1f} ms"
            # 放右上角，右对齐 —— 左上角已经被 HUD 占了
            (fw, fh), _ = cv2.getTextSize(ftxt, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)
            cv2.putText(vis, ftxt, (vis.shape[1] - fw - 10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2, cv2.LINE_AA)
            if args.save:
                if writer is None:
                    h, w = vis.shape[:2]
                    writer = cv2.VideoWriter(args.save, cv2.VideoWriter_fourcc(*"mp4v"),
                                             30, (w, h))
                writer.write(vis)
            if not args.no_show:
                cv2.imshow("detect", vis)
                if cv2.waitKey(1) & 0xFF == 27:
                    break
    finally:
        src.close()
        if writer is not None:
            writer.release()
        cv2.destroyAllWindows()

    wall = time.perf_counter() - t_start
    print()
    print("=" * 66)
    print(f"实测（batch=1，n={n_done}，赛题 3.2.4.3 口径）")
    print("=" * 66)

    def line(name: str, arr):
        s = sorted(arr)
        print(f"  {name:<8s} P50 {pctl(s, .50):6.2f}  P95 {pctl(s, .95):6.2f}  "
              f"P99 {pctl(s, .99):6.2f}  max {s[-1]:6.2f} ms")

    line("预处理", pre_l)
    line("模型推理", inf_l)
    line("后处理", post_l)
    line("总感知", tot_l)
    print(f"  持续吞吐 {n_done / wall:6.2f} FPS（含取帧/显示开销）")
    if isinstance(be, DmlBackend):
        print("  注：DML 后端的 H2D 拷贝在 session.run 内部完成，"
              "因此这部分时间计入「模型推理」而非「预处理」。")

    # 慢帧定位：赛题明确考核 P99 与最大值，需要能解释离群点来自哪里
    order = sorted(range(len(tot_l)), key=lambda i: -tot_l[i])[:3]
    print()
    print("  最慢的 3 帧（帧号从 1 起，前 "
          f"{'20' if n_done > 20 else n_done} 帧内视为启动/自动调优开销）:")
    for i in order:
        tag = "启动开销" if i < 20 else "稳定期异常"
        print(f"    第 {i + 1:3d} 帧  总 {tot_l[i]:8.2f} ms  "
              f"(推理 {inf_l[i]:6.2f} / 后处理 {post_l[i]:7.2f})  {tag}")

    # 剔除前 20 帧后的稳态分位数，便于看清真实尾部延迟
    if n_done > 40:
        steady = tot_l[20:]
        s = sorted(steady)
        print()
        print(f"  稳态（剔除前 20 帧，n={len(steady)}）: "
              f"P50 {pctl(s, .50):.2f}  P95 {pctl(s, .95):.2f}  "
              f"P99 {pctl(s, .99):.2f}  max {s[-1]:.2f} ms")
    return 0


# ---------------------------------------------------------------- 一致性校验
def _iou_xyxy(a, b) -> float:
    """两个 xyxy 框的 IoU（纯 Python，框数很少，够用且不引额外依赖）"""
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    aa = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    bb = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    u = aa + bb - inter
    return inter / u if u > 0 else 0.0


def verify(n_img: int, imgsz: int, conf: float, iou: float) -> int:
    """
    单元级一致性校验：自实现后处理 vs ultralytics 官方 non_max_suppression，
    作用在同一份模型输出上，结果必须逐框一致。

    为什么不直接和 predict() 比：
      predict 会额外走一套 predictor（内部再做一次 fuse、letterbox、坐标反变换），
      差异来源混在一起，且在本机会挂住 GPU。这里把变量隔离掉，
      只验证"我改写的后处理没有改变结果"，这才是本次优化的风险点。
      预处理几何在下面单独用数学核对。
    """
    import torch
    from ultralytics.utils.nms import non_max_suppression

    dev = "cuda:0" if torch.cuda.is_available() else "cpu"
    half = dev != "cpu"
    net, names = load_model(dev, half)

    print("=" * 66, flush=True)
    print(f"一致性校验（后处理 vs 官方 NMS，同输入同输出）  "
          f"{n_img} 张  imgsz={imgsz}  {'FP16' if half else 'FP32'}", flush=True)
    print("=" * 66, flush=True)

    # --- 先核对预处理几何 ---
    print("  预处理几何核对（等比缩放 + 居中补边）:", flush=True)
    for (w, h) in [(640, 480), (1280, 720), (480, 480)]:
        sc = min(imgsz / w, imgsz / h)
        nw, nh = int(round(w * sc)), int(round(h * sc))
        pw, ph = (imgsz - nw) // 2, (imgsz - nh) // 2
        canvas = np.full((imgsz, imgsz, 3), 114, np.uint8)
        rgb, s2, p2w, p2h = letterbox_u8(np.zeros((h, w, 3), np.uint8), imgsz)
        ok = (abs(sc - s2) < 1e-9) and (pw == p2w) and (ph == p2h) and rgb.shape == canvas.shape
        print(f"    {w}x{h} -> scale={s2:.4f} pad=({p2w},{p2h})  {'OK' if ok else '不一致'}",
              flush=True)

    files = sorted(VAL_DIR.glob("*.jpg"))[:n_img]
    bad = 0
    print(flush=True)
    for f in files:
        im = cv2.imread(str(f))
        rgb, sc, pw, ph = letterbox_u8(im, imgsz)
        x = to_tensor(rgb, dev, half)
        with torch.no_grad():
            pred = net(x)
            if dev != "cpu":
                torch.cuda.synchronize()
        pred = pred[0] if isinstance(pred, (list, tuple)) else pred

        b1, s1, c1 = postprocess(pred, conf, iou, 100)
        b1 = unscale(b1, sc, pw, ph)

        o = non_max_suppression(pred, conf_thres=conf, iou_thres=iou, max_det=100)[0]
        b2 = unscale(o[:, :4].float().cpu(), sc, pw, ph) if len(o) else o[:, :4]
        c2 = o[:, 5].int().cpu().tolist() if len(o) else []

        b1l, c1l = b1.tolist(), c1.int().tolist()
        b2l = b2.tolist()

        ok, note = True, ""
        if len(b1l) != len(b2l):
            ok = False
            note = f"框数不同（官方 {len(b2l)}）"
        else:
            used, ious = set(), []
            for i, ba in enumerate(b1l):
                best_j, best = -1, 0.0
                for j, bb in enumerate(b2l):
                    if j in used:
                        continue
                    v = _iou_xyxy(ba, bb)
                    if v > best:
                        best, best_j = v, j
                if best_j < 0 or c1l[i] != c2[best_j]:
                    ok = False
                    note = "存在未能匹配的框"
                    break
                used.add(best_j)
                ious.append(best)
            if ok and ious:
                mean_iou = sum(ious) / len(ious)
                if min(ious) < 0.99:
                    ok = False
                    note = f"最低 IoU {min(ious):.4f}"
                else:
                    note = f"minIoU={min(ious):.4f}"

        if not ok:
            bad += 1
        print(f"  [{'OK  ' if ok else '差异'}] {f.name}  {len(b1l)} 框  {note}", flush=True)

    print(flush=True)
    if bad == 0:
        print(f"  [OK] {len(files)} 张全部逐框一致，后处理改写无误", flush=True)
        return 0
    print(f"  [X] {bad} 张不一致", flush=True)
    return 1


def dump_boxes(be, args, out: Path) -> int:
    """
    把当前后端在前 N 张验证图上的检测结果存成 npz，供 cmp_boxes.py 跨后端比对。

    为什么要分两步：DML 只在 ryzen-ai-1.6.0 环境，torch+ROCm 只在 yolo-rocm 环境，
    没有哪个环境同时具备两者。所以各自 dump，再离线比。
    """
    files = sorted(VAL_DIR.glob("*.jpg"))[:args.dump]
    if not files:
        print(f"[X] 验证集为空: {VAL_DIR}")
        return 1
    data, lines = {}, []
    print(f"dump {len(files)} 张  后端 {be.desc()}", flush=True)
    for i, f in enumerate(files):
        im = cv2.imread(str(f))
        if im is None:
            continue
        rgb, sc, pw, ph = letterbox_u8(im, args.imgsz)
        pred = be.infer(rgb)
        b, s, c = be.post(pred, args.conf, args.iou, args.max_det)
        b = unscale(b, sc, pw, ph)
        data[f"b{i}"] = np.asarray(b, np.float32).reshape(-1, 4)
        data[f"s{i}"] = np.asarray(s, np.float32).reshape(-1)
        data[f"c{i}"] = np.asarray(c, np.int64).reshape(-1)
        lines.append(f"{f.name}\t{len(b)}")
        print(f"  [{i + 1:3d}/{len(files)}] {f.name}  {len(b)} 框", flush=True)
    data["files"] = np.array([f.name for f in files])
    np.savez(out, **data)
    out.with_suffix(".txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] 已写入 {out}", flush=True)
    return 0


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="780M 实时检测（ROCm / DirectML 双后端）")
    ap.add_argument("--backend", choices=["torch", "dml"], default="torch",
                    help="torch=ROCm 栈（赛题指定）；dml=DirectML（快 10 倍，合规待确认）")
    ap.add_argument("--onnx", default=None,
                    help="dml 后端用的 ONNX，默认 export/yolo11n_18cls_<imgsz>.onnx")
    ap.add_argument("--dml-device", type=int, default=None,
                    help="DML 的 DXGI 适配器序号。默认自动找 AMD(0x1002)。"
                         "本机：0/2=780M，1=RTX 4060，3=WARP 软件渲染")
    ap.add_argument("--source", default="webcam",
                    help="webcam / udp / synthetic / 视频文件路径")
    ap.add_argument("--cam", type=int, default=0, help="摄像头索引")
    ap.add_argument("--port", type=int, default=UDP_PORT, help="UDP 监听端口")
    ap.add_argument("--trigger-ip", default=None,
                    help="ZYNQ 板卡 IP，给了就自动发 '1' 触发图传")
    ap.add_argument("--pkt-header", type=int, default=0,
                    help="UDP 每个报文的报头字节数，重组时跳过（默认 0）")
    ap.add_argument("--imgsz", type=int, default=480, help="推理尺寸，默认 480")
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--iou", type=float, default=0.45)
    ap.add_argument("--max-det", type=int, default=100)
    ap.add_argument("--half", action="store_true", default=True)
    ap.add_argument("--no-half", dest="half", action="store_false")
    ap.add_argument("--bench", type=int, default=0, help="只测速，跑够 N 帧就退出")
    ap.add_argument("--frames", type=int, default=0, help="跑够 N 帧就停（出演示视频用）")
    ap.add_argument("--save", default=None, help="把结果写成 mp4")
    ap.add_argument("--no-show", action="store_true", help="不弹窗")
    ap.add_argument("--dump", type=int, default=0,
                    help="把 N 张验证图的检测结果 dump 成 --dump-out 指定的 npz")
    ap.add_argument("--dump-out", default="export/boxes.npz")
    ap.add_argument("--verify", type=int, default=0, help="做 N 张图的一致性校验")

    # ---- 决策层（活体停车 + 蜂鸣器）----
    ap.add_argument("--decide", action="store_true",
                    help="启用活体判定：活体近则停车+鸣叫，障碍近则绕行")
    ap.add_argument("--serial", default="COM10",
                    help="下发命令的串口（PS UART0，115200）。注意不是 --port")
    ap.add_argument("--dry", action="store_true",
                    help="串口 dry-run，只打印命令不发（没插板子时用）")
    ap.add_argument("--motion", choices=["off", "flow", "static"], default="off",
                    help="运动辅助判据：off 纯检测 / flow 框内光流 / static 停车后差分")
    ap.add_argument("--stop-dist", type=float, default=1.0,
                    help="活体进这个距离（米）就停，默认 1.0")
    ap.add_argument("--dist-k", type=float, default=100.0,
                    help="单目测距常数 K=f*h(px·m)，默认 100（需按实车标定）")
    ap.add_argument("--dist-horizon", type=float, default=240.0,
                    help="画面里地平线的 y 像素，480 高时默认 240（相机水平）")
    args = ap.parse_args()

    if args.dump:
        return dump_boxes(build_backend(args), args, Path(args.dump_out))
    if args.verify:
        if args.backend != "torch":
            print("[!] --verify 是拿官方 non_max_suppression 当基准，只能在 torch 环境跑。\n"
                  "    DML 后端请用 --dump + cmp_boxes.py 与 ROCm 结果比对。")
            return 2
        return verify(args.verify, args.imgsz, args.conf, args.iou)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
