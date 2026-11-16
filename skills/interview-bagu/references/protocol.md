# 本地记录协议

运行环境为 Python 3.10 或以上，脚本仅使用标准库。下文将 skill 内的 scripts/progress.py 简写为 progress.py，执行时使用实际脚本路径。命令在学习工作目录运行。

## 准备题目

公开包不含题目或答案。使用时将用户指定或当场生成的题目保存为本地 JSON 数组，每个元素严格包含下列字段。

| 字段 | 要求 |
| --- | --- |
| id | 1–128 位 ASCII 字母、数字、点、下划线或连字符，首位是字母或数字 |
| track | java、agent 或 extra |
| topic | 非空主题 |
| priority | P0、P1 或 P2，P0 优先 |
| prompt | 非空题面 |

这里的 java 轨道可包括 JVM、Spring、数据库和缓存，agent 轨道可包括工具调用、编排、RAG 和评测，extra 用于用户选择的扩展范围。初始化后的题目保存在状态文件中，源题目文件可保留为用户的本地资料。

题目文件、评分 payload 和状态文件一律放在 .interview-bagu/ 内。开始前确认学习工作目录的 Git 忽略规则覆盖该目录。若没有覆盖，先在该仓库的 .git/info/exclude 添加 /.interview-bagu/。不能把 .gitignore 当成公开已跟踪资料的脱敏措施。

## 命令

~~~text
python progress.py init --questions .interview-bagu/questions.json
python progress.py status
python progress.py ask --expect-revision 0
~~~

init 拒绝覆盖已有文件。默认状态路径为 .interview-bagu/progress.json，可在子命令前用 --state 指定另一个本地文件。

init 可设置 --pass-score、--required-streak、--review-days、--track 和 --priority。默认分别为 8、5、3、mixed 和 P0。review_days 支持 1–36500 的整数天。track 可选 mixed、java、agent、extra、all，mixed 包含 java 和 agent，all 包含全部轨道。priority 表示允许的最低优先级，P1 同时包含 P0 和 P1。

~~~text
python progress.py ask --expect-revision REV --track java --priority P1
python progress.py ask --expect-revision REV --question QUESTION_ID
python progress.py hint --expect-revision REV
python progress.py record --expect-revision REV --payload .interview-bagu/attempt.json
python progress.py skip --expect-revision REV --question QUESTION_ID --attempt-id ATTEMPT_ID
python progress.py add --expect-revision REV --questions .interview-bagu/more-questions.json
~~~

REV、QUESTION_ID 和 ATTEMPT_ID 需要替换为当前值。attempt_id 为一次真实答题或跳过生成的稳定唯一标识，可用 UUID。重复相同 ID 和相同内容会返回 changed=false，不会重复记分。相同 ID 的不同内容会报错。

ask 的轨道和优先级选择会保存，后续沿用。有未回答题时，普通 ask 返回当前题，切换范围应先完成或显式跳过当前题。提供同一个问题 ID 也可恢复该题。已掌握题在到期前不自动出现，skip 会将该题延后 review_days 天。全部候选都已掌握或被延后时，ask 返回 current=null，读取 stats 中的 next_review_at，告知下次复习时间。用户也可指定某题提前复习。

status 返回 current、next、stats 和 revision。current 表示用户正在回答的题，next 只表示结束当前题之后的候选。用户暂停时不需要清除 current。

## 评分 payload

JSON 对象严格包含下列字段，使用 UTF-8 保存，也支持带 BOM 的 UTF-8。--payload - 可从标准输入读取，init/add 的 --questions - 同样支持标准输入。

| 字段 | 要求 |
| --- | --- |
| attempt_id | 稳定唯一 ID，规则与题目 ID 相同 |
| question_id | 与 current.question_id 相同 |
| score | 0–10 的有限数值，支持小数 |
| answer | 用户的实际回答，非空字符串 |
| feedback | 得分理由和具体遗漏，非空字符串 |

不提供独立作答标记字段。脚本根据当前题的 hinted 状态确定 independent。hint 状态在该题本次提问中只能从 false 变为 true。skip 通过专用命令记录，分数为空，不改变连续次数。提示后低于通过阈值也会清零连续次数。

history 保存各次实际结果，统计从 history 计算，避免两份成绩漂移。复习时间为最近一次已评分或跳过事件之后的 review_days 天。默认以 UTC 带时区时间保存，向用户展示时可转换到其已知本地时区。脚本不会在后台创建提醒。

## 写入与恢复

读取 status 得到最新 revision 后，再发起一次写入。脚本在写锁内重读状态、检查 revision，通过同目录临时文件与原子替换保存。record/skip 的相同 attempt_id 重试会先识别已有结果，容许该次原始 revision 已经过期。

发生 revision 冲突时，重新读取 current、history 和 revision，确认本次回答是否已被保存。不要自动覆盖并发会话的结果。系统时间早于提问时间时暂停记分并检查时间。

锁冲突会明确报错。若进程异常退出后锁仍存在，先读取 status，检查锁中的进程号和对应运行实例，确认写入进程已停止后才清理该状态文件旁的 .lock。脚本不自动删除其他写入者的锁。

本格式标识为 interview-bagu/v1。其他 schema 会被拒绝，原文件保持原样。需要备份或迁移时将文件留在用户选择的本地私有位置。
