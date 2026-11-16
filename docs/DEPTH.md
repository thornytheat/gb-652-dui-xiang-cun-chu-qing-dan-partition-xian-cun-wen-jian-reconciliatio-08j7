# 深度登记 对象存储清单与分区现存文件对账

        - 规模：中型
        - 核心规则：路径规范化后按 key、size 与摘要逐项比对，目录标记、空分区和缺失对象不得混为一类。
        - 使用者与工作流：导入随仓样例，逐模块复核，最后导出单一证据清单。

        ## 功能模块

        | 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
        |---|---|---|---|---|
        | M1 清单采信 | 运行样例并复核证据 | `/api/manifests/import` / `/manifest` | `app/modules/m1_manifest/core.py` `app/modules/m1_manifest/service.py` | `tests/test_m1_manifest.py` |
| M2 路径契约 | 运行样例并复核证据 | `/api/paths/normalize` / `/paths` | `app/modules/m2_paths/core.py` `app/modules/m2_paths/service.py` | `tests/test_m2_paths.py` |
| M3 对象核验 | 运行样例并复核证据 | `/api/objects/verify` / `/objects` | `app/modules/m3_objects/core.py` `app/modules/m3_objects/service.py` | `tests/test_m3_objects.py` |
| M4 分区判定 | 运行样例并复核证据 | `/api/partitions` / `/partitions` | `app/modules/m4_partitions/core.py` `app/modules/m4_partitions/service.py` | `tests/test_m4_partitions.py` |
| M5 差异对账 | 运行样例并复核证据 | `/api/reconcile` / `/reconcile` | `app/modules/m5_reconcile/core.py` `app/modules/m5_reconcile/service.py` | `tests/test_m5_reconcile.py` |
| M6 批次导出 | 运行样例并复核证据 | `/api/export/differences.csv` / `/runs` | `app/modules/m6_runs/core.py` `app/modules/m6_runs/service.py` | `tests/test_m6_runs.py` |
| M7 分区授权交付 | 授权外发、收回、内部审计 | `/api/delivery/batches` / `/delivery` | `app/modules/m7_delivery/core.py` `app/modules/m7_delivery/service.py` | `tests/test_m7_core.py` `tests/test_m7_service.py` `tests/test_m7_api.py` |

        ## 陷阱登记

        | TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
        |---|---|---|---|---|---|
        | TRAP-02 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m1_manifest/core.py` | `tests/test_m1_manifest.py::test_trap_1` |
| TRAP-05 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m2_paths/core.py` | `tests/test_m2_paths.py::test_trap_1` |
| TRAP-08 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m3_objects/core.py` | `tests/test_m3_objects.py::test_trap_1` |
| TRAP-09 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m4_partitions/core.py` | `tests/test_m4_partitions.py::test_trap_1` |
| TRAP-14 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m5_reconcile/core.py` | `tests/test_m5_reconcile.py::test_trap_1` |
| TRAP-16 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m6_runs/core.py` | `tests/test_m6_runs.py::test_trap_1` |
| TRAP-18 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m1_manifest/core.py` | `tests/test_m1_manifest.py::test_trap_2` |
| TRAP-21 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m2_paths/core.py` | `tests/test_m2_paths.py::test_trap_2` |
| TRAP-22 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m3_objects/core.py` | `tests/test_m3_objects.py::test_trap_2` |
| TRAP-23 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m4_partitions/core.py` | `tests/test_m4_partitions.py::test_trap_2` |
| TRAP-24 | 整批外发泄露别家对象键 | 直接导出完整差异 | 按授权分区裁剪，只发本分区 MISSING 行，越权 403 | `app/modules/m7_delivery/core.py` `app/modules/m7_delivery/service.py` | `tests/test_m7_service.py::test_authorized_download_is_trimmed_to_partition_missing` |
| TRAP-25 | 留痕失败仍返回原件 | 先查证据后补日志 | 审计先落库留痕，事务失败则不返回完整证据 | `app/modules/m7_delivery/service.py` | `tests/test_m7_service.py::test_audit_failure_returns_no_evidence` |
| TRAP-26 | 收回后旧下载地址仍可用 | 凭据签发后不再校验 | 回读在同一立即事务复核授权状态，REVOKED 即 403 | `app/store.py` | `tests/test_m7_service.py::test_revocation_invalidates_old_download_url` |
| TRAP-27 | 重试重复登记访问 | 每次请求新增行 | 以 request_token 唯一约束幂等，回放原凭据 | `app/store.py` | `tests/test_m7_service.py::test_retry_same_token_does_not_duplicate_access_or_ticket` |
| TRAP-28 | 收回与读取并发竞争 | 无锁读到旧授权 | BEGIN IMMEDIATE 串行化，等锁后按最新状态裁决，收回为准 | `app/store.py` | `tests/test_m7_service.py::test_revocation_and_read_concurrent_revoke_wins` |

        ## 对账规则

        - 输入记录数 = 有效记录数 + 隔离记录数 + 空记录数。
        - 核心数值或事件数在转换、判定和导出之间保持守恒。
        - 同一输入摘要重跑复用原运行编号，不增加结果行。

        ## M7 授权交付规则

        - 对账批次由实际登记（manifest）与现存对象（objects）形成，摘要沿用 `canonical_digest`，重跑复用同一批次。
        - 授权粒度为（批次, 供应商, 分区）；外发内容仅含该分区 `MISSING` 对象键，冲突与孤儿只进内部完整证据。
        - 内部审计必须提供理由，先写 `audit_logs` 成功才返回完整差异；同一 request_token 重试不重复留痕。
        - 收回授权后该授权下全部旧下载地址立即失效；收回与读取并发时以收回为准。
        - 全部状态落 SQLite，进程中断重启后批次、授权、凭据与留痕均可恢复；不新增补数领取能力。

        ## 已知不覆盖

        - 生产系统连接、身份认证和外部密钥不在演示范围内。
