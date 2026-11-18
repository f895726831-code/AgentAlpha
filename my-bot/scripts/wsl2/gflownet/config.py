"""
GFlowNet 超参数配置

集中管理所有超参数，通过 dataclass 提供类型安全。
"""

from dataclasses import dataclass, field


@dataclass
class GFNConfig:
    """GFlowNet 训练与模型超参数"""

    # ─── 模型架构 ───
    d_model: int = 64             # Token 嵌入维度
    n_layers: int = 2              # Transformer 编码器层数
    n_heads: int = 4               # 多头注意力头数
    d_ff: int = 128                # FFN 隐藏维度
    dropout: float = 0.1           # Dropout 概率
    stack_depth_embed_dim: int = 8 # 栈深度嵌入维度

    # ─── 表达式约束 ───
    max_expr_len: int = 25         # 最大 token 序列长度
    min_expr_len: int = 2          # 最小 token 序列长度（至少 1 字段 + 1 操作符）
    max_arity_depth: int = 10      # 最大嵌套深度
    allow_comparison_ops: bool = False  # v2.3: 是否允许比较操作符 (> < >= <= == !=)

    # ─── 训练 ───
    batch_size: int = 16           # 每 micro-batch 采样轨迹数
    grad_accum_steps: int = 4      # 梯度累积步数 (effective batch = batch_size × grad_accum_steps)
    n_episodes: int = 200          # 训练 episode 数
    lr: float = 1e-3               # 学习率（策略网络）
    lr_logZ: float = 2e-3          # logZ 学习率（通常更大）
    lr_flow: float = 2e-3          # 状态流预测器学习率
    weight_decay: float = 1e-4     # 权重衰减
    grad_clip: float = 1.0         # 梯度裁剪
    warmup_episodes: int = 50      # 学习率预热 episode 数
    subtb_lambda: float = 0.9      # SubTB λ (0=full trajectory only, 1=all prefixes equal)

    # ─── 采样温度 ───
    train_temperature: float = 1.0  # 训练时采样温度
    sample_temperature: float = 1.5 # 采样时温度（稍高 → 更多样）

    # ─── 奖励 ───
    reward_mode: str = "v4"                 # 奖励模式: "v3" (IC+diversity), "v4" (L1+L2+L3)
    reward_ic_weight: float = 0.6           # IC 评分权重（主导信号）
    reward_diversity_weight: float = 0.3    # 多样性加分权重（辅助探索）
    reward_validity_weight: float = 0.1     # 有效性惩罚权重
    reward_epsilon: float = 1e-6        # 最小奖励（数值稳定）

    # ─── v4 奖励引擎 ───
    reward_v4_l1_min_ic: float = 0.015      # L1 Quality Gate: 最低 |IC| 阈值
    reward_v4_l1_min_coverage: float = 0.80 # L1 Quality Gate: 最低数据覆盖率
    reward_v4_l2_use_icir: bool = True      # L2 Alpha Score: 是否用 ICIR 替代 IC
    reward_v4_l2_stability_weight: float = 0.3  # L2: 年度 IC 稳定性权重
    reward_v4_l2_bullbear_weight: float = 0.2   # L2: 牛熊一致性权重
    reward_v4_l3_novelty_alpha: float = 0.5     # L3: R = alpha_score * (1 + α * incremental_value)
    reward_v4_l3_max_corr_weight: float = 0.5   # L3: 最大相关性惩罚权重
    reward_v4_l3_structure_weight: float = 0.5  # L3: 结构新颖度权重

    # ─── MAP-Elites Archive ───
    archive_enabled: bool = True            # 是否启用 MAP-Elites Archive
    archive_top_k: int = 5                  # 每个类别保留的 Top-K 因子数
    archive_min_ic: float = 0.015           # Archive 入池最低 |IC|

    # ─── v4 双通道采样 ───
    sampler_mode: str = "standard"           # 采样模式: "standard" | "dual_channel"
    sampler_exploit_ratio: float = 0.6       # DualChannel: exploitation 占比 (0.0-1.0)
    sampler_exploit_temp: float = 0.5        # DualChannel: exploitation 温度 (低温=sharp)
    sampler_explore_temp: float = 1.5        # DualChannel: exploration 温度 (高温=flat)

    # ─── 经验回放 ───
    buffer_capacity: int = 500      # 缓冲区容量

    # ─── FFO 评估 ───
    eval_start: str = "2015-01-05"  # 评估起始（CSI500启动日，~10年窗口）
    eval_end: str = "2024-12-31"    # 评估结束（留 2025-2026 为 OOS）
    eval_universe: str = "csi500"   # 股票池

    # ─── 日志与检查点 ───
    log_every: int = 10             # 每 N 个 episode 输出日志
    save_every: int = 100           # 每 N 个 episode 保存检查点
    checkpoint_dir: str = "checkpoints"

    # ─── 设备 ───
    device: str = "cpu"

    def to_dict(self) -> dict:
        """导出为字典（用于保存）"""
        return {k: v for k, v in self.__dict__.items()}


# 默认实例
default_config = GFNConfig()