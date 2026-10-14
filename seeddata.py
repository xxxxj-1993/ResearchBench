# -*- coding: utf-8 -*-
"""科研工作台 —— 内容种子数据（干净分享版）

公开仓库中不含任何个人信息。下面每个键都保持为空列表：
首次启动得到的是一个空白工作台，所有内容由使用者自己填写。

如果你想把已有的成果一次性预置进去（只在自己电脑上用，不要提交），
按下面的字段结构往对应列表里加 dict 即可，字段名与界面一致：

  PUBS            已发表论文    id / year / role / journal / myRole / title /
                               authors / volume / pages / doi / if_ / zone /
                               cites / notes
  GRANTS          已立项项目    id / org / kind / code / name / period /
                               amount / status / role / notes
  TEACHING        教学成果      id / year / kind / name / level / role / notes
  TEACH_PROJECTS  教改与其他    id / year / name / kind / role / status / notes
  PATENTS         专利          id / name / code / role / status / notes
  PLANS           工作计划      id / title / kind / deadline / status / notes
  IDEAS           灵感          id / title / content / tags / status / source
  MATERIALS       我的材料      id / name / kind / path / note

改完重新打包：pyinstaller --noconfirm 科研工作台.spec
"""

PUBS = []
GRANTS = []
TEACHING = []
TEACH_PROJECTS = []
PATENTS = []
PLANS = []
IDEAS = []
MATERIALS = []
