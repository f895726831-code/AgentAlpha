"""
GFlowNet v4 Dual-Channel Sampler — 双通道采样打破探索-利用冲突。

v4 核心创新: 每个 batch 分为两个通道:
  - Exploitation (60%): 低温采样 (t=0.5) → 强化已知高 IC 区域
  - Exploration (40%):  高温采样 (t=1.5) → 搜索未被覆盖的结构空间

配合 L3 结构性新颖度奖励，低温通道深挖已有结构，高温通道开拓新结构。
Archive 通过 L3 reward 间接影响探索方向——低覆盖类别获得更高新颖度 bonus。

用法:
    from .sampler import DualChannelSampler

    sampler = DualChannelSampler(config)
    tids, aids, lpfs, sds, formulas = sampler.sample_batch(model, batch_size)
"""

from typing import List, Tuple, Optional


class DualChannelSampler:
    """
    双通道采样器。

    将每个 batch 划分为两个子 batch:
      - Exploitation channel: exploit_ratio × batch_size 条轨迹
        使用低温度 (exploit_temp) 进行 sharpened 采样。
      - Exploration channel: 剩余轨迹
        使用高温度 (explore_temp) 进行 flattened 采样。

    参数:
        exploit_ratio: float = 0.6  — exploitation 通道占比
        exploit_temp:  float = 0.5  — exploitation 温度 (sharpen)
        explore_temp:  float = 1.5  — exploration 温度 (flatten)
    """

    def __init__(
        self,
        exploit_ratio: float = 0.6,
        exploit_temp: float = 0.5,
        explore_temp: float = 1.5,
    ):
        self.exploit_ratio = exploit_ratio
        self.exploit_temp = exploit_temp
        self.explore_temp = explore_temp

        # 统计
        self.total_exploit = 0
        self.total_explore = 0

    def sample_batch(
        self,
        model,
        batch_size: int,
    ) -> Tuple[
        List[List[int]],   # token_ids_batch
        List[List[int]],   # action_indices_batch
        List[List[float]], # log_pfs_batch
        List[List[int]],   # stack_depths_batch
        List[Optional[str]],  # formulas
    ]:
        """
        双通道 batch 采样。

        参数:
            model:      GFlowNetCore 模型实例
            batch_size: 总 batch 大小

        返回:
            与 GFlowNetCore.sample_batch() 相同的 5-tuple
        """
        n_exploit = int(batch_size * self.exploit_ratio)
        # 确保至少 1 条 exploration 轨迹（除非 batch 太小）
        n_explore = max(0, batch_size - n_exploit)
        if n_explore == 0 and batch_size > 1:
            n_exploit = batch_size - 1
            n_explore = 1

        # ── Exploitation channel: 低温 sharpened 采样 ──
        if n_exploit > 0:
            tids_e, aids_e, lpfs_e, sds_e, formulas_e = model.sample_batch(
                n_exploit, temperature=self.exploit_temp
            )
            self.total_exploit += n_exploit
        else:
            tids_e, aids_e, lpfs_e, sds_e, formulas_e = [], [], [], [], []

        # ── Exploration channel: 高温 flattened 采样 ──
        if n_explore > 0:
            tids_x, aids_x, lpfs_x, sds_x, formulas_x = model.sample_batch(
                n_explore, temperature=self.explore_temp
            )
            self.total_explore += n_explore
        else:
            tids_x, aids_x, lpfs_x, sds_x, formulas_x = [], [], [], [], []

        # ── 合并 ──
        return (
            tids_e + tids_x,
            aids_e + aids_x,
            lpfs_e + lpfs_x,
            sds_e + sds_x,
            formulas_e + formulas_x,
        )

    def get_stats(self) -> dict:
        """返回采样统计"""
        total = self.total_exploit + self.total_explore
        if total == 0:
            return {"total": 0, "exploit_pct": 0, "explore_pct": 0}
        return {
            "total": total,
            "exploit": self.total_exploit,
            "explore": self.total_explore,
            "exploit_pct": round(self.total_exploit / total * 100, 1),
            "explore_pct": round(self.total_explore / total * 100, 1),
        }