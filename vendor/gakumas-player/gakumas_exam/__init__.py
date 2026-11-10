"""高保真离线考试引擎：按学マス客户端 3.5.1 的考试模块（Campus.InGame.Exam）结构移植。

- 方法名与客户端保持一致（TryAddReviewStatus、CalculateAddingParameter …），方便与客户端逻辑 逐行对照；
- 局面格式即客户端存档 ExamSaveData（Unity SerializeReference），可从任意实机存档起步、与之后的存档整体比对；
- 只用 Python 标准库；主数据走 gakumas_exam.catalog（gakumasu-diff 固定快照）。
"""
