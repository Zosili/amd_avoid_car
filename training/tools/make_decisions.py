# -*- coding: utf-8 -*-
"""根据人工复核结果生成 decisions.json。

规则：
  REVIEW 层（R*）：默认 drop，只有列在 KEEP/UNSURE 里的才保留/存疑
  AUTO   层（A*）：默认 keep（多模型共识），列在 DROP/UNSURE 里的剔除/存疑
  UNSURE -> "skip"，apply 时整张图排除（既不标错，也不当负样本）
"""

import json
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
REV = PROJECT / "runs/review"

# ---- REVIEW 层：真人 ----
R_KEEP = """
R3 R11 R15 R19 R24 R26 R28 R31 R32 R35 R36 R38 R52 R56 R68 R71 R75 R93 R96 R98 R99
R100 R103 R107 R108 R109 R110 R112 R117 R121 R126 R133 R138 R147 R148 R154 R155 R156 R157
R166 R167 R168 R171 R180 R181 R185 R187 R200 R201 R202 R204 R206 R214 R217 R220 R225 R226
R233 R236 R240 R244 R245 R247 R248 R253 R254 R255 R259 R263 R278 R303 R305 R308 R312 R319
R322 R325 R327 R328 R331 R334 R337 R339 R340 R346 R348 R349 R361 R365 R381 R393
""".split()

# ---- REVIEW 层：拿不准（整图排除）----
R_UNSURE = """
R9 R13 R18 R83 R118 R123 R127 R159 R170 R173 R179 R183 R232 R237 R250 R264 R265 R266 R274
R311 R315 R316 R321 R329 R332 R344 R351 R371 R380 R401
""".split()

# ---- AUTO 层：误检 ----
A_DROP = """
A72 A73 A80 A129 A130 A132 A133 A136 A141 A143 A145 A147 A163 A167 A169 A179 A181 A187 A194
A205 A207 A210 A211 A219 A223 A224 A228 A231 A233 A236 A237 A239 A246 A255 A260 A261 A264
A268 A271 A275 A276 A277 A283 A285 A288 A295 A298 A299 A300 A302 A305 A314 A320 A321
""".split()

# ---- AUTO 层：拿不准 ----
A_UNSURE = """
A69 A139 A144 A146 A155 A159 A160 A183 A190 A191 A203 A204 A214 A215 A220 A225 A229 A235
A238 A242 A247 A248 A249 A254 A265 A267 A269 A272 A273 A274 A282 A284 A286 A292 A296 A307
A310 A313 A316 A317 A319 A322
""".split()

rev_idx = json.loads((REV / "REVIEW_index.json").read_text(encoding="utf-8"))
auto_idx = json.loads((REV / "AUTO_index.json").read_text(encoding="utf-8"))

dec: dict[str, str] = {}
for k in rev_idx:
    dec[k] = "keep" if k in set(R_KEEP) else ("skip" if k in set(R_UNSURE) else "drop")
for k in auto_idx:
    dec[k] = "drop" if k in set(A_DROP) else ("skip" if k in set(A_UNSURE) else "keep")

# 校验：列出的 id 必须真实存在
missing = [x for x in R_KEEP + R_UNSURE if x not in rev_idx]
missing += [x for x in A_DROP + A_UNSURE if x not in auto_idx]
if missing:
    print("!! 这些 id 不在 index 里:", missing)

out = PROJECT / "runs/decisions.json"
out.write_text(json.dumps(dec, ensure_ascii=False, indent=0), encoding="utf-8")

from collections import Counter
c = Counter(dec.values())
print("决策条目", len(dec), dict(c))
print("REVIEW:", len(rev_idx), "簇 | AUTO:", len(auto_idx), "簇")
print("->", out)
