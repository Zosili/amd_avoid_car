# -*- coding: utf-8 -*-
"""NPU 就绪验证脚本（重启后运行）
依次验证：1) quicktest 小模型上NPU  2) yolov8n INT8 上NPU + 测速
"""
import os
os.environ['RYZEN_AI_INSTALLATION_PATH'] = r'C:\Program Files\RyzenAI\1.6.0'
import numpy as np
import onnxruntime as ort

CACHE = os.path.expanduser('~/.ryzenai_cache')

def test(name, model, input_name, shape, cache_key):
    print(f"\n===== 测试 {name} =====")
    opts = [{'cache_dir': CACHE, 'cache_key': cache_key}]
    try:
        s = ort.InferenceSession(model, providers=['VitisAIExecutionProvider'],
                                 provider_options=opts)
    except Exception as e:
        print(f'❌ 会话创建失败: {str(e)[:200]}')
        return False
    x = np.random.rand(*shape).astype(np.float32)
    try:
        out = s.run(None, {input_name: x})   # 预热
        t0 = time.time()
        n = 5
        for _ in range(n):
            out = s.run(None, {input_name: x})
        dt = (time.time() - t0) / n
        print(f'✅ NPU推理成功！平均 {dt*1000:.1f}ms/帧, 输出 {out[0].shape}')
        return True
    except Exception as e:
        print(f'❌ 推理失败: {str(e)[:200]}')
        return False

import time
ok1 = test('quicktest小模型', r'C:\Program Files\RyzenAI\1.6.0\quicktest\test_model.onnx',
           'input', (1,3,32,32), 'qt_check')
ok2 = test('YOLOv8n INT8', r'D:\FPGA\42_ov5640_udp_pc\yolov8n_int8.onnx',
           'images', (1,3,640,640), 'yolov8n_final')

print('\n========== 总结 ==========')
print(f'quicktest: {"✅ NPU可用" if ok1 else "❌ NPU不可用"}')
print(f'YOLOv8n INT8: {"✅ 上NPU成功" if ok2 else "❌ 上NPU失败"}')
