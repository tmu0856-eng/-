"""串行执行本地模型计算，限制索引与精排的显存峰值。"""
import threading

INFERENCE_LOCK = threading.RLock()
