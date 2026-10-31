# 让 tests 与脚本都能用绝对导入（项目根进 sys.path）
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
